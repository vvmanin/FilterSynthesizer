# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""
Verify the 4 first-order families before building the solver.

  1LP-ni / 1LP-inv / 1HP-ni / 1HP-inv,  each x {unity, gained, atten}

For each cell we:
  (a) build the NON-IDEAL nodal solution (real op-amp: 1-pole A_s, output Ro),
  (b) compare it to the closed-form IDEAL TF in the near-ideal op-amp limit,
  (c) check the closed-form REALIZER hits the target pole freq and gain.
"""
import sympy as sp
import numpy as np

s = sp.symbols('s')
R1, R2, R3, R4, C1, Ro = sp.symbols('R1 R2 R3 R4 C1 Ro', positive=True)
A_ol, GBWP = sp.symbols('A_ol GBWP_hz', positive=True)
Vin = sp.Integer(1)
wc = 2*sp.pi*GBWP
A_s = A_ol*wc/(wc + s*A_ol)          # 1-pole open-loop gain (matches tf_derivation_v2)


# ---------------------------------------------------------------- non-ideal
def solve_nonideal(family, realization, gain):
    Vp, Vm, Vout = sp.symbols('Vp Vm Vout')
    eqs = []
    if realization == 'ni':
        # ---- input network -> node p ----
        if family == 'LP':                       # R1: in->p ; C1 (+ R2 if atten): p->gnd
            shunt = s*C1 + (1/R2 if gain == 'atten' else 0)
            eqs.append((Vp - Vin)/R1 + Vp*shunt)
        else:                                    # HP: (R1 series C1): in->p ; R2: p->gnd
            ys = s*C1/(1 + s*R1*C1) if gain == 'atten' else s*C1   # R1 shorted unless atten
            eqs.append((Vp - Vin)*ys + Vp/R2)
        # ---- feedback at m / out ----
        if gain == 'gained':                     # R3: m->out ; R4: m->gnd
            eqs.append((Vm - Vout)/R3 + Vm/R4)
            eqs.append((Vout - A_s*(Vp - Vm))/Ro + (Vout - Vm)/R3)
        else:                                    # unity/atten: R3 shorted (Vm=Vout), R4 absent
            eqs.append(Vm - Vout)
            eqs.append((Vout - A_s*(Vp - Vm))/Ro)
        unk = [Vp, Vm, Vout]
    else:                                        # inverting: V+ = 0
        if family == 'LP':                       # R1: in->m ; R2||C1: m->out
            yf = 1/R2 + s*C1
            eqs.append((Vm - Vin)/R1 + (Vm - Vout)*yf)
            eqs.append((Vout - A_s*(0 - Vm))/Ro + (Vout - Vm)*yf)
        else:                                    # HP: (R1 series C1): in->m ; R2: m->out
            ys = s*C1/(1 + s*R1*C1)
            eqs.append((Vm - Vin)*ys + (Vm - Vout)/R2)
            eqs.append((Vout - A_s*(0 - Vm))/Ro + (Vout - Vm)/R2)
        unk = [Vm, Vout]
    sol = sp.solve(eqs, unk, dict=True)[0]
    num, den = sp.fraction(sp.together(sol[Vout]))
    return num, den


# ---------------------------------------------------------------- ideal (closed form)
def ideal_TF(family, realization, gain):
    if realization == 'ni':
        if family == 'LP':
            if gain == 'gained': return (1 + R3/R4)/(1 + s*R1*C1)
            if gain == 'unity':  return 1/(1 + s*R1*C1)
            if gain == 'atten':  return (R2/(R1+R2))/(1 + s*(R1*R2/(R1+R2))*C1)
        else:
            if gain == 'gained': return (1 + R3/R4)*s*R2*C1/(1 + s*R2*C1)
            if gain == 'unity':  return s*R2*C1/(1 + s*R2*C1)
            if gain == 'atten':  return s*R2*C1/(1 + s*(R1+R2)*C1)
    else:
        if family == 'LP': return -(R2/R1)/(1 + s*R2*C1)
        else:              return -s*R2*C1/(1 + s*R1*C1)


# ---------------------------------------------------------------- closed-form realizer
def realize(family, realization, gain, w0, G, C1v, R4v=0.1):
    """Return component values for a target pole w0 (rad/s) and gain G.
       LP gain G = DC gain; HP gain G = HF gain. inv: |G| any; ni: gained>=1, atten<1."""
    g = abs(G)
    if realization == 'ni':
        if family == 'LP':
            if gain == 'gained': return {'R1': 1/(w0*C1v), 'R3': (g-1)*R4v, 'R4': R4v, 'C1': C1v}
            if gain == 'unity':  return {'R1': 1/(w0*C1v), 'C1': C1v}
            if gain == 'atten':  return {'R1': 1/(w0*g*C1v), 'R2': 1/(w0*(1-g)*C1v), 'C1': C1v}
        else:
            if gain == 'gained': return {'R2': 1/(w0*C1v), 'R3': (g-1)*R4v, 'R4': R4v, 'C1': C1v}
            if gain == 'unity':  return {'R2': 1/(w0*C1v), 'C1': C1v}
            if gain == 'atten':  return {'R2': g/(w0*C1v), 'R1': (1-g)/(w0*C1v), 'C1': C1v}
    else:
        if family == 'LP': return {'R2': 1/(w0*C1v), 'R1': 1/(w0*g*C1v), 'C1': C1v}
        else:              return {'R1': 1/(w0*C1v), 'R2': g/(w0*C1v), 'C1': C1v}


# ================================================================ run checks
subs_vals = {R1: 0.043, R2: 0.1, R3: 0.39, R4: 0.2, C1: 0.0033}   # arbitrary, units cancel
near_ideal = {A_ol: sp.Float(1e12), GBWP: sp.Float(1e15), Ro: sp.Float(1e-9)}
w = 2*np.pi*np.logspace(0, 5, 60)

print("=== (a/b) NON-IDEAL  ->  IDEAL  limit check ===")
worst = 0.0
for fam in ['LP', 'HP']:
    for real in ['ni', 'inv']:
        for gn in ['unity', 'gained', 'atten']:
            num, den = solve_nonideal(fam, real, gn)
            Hni = (num/den)
            Hid = ideal_TF(fam, real, gn)
            fni = sp.lambdify(s, Hni.subs(subs_vals).subs(near_ideal), 'numpy')
            fid = sp.lambdify(s, Hid.subs(subs_vals), 'numpy')
            a = np.asarray(fni(1j*w), dtype=complex)
            b = np.asarray(fid(1j*w), dtype=complex)
            err = float(np.max(np.abs(a - b)))
            worst = max(worst, err)
            # also report the symbolic denominator degree in s (sanity: 1, not 2)
            ddeg = sp.degree(sp.Poly(sp.expand(den.subs(near_ideal)), s), s)
            print(f"  {fam}-{real}-{gn:7s}  max|Hni-Hid| = {err:.2e}   den_deg(s)={ddeg}")
print(f"  WORST ideal-limit error = {worst:.2e}\n")

print("=== (c) CLOSED-FORM REALIZER target-hit (pole freq + gain) ===")
f0 = 1000.0
w0 = 2*np.pi*f0
gains = {'unity': 1.0, 'gained': 4.0, 'atten': 0.25}
inv_gains = {'unity': 1.0, 'gained': 4.0, 'atten': 0.25}
ok_all = True
for fam in ['LP', 'HP']:
    for real in ['ni', 'inv']:
        gset = inv_gains if real == 'inv' else gains
        for gn, G in gset.items():
            comp = realize(fam, real, gn, w0, G, C1v=0.0033)
            Hid = ideal_TF(fam, real, gn)
            symtab = {'R1': R1, 'R2': R2, 'R3': R3, 'R4': R4, 'C1': C1}
            sub = {symtab[k]: v for k, v in comp.items()}
            f = sp.lambdify(s, Hid.subs(sub), 'numpy')
            # gain reference: DC for LP, HF for HP
            ref = abs(complex(f(1j*1e-7*w0))) if fam == 'LP' else abs(complex(f(1j*1e7*w0)))
            at_w0 = abs(complex(f(1j*w0)))
            gain_err = abs(ref - abs(G))/abs(G)
            # at w0, |H| should be ref/sqrt(2)  (the -3 dB pole)
            pole_err = abs(at_w0 - ref/np.sqrt(2))/(ref/np.sqrt(2))
            good = gain_err < 1e-9 and pole_err < 1e-9
            ok_all &= good
            print(f"  {fam}-{real}-{gn:7s} G={G:<5} | gain={ref:.5f} (err {gain_err:.1e}) "
                  f"| -3dB@f0 err {pole_err:.1e}  {'OK' if good else 'FAIL'}")
print(f"\n  realizer all-pass = {ok_all}")

def test_wrapping_is_presentation_only():
    """Wrapped display lines must re-join into the exact copy-paste string.

    The user reads a wrapped H(s) but copies a flat one; those two must never
    be allowed to drift apart. `sep` is what makes this exact — poly_to_latex
    joins terms with a space, roots_to_biquad_latex concatenates them.
    """
    import numpy as np
    from tf_utils import (poly_to_latex, poly_to_latex_lines,
                          roots_to_biquad_latex, roots_to_biquad_lines)

    polys = [
        np.array([1.0]),                                   # constant
        np.array([1.0, 1.0]),                              # 1st order
        np.array([1.0, 1.414214, 1.0]),                    # 2nd order
        np.array([1.0, -2.5, 3.0, -0.125]),                # negative coeffs
        np.array([1.0, 1.2e-6, 3.4e5, 9.9e9]),             # scientific notation
        np.array([1.0, 0.0, 0.0, 5.0]),                    # pruned interior zeros
        np.concatenate([[1.0], np.linspace(0.5, 9.5, 20)]),  # 20th order
    ]
    for p in polys:
        assert " ".join(poly_to_latex_lines(p)) == poly_to_latex(p), p

    roots = [-0.2+0.97j, -0.2-0.97j, -0.55+0.7j, -0.55-0.7j, -0.9+0j]
    assert "".join(roots_to_biquad_lines(roots)) == roots_to_biquad_latex(roots)