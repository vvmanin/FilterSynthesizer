# FilterSynthesizer 1.1.0

## New

- **Bessel and Equiripple Delay responses** — filters designed for flat group
  delay, low-pass and band-pass. The order can be chosen from a delay, corner,
  flatness or stopband requirement, and the corner from a target delay τ₀.
  An optional equiripple stopband adds notches without disturbing the delay.
- **Custom H(s)** — enter your own transfer function (coefficients, f₀/Q
  factors, Tietze–Schenk tables or poles and zeros, with paste from MATLAB) as
  a low-pass prototype or as a complete filter; the rest of the flow works on it
  unchanged.
- **LTspice export** — the whole picked cascade as LTspice schematics and
  netlists, nominal and Monte-Carlo, with a README of the values LTspice should
  show. Inter-stage loading is simulated.
- **Vendor op-amp models** — import a manufacturer's SPICE model once (TL072H,
  LM358B, LMV358A, OPA1656, TLV900x) and the export uses it. The models are not
  shipped; you download them yourself.
- **Op-amp library** — save your own parts, adjust shipped ones, revert at any
  time. New parts: TL072H, LM358B, OPA1656, TLV9001/2/4.
- **Batch mode** — one op-amp and one component envelope for all sections, and
  *Solve all sections*.

## Faster

- **Hardware solving is 5–33× faster per section**: a section that took 4–27 s
  now solves in about 0.1–3 s (measured with a real op-amp model).
- **Sections solve in parallel**, one per processor core, instead of one after
  another.
- **No idle load**: the app no longer uses CPU while nothing is solving, even
  with large designs.

## Improved and fixed

- **Four tabs instead of five**: roots and H(s) now sit at the end of
  Response Plots. Phase and group delay overlay the magnitude plot.
  Colour-coded boxes show which controls change the design (blue) and where you
  pick a result (amber).
- **Near-notch sections** (a zero just off the pole frequency) are realized
  where the zero really is, instead of being forced onto a pure notch.
- **Band-pass and band-reject pairing** always produces buildable sections; it
  no longer loses real poles or fails on wide bands. A 3rd-order band-pass
  section starts on MFB, the family that realizes it.
- **Band-pass overall gain** on the Topology tab now matches the realized
  response.
- **Documentation**: Quick Start and User Manual rewritten for this version,
  with a new LTspice chapter.

## Before you upgrade

- TL072, LM358 and NE5532 are no longer in the op-amp list; a section that
  used one falls back to Ideal. Use TL072H, LM358B or a part of your own.
