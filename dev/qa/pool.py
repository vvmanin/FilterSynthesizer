# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""The harness's own worker pool (stdlib only).

concurrent.futures cannot kill one task, and a 2-hour unattended run must not
stall on a single hung solve. Here every worker is a spawned process with its
own task queue, so the coordinator knows which process runs which task and can
kill + respawn it past the task's timeout.

* tasks carry a `kind`; `limits` caps how many of a kind run at once (LTspice);
* a `priority` (lower first) orders the queue; after the `deadline` only tasks
  marked `essential` are still started, the rest come back as "skipped";
* a worker is recycled after `recycle_after` tasks (AppTest sessions leave
  Streamlit caches behind) or above `max_rss_mb` when psutil is available;
* each worker's stdout/stderr go to logs/worker_<i>.log (fd level, so C-level
  and child-process output too).
"""
import heapq
import importlib
import itertools
import multiprocessing as mp
import os
import queue
import sys
import time
import traceback


def _rss_mb():
    try:
        import psutil
        return psutil.Process().memory_info().rss / 2 ** 20
    except Exception:                                     # noqa: BLE001
        return None


def _worker_main(wid, in_q, out_q, log_path, work_dir, paths, env):
    os.environ.update(env)
    f = open(log_path, "a", buffering=1, encoding="utf-8", errors="replace")
    try:
        os.dup2(f.fileno(), 1)
        os.dup2(f.fileno(), 2)
    except OSError:
        pass
    sys.stdout = sys.stderr = f
    for p in reversed(paths):
        if p not in sys.path:
            sys.path.insert(0, p)
    os.makedirs(work_dir, exist_ok=True)
    os.chdir(work_dir)
    out_q.put(("ready", wid, None))
    while True:
        msg = in_q.get()
        if msg is None:
            break
        tid, fn_name, args, kwargs = msg
        print(f"\n===== task {tid} {fn_name} {time.strftime('%H:%M:%S')}", flush=True)
        t0, c0 = time.perf_counter(), time.process_time()
        try:
            mod, func = fn_name.split(":")
            fn = getattr(importlib.import_module(mod), func)
            value = fn(*args, **kwargs)
            res = dict(status="ok", value=value)
        except BaseException as e:                        # noqa: BLE001
            res = dict(status="error", error=f"{type(e).__name__}: {e}"[:2000],
                       traceback=traceback.format_exc()[-6000:])
            if isinstance(e, KeyboardInterrupt):
                break
        res.update(wall=time.perf_counter() - t0, cpu=time.process_time() - c0, rss_mb=_rss_mb())
        try:
            out_q.put(("done", wid, (tid, res)))
        except Exception as e:                            # noqa: BLE001  (unpicklable value)
            out_q.put(("done", wid, (tid, dict(status="error", error=f"result not picklable: {e}",
                                               wall=res["wall"], cpu=res["cpu"]))))


class Task:
    _seq = itertools.count()

    def __init__(self, fn, args=(), kwargs=None, kind="misc", timeout=600, priority=50,
                 essential=False, meta=None, on_done=None, label=None):
        self.id = next(Task._seq)
        self.fn, self.args, self.kwargs = fn, tuple(args), dict(kwargs or {})
        self.kind, self.timeout, self.priority = kind, timeout, priority
        self.essential, self.meta, self.on_done = essential, meta or {}, on_done
        self.label = label or fn


class Pool:
    def __init__(self, n_workers, work_dir, log_dir, paths=(), env=None, limits=None,
                 recycle_after=60, max_rss_mb=None):
        self.n = max(1, int(n_workers))
        self.work_dir, self.log_dir = work_dir, log_dir
        self.paths, self.env = list(paths), dict(env or {})
        self.limits = dict(limits or {})
        self.recycle_after, self.max_rss_mb = recycle_after, max_rss_mb
        self.ctx = mp.get_context("spawn")
        self.out_q = self.ctx.Queue()
        self.workers = {}                    # wid -> dict(proc, in_q, task, start, ntasks, ready)
        self.pending = []                    # heap (priority, seq, task)
        self.tasks = {}
        self.running_kind = {}
        self.deadline = None
        self.done_count = 0
        self.stats = {"ok": 0, "error": 0, "timeout": 0, "crash": 0, "skipped": 0}
        self._wid = itertools.count()
        os.makedirs(log_dir, exist_ok=True)
        self.busy_seconds = 0.0
        self.t_start = time.perf_counter()

    # -- workers ------------------------------------------------------------
    def _spawn(self):
        wid = next(self._wid)
        in_q = self.ctx.Queue()
        slot = wid % self.n
        log = os.path.join(self.log_dir, f"worker_{slot:02d}.log")
        p = self.ctx.Process(target=_worker_main, daemon=True,
                             args=(wid, in_q, self.out_q, log, self.work_dir, self.paths, self.env))
        p.start()
        self.workers[wid] = dict(proc=p, in_q=in_q, task=None, start=None, ntasks=0, ready=False)

    def _retire(self, wid, kill=False):
        w = self.workers.pop(wid)
        if kill:
            try:
                w["proc"].kill()
            except Exception:                             # noqa: BLE001
                pass
        else:
            try:
                w["in_q"].put(None)
            except Exception:                             # noqa: BLE001
                pass
        w["proc"].join(timeout=0 if kill else 5)

    def start(self):
        for _ in range(self.n):
            self._spawn()

    def close(self):
        for wid in list(self.workers):
            self._retire(wid, kill=True)

    # -- tasks --------------------------------------------------------------
    def submit(self, task):
        self.tasks[task.id] = task
        heapq.heappush(self.pending, (task.priority, task.id, task))
        return task

    def _finish(self, task, res):
        self.done_count += 1
        self.stats[res["status"]] = self.stats.get(res["status"], 0) + 1
        self.tasks.pop(task.id, None)
        if task.on_done:
            try:
                task.on_done(task, res)
            except Exception:                             # noqa: BLE001
                traceback.print_exc()

    def _next_task(self):
        skipped = []
        found = None
        now = time.time()
        while self.pending:
            pr, seq, t = heapq.heappop(self.pending)
            if self.deadline and now > self.deadline and not t.essential:
                self._finish(t, dict(status="skipped", error="time budget reached", wall=0, cpu=0))
                continue
            lim = self.limits.get(t.kind)
            if lim is not None and self.running_kind.get(t.kind, 0) >= lim:
                skipped.append((pr, seq, t))
                continue
            found = t
            break
        for item in skipped:
            heapq.heappush(self.pending, item)
        return found

    def _dispatch(self):
        for wid, w in list(self.workers.items()):
            if w["task"] is not None or not w["ready"]:
                continue
            t = self._next_task()
            if t is None:
                return
            w["task"], w["start"] = t, time.perf_counter()
            self.running_kind[t.kind] = self.running_kind.get(t.kind, 0) + 1
            w["in_q"].put((t.id, t.fn, t.args, t.kwargs))

    def _release(self, wid, task, res):
        w = self.workers.get(wid)
        self.running_kind[task.kind] = self.running_kind.get(task.kind, 1) - 1
        if w is not None:
            self.busy_seconds += time.perf_counter() - (w["start"] or time.perf_counter())
            w["task"], w["start"] = None, None
            w["ntasks"] += 1
            rss = res.get("rss_mb")
            if (w["ntasks"] >= self.recycle_after
                    or (self.max_rss_mb and rss and rss > self.max_rss_mb)):
                self._retire(wid)
                self._spawn()
        self._finish(task, res)

    def running(self):
        return [(w["task"], time.perf_counter() - w["start"]) for w in self.workers.values()
                if w["task"] is not None]

    def idle(self):
        return not self.pending and all(w["task"] is None for w in self.workers.values())

    def run(self, progress=None, every=5.0):
        """Run until queue and workers are empty. `progress(pool)` every `every` s."""
        last = 0.0
        while True:
            self._dispatch()
            if self.idle():
                break
            try:
                kind, wid, payload = self.out_q.get(timeout=0.25)
                if kind == "ready":
                    if wid in self.workers:
                        self.workers[wid]["ready"] = True
                elif kind == "done":
                    tid, res = payload
                    w = self.workers.get(wid)
                    task = w["task"] if w and w["task"] and w["task"].id == tid else self.tasks.get(tid)
                    if task is not None:
                        self._release(wid, task, res)
            except queue.Empty:
                pass
            now = time.perf_counter()
            for wid, w in list(self.workers.items()):
                t = w["task"]
                if t is not None and now - w["start"] > t.timeout:
                    res = dict(status="timeout", error=f"timeout after {t.timeout:.0f} s",
                               wall=now - w["start"], cpu=None)
                    self._retire(wid, kill=True)
                    self._spawn()
                    self.running_kind[t.kind] = self.running_kind.get(t.kind, 1) - 1
                    self._finish(t, res)
                elif not w["proc"].is_alive():
                    self._retire(wid, kill=True)
                    self._spawn()
                    if t is not None:
                        self.running_kind[t.kind] = self.running_kind.get(t.kind, 1) - 1
                        self._finish(t, dict(status="crash", error="worker process died",
                                             wall=now - (w["start"] or now), cpu=None))
            if progress and now - last >= every:
                last = now
                progress(self)

    def utilisation(self):
        el = time.perf_counter() - self.t_start
        return self.busy_seconds / (el * self.n) if el > 0 else 0.0
