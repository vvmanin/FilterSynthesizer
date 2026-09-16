# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  cells_am_core.py
#  Ackerberg-Mossberg (AM) three-op-amp biquad -- SHARED CORE for the four
#  AM family modules (cells_am / cells_am_hp / cells_am_bp / cells_am_notch).
#
#  One physical core, many cells: the AM loop is a Miller integrator (U1)
#  closed through an ACTIVELY COMPENSATED non-inverting integrator (U2 + the
#  U3 unity inverter). Every cell shares the same denominator
#
#     D(s) = s^2 + s/(R4 C2) + (R8/R7)/(R5 R6 C2 C3)
#
#  and differs only in which INPUT BRANCH is populated and which op-amp
#  output is TAPPED (see AM_IDEAL_TF_ANALYSIS.md for the full derivation,
#  CAS-verified):
#
#     kind   input branch(es)      tap    response
#     ----   ------------------    ----   -----------------------------
#     LP     R2  (a->p2)           out1   -(R6/R2) w0^2 / D
#     LP2    R1  (a->m1)           out2   -(R5/R1) w0^2 / D   (classic AM)
#     HP     C1  (a->m1)           out1   -(C1/C2) s^2 / D
#     BP     R1  (a->m1)           out1   -(1/(R1 C2)) s / D  (mid = R4/R1)
#     BP2    C1  (a->m1)           out2   -(C1/(R6 C2 C3)) s / D
#     LPn/HPn/N  C1 + R2           out1   -(C1/C2)(s^2+wz^2)/D,
#                                          wz^2 = (R8/R7)/(R2 R5 C1 C3)
#
#  MATCHED INVERTER PAIR R7 = R8 (hard constraint, single symbol):
#  R7 (out3->m3) and R8 (out2->m3) enter every pole/zero quantity only as
#  the ratio R8/R7, and R7 = R8 is the condition for the AM active
#  GB-compensation (Q error ~ (w0/wt)^2 instead of ~ Q*w0/wt) -- the whole
#  reason to use this 3-amp family. Both branches therefore carry the ONE
#  symbol R7 here: the ideal TF is R7-free (the ratio cancels exactly), the
#  non-ideal TF keeps R7 (loop dynamics/loading depend on its absolute
#  value), and the BOM's single R7 value designates the matched pair (two
#  physical resistors, same E-series value, ideally one array). Because R7
#  drops from the ideal residuals its Jacobian column is zero; the solver
#  leaves it at its start and unified_solver_v2.rescale_isolated_r5r6 pins
#  it to sqrt(R5*R6) post-solve (a free DOF -- the response is invariant).
#
#  STRUCTURAL NOTCH: with only {C1, R2} populated the s^1 numerator term is
#  IDENTICALLY zero for any element values (R1/R3 branches do not exist),
#  so the transmission zeros sit exactly on the jw axis -- tolerances shift
#  wz but can never fill the null. build_ideal ASSERTS this structural zero
#  at derivation time instead of carrying an on-axis residual.
#
#  THIRD-ORDER ABSORPTION (3LP/3LPn: R1 series in->a + C4 shunt a->gnd;
#  3HP/3HPn: C4 series in->a + R1 shunt a->gnd): because every biquad input
#  lands on a virtual ground, the RC network sees a pure load to (virtual)
#  ground and the absorbed real pole is EXACTLY first-order -- no
#  back-interaction (unlike SK 3rd order). R1/C4 are chosen because they
#  are free in every 3rd-order AM cell (the biquad R1 input exists only on
#  the 2nd-order BP/LP2 cells, and C4 is never a core element).
#
#  Op-amp node mapping: U1 (-)=m1 (+)=gnd out=out1 ; U2 (+)=p2 (-)=gnd
#  out=out2 ; U3 (-)=m3 (+)=gnd out=out3. Non-ideal model: the SAME
#  one-pole A(s) = A_ol*wc/(wc + s*A_ol) for all three amps (matched parts,
#  the standard AM assumption), each behind Ro -- identical to the VCVS /
#  MFB modules' op-amp model.
# =====================================================================

import sympy as sp

from tf_symbols import (s, V2,
                        R1, R2, R3, R4, R5, R6, R7, R8, Ro,
                        C1, C2, C3, C4,
                        A_ol, GBWP_hz,
                        p1, w0, wz, Q, K)

# AM-only node voltages (internal solve variables; eliminated by the nodal
# solve and never present in a cached TF, so module-local symbols are safe).
Va = sp.Symbol("Va")                       # 3rd-order input node a
V1, V3 = sp.symbols("V1 V3")               # U1 / U3 outputs (V2 = U2 output)
Vm1, Vp2, Vm3 = sp.symbols("Vm1 Vp2 Vm3")  # op-amp input nodes (non-ideal)

# kinds that populate each input branch / tap out2
_KINDS_C1 = ("HP", "HPn", "LPn", "N", "BP2")   # C1: a -> m1
_KINDS_R2 = ("LP", "HPn", "LPn", "N")          # R2: a -> p2
_KINDS_R1IN = ("LP2", "BP")                    # R1: a -> m1 (2nd order only)
_KINDS_TAP2 = ("LP2", "BP2")                   # section output = out2
_KINDS_NOTCH = ("LPn", "HPn", "N")


def tap_symbol(topo):
    return V2 if topo["kind"] in _KINDS_TAP2 else V1


def core_var_list(topo):
    """Free search variables (caps first, then resistors; deterministic order).
    R7 designates the matched inverter pair (R7 = R8). It is response-free in
    the ideal model (zero Jacobian column) and is pinned post-solve; it stays
    in var_list so the BOM/snapper/non-ideal correction all carry it."""
    kind, o = topo["kind"], topo["order"]
    caps = []
    if kind in _KINDS_C1:
        caps.append(C1)
    caps += [C2, C3]
    if o == 3:
        caps.append(C4)                       # 3rd-order input-network cap
    res = []
    if kind in _KINDS_R1IN:
        res.append(R1)                        # biquad input (BP, LP2 -> a->m1)
    if o == 3:
        # 3rd-order absorbed-pole prefilter series resistor. LP/LPn/HP/HPn use
        # R1; LP2 already spends R1 on its biquad input, so its prefilter series
        # element is R3 (otherwise-unused in the AM netlist).
        res.append(R3 if kind == "LP2" else R1)
    if kind in _KINDS_R2:
        res.append(R2)
    res += [R4, R5, R6, R7]
    return caps + res


# =====================================================================
# Nodal-equation builder (ideal and non-ideal share it)
# =====================================================================
def am_eqs(topo, vg, opamp_srcs=None, r8_sym=None):
    """KCL list for the AM core.
       vg         : (vm1, vp2, vm3) voltages at the three op-amp input nodes
                    (three zeros ideal; the free Vm1/Vp2/Vm3 symbols non-ideal).
       opamp_srcs : None -> ideal (no output equations; V1/V2/V3 solved from
                    the input-node KCLs); (src1, src2, src3) -> non-ideal
                    Thevenin sources A(s)*(V+ - V-) behind Ro, one per amp.
    Netlist: C1 a-m1 | C2 m1-out1 | C3 p2-out3 | R1 a-m1 (BP/LP2) |
    R2 a-p2 | R4 m1-out1 | R5 m1-out2 | R6 out1-p2 | R7 out3-m3 |
    R7(=R8) out2-m3 ; 3rd-order input network in->a per kind (see header)."""
    kind, o = topo["kind"], topo["order"]
    vm1, vp2, vm3 = vg
    r8 = r8_sym if r8_sym is not None else R7   # ideal/matched -> single R7 symbol
    eqs = []
    if o == 3:                                # node a: absorbed real pole
        if kind in ("HP", "HPn"):             # C4 series in->a, R1 shunt a->gnd
            e = s*C4*(Va - 1) + Va/R1
        else:                                 # low-pass-like: series R, C4 shunt
            series_R = R3 if kind == "LP2" else R1   # LP2 spends R1 on biquad input
            e = (Va - 1)/series_R + s*C4*Va
        if kind in _KINDS_C1:                 # C1  a->m1
            e += s*C1*(Va - vm1)
        if kind in _KINDS_R2:                 # R2  a->p2
            e += (Va - vp2)/R2
        if kind in _KINDS_R1IN:               # R1  a->m1 (LP2 3rd-order biquad input)
            e += (Va - vm1)/R1
        eqs.append(e)
        a = Va
    else:
        a = sp.Integer(1)
    # node m1 (U1 -)
    e = (vm1 - V1)*(s*C2 + 1/R4) + (vm1 - V2)/R5
    if kind in _KINDS_C1:
        e += s*C1*(vm1 - a)
    if kind in _KINDS_R1IN:
        e += (vm1 - a)/R1
    eqs.append(e)
    # node p2 (U2 +)
    e = (vp2 - V1)/R6 + s*C3*(vp2 - V3)
    if kind in _KINDS_R2:
        e += (vp2 - a)/R2
    eqs.append(e)
    # node m3 (U3 -): unity inverter. R8 (out2->m3) and R7 (out3->m3) are one
    # matched pair; ideal uses the single symbol R7 (r8==R7 -> ratio 1 cancels),
    # non-ideal passes r8_sym=R8 so the finite-GB loop and Monte-Carlo see the
    # two resistors independently (mismatch degrades the GB compensation).
    eqs.append((vm3 - V2)/r8 + (vm3 - V3)/R7)
    if opamp_srcs is not None:                # non-ideal output nodes
        src1, src2, src3 = opamp_srcs
        eqs.append((V1 - src1)/Ro + (V1 - vm1)*(s*C2 + 1/R4) + (V1 - vp2)/R6)
        eqs.append((V2 - src2)/Ro + (V2 - vm1)/R5 + (V2 - vm3)/r8)
        eqs.append((V3 - src3)/Ro + s*C3*(V3 - vp2) + (V3 - vm3)/R7)
    return eqs


def am_unknowns(topo, nonideal):
    u = [Va] if topo["order"] == 3 else []
    if nonideal:
        u += [Vm1, Vp2, Vm3]
    u += [V1, V2, V3]
    return u


# =====================================================================
# Conductance-cleared transfer-function solver (same trick as cells_mfb:
# substitute R -> 1/G so the nodal matrix is polynomial, take Cramer
# determinants -- minimal s-degree, no spurious LUsolve factors -- then
# G -> 1/R back). Ro is excluded: it only appears in the non-ideal path,
# which uses raw LUsolve like every other family module.
# =====================================================================
_RES_SYMS = (R1, R2, R3, R4, R5, R6, R7)
_G_SYMS = sp.symbols("Ga1 Ga2 Ga3 Ga4 Ga5 Ga6 Ga7")
_R2G = {R: 1/g for R, g in zip(_RES_SYMS, _G_SYMS)}
_G2R = {g: 1/R for R, g in zip(_RES_SYMS, _G_SYMS)}


def _solve_tf_G(eqs, unk, out_sym):
    eqsG = [e.subs(_R2G) for e in eqs]
    A, b = sp.linear_eq_to_matrix(eqsG, unk)
    idx = unk.index(out_sym)
    den_G = A.det()
    Ab = A.copy(); Ab[:, idx] = b
    num_G = Ab.det()
    return sp.expand(num_G), sp.expand(den_G)


# =====================================================================
# IDEAL derivation (shared): virtual grounds at m1/p2/m3
# =====================================================================
def build_ideal_common(topo, target_subs, K_signed_in_residual=None):
    """Derive the ideal case dict for any AM cell.

    K_signed_in_residual : None  -> gain residual drives b_lead/a_lead -> K
                                    (K itself carries the sign; LP/HP pattern),
                           +-1   -> residual targets SIGN*K with K a positive
                                    magnitude (BP / pure-notch pattern).
    """
    o, kind = topo["order"], topo["kind"]
    eqs = am_eqs(topo, (sp.Integer(0),)*3, opamp_srcs=None)
    unk = am_unknowns(topo, nonideal=False)
    num_G, den_G = _solve_tf_G(eqs, unk, tap_symbol(topo))
    num_poly = sp.Poly(num_G, s)
    den_poly = sp.Poly(den_G, s)
    num = sp.expand(num_G).subs(_G2R)
    den = sp.expand(den_G).subs(_G2R)
    a_lead, b_lead = den_poly.LC(), num_poly.LC()
    dcs = den_poly.monic().all_coeffs()

    # target denominator
    if o == 3:
        dt = sp.expand((s + p1)*(s**2 + (w0/Q)*s + w0**2))
    else:
        dt = sp.expand(s**2 + (w0/Q)*s + w0**2)
    tcs = sp.Poly(dt, s).all_coeffs()
    res = [(dcs[i] - tcs[i]) / tcs[i] for i in range(1, len(dcs))]

    # notch cells: the transmission zeros are STRUCTURALLY on the jw axis
    # (num = LC * s^(deg-2) * (s^2 + wz^2); the s^1 biquad-numerator term does
    # not exist because the R1/R3 branches are absent). Assert that instead of
    # carrying an on-axis residual, and pin only the zero FREQUENCY.
    if kind in _KINDS_NOTCH:
        raw = num_poly.all_coeffs()            # raw G-space coefficients
        for i, c in enumerate(raw):
            if i not in (0, 2) and sp.expand(c) != 0:
                raise AssertionError(
                    f"{topo}: AM notch numerator lost its structural on-axis "
                    f"zero (s^{len(raw)-1-i} coefficient != 0)")
        ncs = num_poly.monic().all_coeffs()    # [1, 0, wz^2 carrier, (0)]
        res.append((ncs[2] - wz**2) / wz**2)   # zero-frequency residual

    # gain residual
    if K_signed_in_residual is None:
        res.append((b_lead / a_lead - K) / K)
    else:
        res.append((b_lead / a_lead - K_signed_in_residual*K) / K)

    res = [e.subs(target_subs).subs(_G2R) for e in res]
    a1_expr = dcs[-2]
    a2_expr = dcs[-3] if len(dcs) >= 3 else dcs[-2]

    tf_syms = sorted([x for x in (num + den).free_symbols
                      if x != s and not x.is_number], key=str)
    return {
        "kind": "ideal", "topo": topo,
        "den_degree": int(den_poly.degree()), "num_degree": int(num_poly.degree()),
        "res_eqs": res, "R5_constraint": None,
        "a1_expr": a1_expr.subs(target_subs).subs(_G2R),
        "a2_expr": a2_expr.subs(target_subs).subs(_G2R),
        "var_list": core_var_list(topo),
        "tf_num": num, "tf_den": den, "tf_var_list": tf_syms,
    }


# =====================================================================
# NON-IDEAL derivation (shared): three matched one-pole op-amps behind Ro.
# Raw rational TF (no expand), identical op-amp model to VCVS/MFB modules.
# =====================================================================
def _nonideal_eqs(topo):
    """The non-ideal nodal equations (shared by the lightweight and symbolic
    builders): three matched one-pole op-amps A(s)=A_ol*wc/(wc+s*A_ol) behind Ro."""
    wc = 2*sp.pi*GBWP_hz
    A_s = A_ol*wc / (wc + s*A_ol)
    srcs = (-A_s*Vm1, A_s*Vp2, -A_s*Vm3)      # U1: +(gnd)-(m1); U2: +(p2)-(gnd); U3: +(gnd)-(m3)
    return am_eqs(topo, (Vm1, Vp2, Vm3), opamp_srcs=srcs, r8_sym=R8)


# Internal-node symbols eliminated by the solve (never a component of the cell):
_NODE_SYMS = frozenset({s, V1, V2, V3, Va, Vm1, Vp2, Vm3})


def build_nonideal_common(topo):
    """LIGHTWEIGHT non-ideal case (S3). It carries NO solved symbolic transfer
    function: for the 3rd-order NOTCH cells that solved object is a ~766k-op /
    ~10 MB nested rational whose LUsolve+together, lambdify, srepr and sympify are
    all catastrophic. The response is instead evaluated numerically by
    `am_mna.build_mna_response`, which solves the SAME small nodal system per
    frequency (validated identical to the symbolic TF to ~1e-10). The heavy
    symbolic TF is produced only on demand by `build_nonideal_symbolic` — used
    solely for the (optional, cached) analytic group-delay overlay, via
    `tf_derivation_v2.ensure_symbolic_tf`.

    `mna=True` tags the case so `make_response_func` routes it to the MNA
    evaluator; every non-AM family (which never sets this flag) keeps its existing
    tiny symbolic route unchanged."""
    eqs = _nonideal_eqs(topo)
    free = set()
    for e in eqs:
        free |= e.free_symbols
    tf_syms = sorted([x for x in free
                      if x not in _NODE_SYMS and not x.is_number], key=str)
    return {
        "kind": "nonideal", "topo": topo,
        "den_degree": None, "num_degree": None,
        "tf_num": None, "tf_den": None, "tf_var_list": tf_syms,
        "mna": True,
    }


def build_nonideal_symbolic(topo):
    """Heavy path: the fully-solved symbolic non-ideal TF (num/den), kept
    byte-for-byte identical to the original `build_nonideal_common` so any
    consumer that reads `tf_num`/`tf_den` (analytic group delay) is unchanged.
    Built lazily, only when the symbolic form is actually requested."""
    eqs = _nonideal_eqs(topo)
    unk = am_unknowns(topo, nonideal=True)
    A, b = sp.linear_eq_to_matrix(eqs, unk)
    sol = A.LUsolve(b)[unk.index(tap_symbol(topo))]
    num, den = sp.fraction(sp.together(sol))   # NO expand
    tf_syms = sorted([x for x in (num + den).free_symbols
                      if x != s and not x.is_number], key=str)
    return {
        "kind": "nonideal", "topo": topo,
        "den_degree": None, "num_degree": None,
        "tf_num": num, "tf_den": den, "tf_var_list": tf_syms,
        "mna": True,
    }
