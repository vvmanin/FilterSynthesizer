# Filter Synthesizer

Active filter design, from a specification to a bill of materials. Choose a
Butterworth, Chebyshev, Inverse Chebyshev or Elliptic response for a low-pass,
high-pass, band-pass or band-reject filter. Filter Synthesizer splits it into
op-amp sections and solves each one as a Sallen-Key, multiple-feedback or
Ackerberg–Mossberg circuit on standard E-series parts, with an ideal or a real
op-amp model. It ranks the candidate BOMs by sensitivity, draws each section's
schematic, checks the whole cascade against component tolerances with
Monte-Carlo, and writes it all up as a PDF report. It runs locally, in your
browser.

![Filter Synthesizer: the specification on the left, the design pipeline in five tabs.](docs/manual/img/01-first-run.png)

## Download

The Windows build is attached to the [latest release](../../releases/latest) as
a zip. Unzip it anywhere:

```
FilterSynthesizer/
├── FilterSynthesizer.exe          double-click to start
├── _internal/                     the runtime; keep it next to the EXE
├── Section_Schematic_Diagrams/    schematic drawings, one per circuit (editable)
├── Quick_Start.pdf
└── User_Manual.pdf
```

A console window opens and, 10–20 seconds later on the first launch (2–4 s
after that), the app appears in your browser, normally at
`http://localhost:8501`. Closing the console window quits it. Nothing is
installed: to remove it, delete the folder and
`%LOCALAPPDATA%\FilterSynthesizer`, which holds its cache.

## Run from source

Python 3.11 or 3.12, in a fresh virtual environment:

```bash
pip install -r requirements.txt
python -m streamlit run app.py
```

## Documentation

- **[Quick Start](docs/Quick_Start.pdf)** — one filter, from specification to
  BOM, schematic and PDF report, in about ten minutes.
- **[User Manual](docs/User_Manual.pdf)** — every control, choosing a circuit
  family, what a real op-amp changes, and what to do about every message the
  app shows.

The PDFs match the latest release. Their Markdown sources in
[`docs/manual/`](docs/manual/) follow the current code and can be read
directly on GitHub.

## For developers

- [Architecture](docs/ARCHITECTURE.md) — how the modules fit together
- [Building](docs/BUILDING.md) — packaging the Windows build with `build.bat`
- [Documentation workflow](docs/manual/DOC_WORKFLOW.md) — keeping the manuals
  in step with the UI

## License

[GPL-3.0-or-later](LICENSE). Copyright © 2026 Viacheslav Manin.
