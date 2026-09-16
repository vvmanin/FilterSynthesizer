# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

import numpy as np
import plotly.graph_objects as go

def evaluate_h_complex(f_hz_array, poles_rad, zeros_rad, k_gain):
    """Returns the raw complex array H(jw) for magnitude, phase, and delay calcs."""
    w = 2 * np.pi * np.asarray(f_hz_array, dtype=float)
    jw = 1j * w
    
    num = np.ones_like(jw, dtype=complex) * k_gain
    if len(zeros_rad) > 0:
        for z in zeros_rad:
            num *= (jw - z)
            
    den = np.ones_like(jw, dtype=complex)
    if len(poles_rad) > 0:
        for p in poles_rad:
            den *= (jw - p)
            
    return num / den

def evaluate_transfer_function(f_hz, poles_rad, zeros_rad, k_gain):
    h_complex = evaluate_h_complex(f_hz, poles_rad, zeros_rad, k_gain)
    mag_linear = np.maximum(np.abs(h_complex), 1e-12)
    return 20 * np.log10(mag_linear)

def plot_main_magnitude(engine_results, f_corner_ui, freq_unit, multiplier, alpha_max, as_db, filter_type, target_gain_units, f2_corner_ui=None, as_db_2=None):
    import plotly.graph_objects as go
    import numpy as np
    from plot_utils import evaluate_transfer_function
    
    p_rad, z_rad = engine_results['poles'], engine_results['zeros']
    k_scaled = engine_results['k'] * target_gain_units
    target_gain_db = 20 * np.log10(target_gain_units) if target_gain_units > 0 else 0.0

    if filter_type == "Bandpass" and f2_corner_ui is not None:
        safe_min_f = f_corner_ui * 0.01
        safe_max_f = f2_corner_ui * 100.0
        fs_hp_hz = engine_results.get('f_stop_hp_hz')
        if fs_hp_hz:
            fs_hp_ui = fs_hp_hz / multiplier
            if fs_hp_ui > f_corner_ui * 0.0001: safe_min_f = min(safe_min_f, fs_hp_ui * 0.5)
        fs_lp_hz = engine_results.get('f_stop_lp_hz')
        if fs_lp_hz:
            fs_lp_ui = fs_lp_hz / multiplier
            if fs_lp_ui < f2_corner_ui * 10000.0: safe_max_f = max(safe_max_f, fs_lp_ui * 1.5)
            
    elif filter_type == "Band-Reject" and f2_corner_ui is not None:
        safe_min_f = f_corner_ui * 0.1
        safe_max_f = f2_corner_ui * 10.0
        
    elif filter_type == "Highpass":
        safe_min_f = f_corner_ui * 0.01
        safe_max_f = f_corner_ui * 100.0
        fs_hz = engine_results.get('f_stop_hz')
        if fs_hz:
            fs_ui = fs_hz / multiplier
            if fs_ui > f_corner_ui * 0.0001: safe_min_f = min(safe_min_f, fs_ui * 0.5)
            
    else: 
        safe_min_f = f_corner_ui * 0.01 if f_corner_ui > 0 else 0.1
        safe_max_f = f_corner_ui * 100.0
        fs_hz = engine_results.get('f_stop_hz')
        if fs_hz:
            fs_ui = fs_hz / multiplier
            if fs_ui < f_corner_ui * 10000.0: safe_max_f = max(safe_max_f, fs_ui * 1.5)

    f_ui_array = np.logspace(np.log10(safe_min_f), np.log10(safe_max_f), 5000)
    mag_db = evaluate_transfer_function(f_ui_array * multiplier, p_rad, z_rad, k_scaled)
    
    fig = go.Figure()
    fig.add_trace(go.Scattergl(x=f_ui_array, y=mag_db, mode='lines', line=dict(color='blue', width=1.6), showlegend=False))

    fig.add_hline(y=target_gain_db - alpha_max, line_dash="dash", line_color="red", opacity=0.6, line_width=1, annotation_text="-α_max")
    fig.add_hline(y=target_gain_db, line_width=1, line_color="black", opacity=0.5)

    if filter_type == "Bandpass" and as_db_2 is not None:
        # Lower As (Stops at fc1)
        fig.add_trace(go.Scattergl(x=[safe_min_f, f_corner_ui], y=[target_gain_db - as_db, target_gain_db - as_db], mode="lines", line=dict(color="#2ca02c", dash="dash", width=1), hoverinfo="skip", showlegend=False))
        fig.add_annotation(x=np.log10(f_corner_ui) - 0.3, y=target_gain_db - as_db, text="-A_s (Lower)", showarrow=False, yshift=10, font=dict(color="#2ca02c"))
        
        # Upper As (Starts at fc2)
        fig.add_trace(go.Scattergl(x=[f2_corner_ui, safe_max_f], y=[target_gain_db - as_db_2, target_gain_db - as_db_2], mode="lines", line=dict(color="#115e11", dash="dash", width=1), hoverinfo="skip", showlegend=False))
        fig.add_annotation(x=np.log10(f2_corner_ui) + 0.3, y=target_gain_db - as_db_2, text="-A_s (Upper)", showarrow=False, yshift=10, font=dict(color="#115e11"))
        min_as = max(as_db, as_db_2)
        
    elif filter_type == "Band-Reject":
        # Single continuous Stopband Limit between fc1 and fc2
        fig.add_trace(go.Scattergl(x=[f_corner_ui*0.9, f2_corner_ui*1.1], y=[target_gain_db - as_db, target_gain_db - as_db], mode="lines", line=dict(color="#2ca02c", dash="dash", width=1), hoverinfo="skip", showlegend=False))
        fig.add_annotation(x=np.log10(np.sqrt(f_corner_ui * f2_corner_ui)), y=target_gain_db - as_db, text="-A_s", showarrow=False, yshift=10, font=dict(color="#2ca02c"))
        min_as = as_db
        
    else:
        fig.add_hline(y=target_gain_db - as_db, line_dash="dash", line_color="green", opacity=0.6, line_width=1, annotation_text="-A_s")
        min_as = as_db

    # --- VERTICAL LINES (CORNERS) ---
    if filter_type in ["Bandpass", "Band-Reject"] and f2_corner_ui is not None:
        fig.add_vline(x=f_corner_ui, line_dash="dot", line_color="black", opacity=0.7, line_width=1, annotation_text=f"fc1 ({freq_unit})")
        fig.add_vline(x=f2_corner_ui, line_dash="dot", line_color="black", opacity=0.7, line_width=1, annotation_text=f"fc2 ({freq_unit})")
    else:
        fig.add_vline(x=f_corner_ui, line_dash="dot", line_color="black", opacity=0.7, line_width=1, annotation_text=f"fc ({freq_unit})")

    # --- VERTICAL LINES (STOPBANDS) ---
    if filter_type in ["Bandpass", "Band-Reject"]:
        fs_hp_hz = engine_results.get('f_stop_hp_hz')
        fs_lp_hz = engine_results.get('f_stop_lp_hz')
        if fs_hp_hz: fig.add_vline(x=fs_hp_hz / multiplier, line_dash="dot", line_color="magenta", opacity=0.6, line_width=1, annotation_text=f"fs1 ({freq_unit})")
        if fs_lp_hz: fig.add_vline(x=fs_lp_hz / multiplier, line_dash="dot", line_color="magenta", opacity=0.6, line_width=1, annotation_text=f"fs2 ({freq_unit})")
    else:
        fs_hz = engine_results.get('f_stop_hz')
        if fs_hz: fig.add_vline(x=fs_hz / multiplier, line_dash="dot", line_color="magenta", opacity=0.6, line_width=1, annotation_text=f"fs ({freq_unit})")

    # --- IDEAL NOTCHES ---
    ideal_notches = []
    if filter_type in ["Bandpass", "Band-Reject"]:
        ideal_notches.extend(engine_results.get('ideal_notches_hp_hz', []))
        ideal_notches.extend(engine_results.get('ideal_notches_lp_hz', []))
        # Add a direct grab just in case the BR engine drops them into the old key
        ideal_notches.extend(engine_results.get('ideal_notches_hz', []))
    else:
        ideal_notches.extend(engine_results.get('ideal_notches_hz', []))

    # Helper function to dynamically calculate the top limit based on notch frequency
    def get_y_top(nx_ui):
        if filter_type == "Bandpass" and as_db_2 is not None and f2_corner_ui is not None:
            f0_ui = np.sqrt(f_corner_ui * f2_corner_ui)
            if nx_ui < f0_ui:
                return target_gain_db - as_db - 5.0
            else:
                return target_gain_db - as_db_2 - 5.0
        return target_gain_db - as_db - 5.0

    # Draw line well past the bottom viewport limit
    y_bottom = target_gain_db - min_as - 30.0 

    for nz_hz in ideal_notches:
        nx = nz_hz / multiplier
        y_top = get_y_top(nx)
        fig.add_trace(go.Scattergl(
            x=[nx, nx], y=[y_bottom, y_top], mode="lines", 
            line=dict(color="gray", dash="solid", width=1), 
            opacity=0.4, hoverinfo="skip", showlegend=False
        ))

    # --- ACTUAL FINAL NOTCHES ---
    for z in z_rad:
        if z.imag > 1e-6 and abs(z.real) < 1e-6:
            nx = (z.imag / (2 * np.pi)) / multiplier
            y_top = get_y_top(nx)
            fig.add_trace(go.Scattergl(
                x=[nx, nx], y=[y_bottom, y_top], mode="lines", 
                line=dict(color="gray", dash="dot", width=1), 
                opacity=0.7, hoverinfo="skip", showlegend=False
            ))

    fig.update_layout(
        xaxis_type="log", xaxis_title=f"Frequency ({freq_unit})", yaxis_title="Magnitude (dB)",
        xaxis_range=[np.log10(safe_min_f), np.log10(safe_max_f)], 
        yaxis_range=[target_gain_db - min_as - 20, target_gain_db + 5], margin=dict(l=20, r=20, t=30, b=20), height=450, showlegend=False
    )
    return fig

def plot_passband_magnitude(engine_results, f_corner_ui, freq_unit, multiplier, alpha_max, target_gain_units, filter_type, f2_corner_ui=None):
    import plotly.graph_objects as go
    import numpy as np
    from plot_utils import evaluate_transfer_function

    p_rad, z_rad = engine_results['poles'], engine_results['zeros']
    k_scaled = engine_results['k'] * target_gain_units
    target_gain_db = 20 * np.log10(target_gain_units) if target_gain_units > 0 else 0.0

    if filter_type == "Bandpass" and f2_corner_ui is not None:
        bw = f2_corner_ui - f_corner_ui
        f_min = max(f_corner_ui - (bw * 0.25), 1e-6)
        f_max = f2_corner_ui + (bw * 0.25)
        xaxis_type = "linear"
        vlines = [(f_corner_ui, "fc1"), (f2_corner_ui, "fc2")]
    elif filter_type == "Highpass" or filter_type == "Band-Reject-Upper":
        anchor_f = f2_corner_ui if filter_type == "Band-Reject-Upper" else f_corner_ui
        lbl = "fc2" if filter_type == "Band-Reject-Upper" else "fc"
        f_min = anchor_f * 0.8
        f_max = anchor_f * 25.0
        xaxis_type = "log"
        vlines = [(anchor_f, lbl)]
    else: # Lowpass or Band-Reject-Lower
        anchor_f = f_corner_ui
        lbl = "fc1" if filter_type == "Band-Reject-Lower" else "fc"
        f_min = anchor_f * 0.0001 if anchor_f > 0 else 0.0
        f_max = anchor_f * 1.1
        xaxis_type = "linear"
        vlines = [(anchor_f, lbl)]

    if xaxis_type == "log":
        f_ui_array = np.logspace(np.log10(max(f_min, 1e-3)), np.log10(f_max), 2000)
    else:
        f_ui_array = np.linspace(f_min, f_max, 2000)

    mag_db = evaluate_transfer_function(f_ui_array * multiplier, p_rad, z_rad, k_scaled)
    
    fig = go.Figure()
    fig.add_trace(go.Scattergl(x=f_ui_array, y=mag_db, mode='lines', line=dict(color='blue', width=1.6)))

    fig.add_hline(y=target_gain_db, line_width=1, line_color="black", opacity=0.5)
    fig.add_hline(y=target_gain_db - alpha_max, line_dash="dash", line_color="red", opacity=0.6, line_width=1, annotation_text="-α_max")
    
    for vl, lbl in vlines:
        fig.add_vline(x=vl, line_dash="dot", line_color="black", opacity=0.7, line_width=1, annotation_text=f"{lbl} ({freq_unit})")

    # Explicitly lock the view to match the exact array bounds
    x_range = [np.log10(max(f_min, 1e-3)), np.log10(f_max)] if xaxis_type == "log" else [f_min, f_max]

    fig.update_layout(
        xaxis_type=xaxis_type, 
        xaxis_range=x_range, # <--- This prevents the explosion
        xaxis_title=f"Frequency ({freq_unit})" if xaxis_type == "log" else f"Linear Frequency ({freq_unit})", 
        yaxis_title="Magnitude (dB)",
        yaxis_range=[target_gain_db - alpha_max - 0.25, target_gain_db + 0.25], 
        margin=dict(l=20, r=20, t=30, b=10), height=300, showlegend=False
    )
    return fig
    
def plot_phase_delay(engine_results, f_corner_ui, freq_unit, multiplier, show_phase, show_gd):
    p_rad, z_rad = engine_results['poles'], engine_results['zeros']
    
    # --- CALCULATE SAFE VIEWPORT LIMITS FIRST ---
    safe_max_f = f_corner_ui * 100.0
    fs_hz = engine_results.get('f_stop_hz')
    if fs_hz is not None:
        fs_ui = fs_hz / multiplier
        if fs_ui < f_corner_ui * 10000.0:
            safe_max_f = max(safe_max_f, fs_ui * 1.5)

    # NOW generate the array so it perfectly fills the viewport
    f_ui_array = np.logspace(np.log10(f_corner_ui * 0.01), np.log10(safe_max_f), 1500)
    w = 2 * np.pi * (f_ui_array * multiplier)
    jw = 1j * w
    
    fig = go.Figure()
    
    if show_phase:
        h_complex = evaluate_h_complex(f_ui_array * multiplier, p_rad, z_rad, 1.0)
        phase_deg = np.degrees(np.unwrap(np.angle(h_complex)))
        fig.add_trace(go.Scattergl(x=f_ui_array, y=phase_deg, mode='lines', line=dict(color='orange', width=2), name='Phase (deg)'))

    if show_gd:
        gd_s = np.zeros_like(w)
        if len(p_rad) > 0: gd_s += np.sum(np.real(1.0 / (jw[:, None] - p_rad)), axis=1)
        if len(z_rad) > 0: gd_s -= np.sum(np.real(1.0 / (jw[:, None] - z_rad)), axis=1)
        gd_ms = gd_s * 1000.0
        fig.add_trace(go.Scattergl(x=f_ui_array, y=gd_ms, mode='lines', line=dict(color='green', width=2), name='Group Delay (ms)', yaxis='y2' if show_phase else 'y'))
    
    layout_kwargs = dict(
        xaxis_type="log", xaxis_title=f"Frequency ({freq_unit})", 
        xaxis_range=[np.log10(f_corner_ui * 0.01), np.log10(safe_max_f)], # <--- THIS LOCKS THE VIEWPORT
        margin=dict(l=20, r=20, t=30, b=20), height=350,
        showlegend=True, legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    
    if show_phase and show_gd:
        layout_kwargs['yaxis'] = dict(title='Phase (deg)', color='orange')
        layout_kwargs['yaxis2'] = dict(title='Group Delay (ms)', color='green', overlaying='y', side='right')
    elif show_phase: layout_kwargs['yaxis'] = dict(title='Phase (deg)', color='orange')
    elif show_gd: layout_kwargs['yaxis'] = dict(title='Group Delay (ms)', color='green')
        
    fig.update_layout(**layout_kwargs)
    
    fig.add_vline(x=f_corner_ui, line_dash="dot", line_color="black", opacity=0.7, line_width=1)
    if fs_hz is not None:
        fig.add_vline(x=fs_hz / multiplier, line_dash="dot", line_color="magenta", opacity=0.6, line_width=1, annotation_text=f"fs ({freq_unit})")
    
    return fig


def plot_pole_zero_map(poles, zeros, scale_type, unit_label, stretch_factor=1.0):
    fig = go.Figure()

    # Draw Unit Circle if Normalized
    if scale_type == "Normalized":
        theta = np.linspace(0, 2*np.pi, 300)
        fig.add_trace(go.Scattergl(
            x=np.cos(theta), y=np.sin(theta),
            mode='lines', line=dict(color='black', dash='dash', width=1),
            opacity=0.2, hoverinfo='skip', name='Unit Circle'
        ))

    def add_roots(roots, marker_symbol, color, name):
        if len(roots) == 0: return
        
        x_exact = np.real(roots)
        y_exact = np.imag(roots)
        hover_texts = [f"Real: {r.real:+.6e}<br>Imag: {r.imag:+.6e}j" for r in roots]

        rounded_roots = np.round(roots, 3)
        unique_roots, counts = np.unique(rounded_roots, return_counts=True)
        
        text_labels = []
        for exact_r in roots:
            match_idx = np.argmin(np.abs(unique_roots - exact_r))
            count = counts[match_idx]
            if count > 1:
                text_labels.append(f" x{count}")
                counts[match_idx] = 0 
            else:
                text_labels.append("")

        fig.add_trace(go.Scattergl(
            x=x_exact, y=y_exact, mode='markers+text',
            # REDUCED SIZE TO 8, ENFORCED LINE WIDTH 1
            marker=dict(symbol=marker_symbol, size=9, line=dict(width=1.5, color=color)),
            text=text_labels, textposition="top right", textfont=dict(color=color, size=14),
            hovertext=hover_texts, hoverinfo="text", name=name
        ))
       

    add_roots(poles, 'x-thin', 'blue', 'Poles')
    add_roots(zeros, 'circle-open', 'red', 'Zeros')
    
    # --- NEW: CALCULATE EXPLICIT AXIS LIMITS BASED ON ROOTS ---
    all_roots = list(poles) + list(zeros)
    if len(all_roots) > 0:
        max_x = max([abs(r.real) for r in all_roots])
        max_y = max([abs(r.imag) for r in all_roots])
    else:
        max_x, max_y = 1.0, 1.0

    # Ensure a minimum view window, plus a 15% margin
    limit_x = max(max_x, 0.1) * 1.15 if scale_type == "Normalized" else max_x * 1.15
    limit_y = max(max_y, 0.1) * 1.15 if scale_type == "Normalized" else max_y * 1.15

    # Apply explicit ranges to force cropping
    layout_kwargs = dict(
        xaxis_title=f"Real ({unit_label})", 
        yaxis_title=f"Imaginary ({unit_label})",
        xaxis=dict(range=[-limit_x, limit_x]),
        margin=dict(l=20, r=20, t=30, b=20), height=600, showlegend=True,
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01)
    )
    
    if stretch_factor == 1.0:
        layout_kwargs['yaxis'] = dict(range=[-limit_y, limit_y], scaleanchor="x", scaleratio=1)
    else:
        layout_kwargs['yaxis'] = dict(range=[-limit_y, limit_y], scaleanchor="x", scaleratio=1.0 / stretch_factor)
        
    fig.update_layout(**layout_kwargs)
    
    # Add crosshairs through the origin
    fig.add_hline(y=0, line_width=1, line_color="black", opacity=0.3)
    fig.add_vline(x=0, line_width=1, line_color="black", opacity=0.3)
    
    return fig

def plot_mnemoscheme_map(poles, zeros, scale_type, unit_label, paired_stages, stretch_factor=1.0):
    """Draws the textbook-style interactive mnemoscheme with omega_0 circles and thick connections."""
    fig = go.Figure()

    # Distinct, high-contrast colors for different hardware stages
    stage_colors = ['#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#bcbd22', '#17becf']

    def add_roots(roots, marker_symbol, color, name, text_labels):
        if len(roots) == 0: return
        x_exact, y_exact = np.real(roots), np.imag(roots)
        hover_texts = [f"Real: {r.real:+.6e}<br>Imag: {r.imag:+.6e}j" for r in roots]

        fig.add_trace(go.Scattergl(
            x=x_exact, y=y_exact, mode='markers+text',
            marker=dict(symbol=marker_symbol, size=9, color=color, line=dict(width=2.5, color=color)),
            text=text_labels, textposition="middle right", textfont=dict(color=color, size=11, weight="bold"),
            hovertext=hover_texts, hoverinfo="text", name=name
        ))

    # --- RENDER MNEMOSCHEME ---
    theta = np.linspace(0, 2*np.pi, 300)
    
    assigned_poles = []
    assigned_zeros = []

    for idx, stage in enumerate(paired_stages):
        c = stage_colors[idx % len(stage_colors)]
        stage_num = str(stage['stage_num'])
        
        s_poles = np.array(stage['poles'])
        s_zeros = np.array(stage['zeros'])
        
        assigned_poles.extend(s_poles)
        assigned_zeros.extend(s_zeros)
        
        # 1. Draw omega_0 circles ONLY for complex poles!
        # Filter out real poles (where imaginary part is basically zero)
        complex_poles = [p for p in s_poles if abs(p.imag) > 1e-6]
        if len(complex_poles) > 0:
            # They are conjugates, so abs() is the same for both. Just take the first one.
            w0 = abs(complex_poles[0])
            fig.add_trace(go.Scattergl(
                x=w0 * np.cos(theta), y=w0 * np.sin(theta),
                mode='lines', line=dict(color=c, dash='dot', width=1.5),
                opacity=0.3, hoverinfo='skip', showlegend=False
            ))
        
        # 2. Draw Dashed Connecting Lines
        if len(s_poles) > 0 and len(s_zeros) > 0:
            
            # Detect the special case: exactly 2 real poles
            is_two_real_poles = len(s_poles) == 2 and all(abs(p.imag) < 1e-6 for p in s_poles)
            
            if is_two_real_poles:
                # Draw a full bipartite graph (both real poles connect to both zeros)
                for p in s_poles:
                    for z in s_zeros:
                        fig.add_trace(go.Scattergl(
                            x=[p.real, z.real], y=[p.imag, z.imag],
                            mode='lines', line=dict(color=c, width=2, dash='dash'), 
                            opacity=0.85, hoverinfo='skip', showlegend=False
                        ))
            else:
                # Standard 1:1 pairing (Complex poles tie cleanly to their respective conjugates)
                unassigned_zeros = list(s_zeros)
                # Sort puts complex poles first (largest imaginary part), real poles last
                sorted_poles = sorted(s_poles, key=lambda x: (-abs(x.imag), x.real))
                
                for p in sorted_poles:
                    if unassigned_zeros:
                        # Tie-breaker bias (+1e-9j) ensures positive poles grab positive zeros
                        closest_z = min(unassigned_zeros, key=lambda z: abs(z - (p + 1e-9j)))
                        unassigned_zeros.remove(closest_z)
                        
                        fig.add_trace(go.Scattergl(
                            x=[p.real, closest_z.real], y=[p.imag, closest_z.imag],
                            mode='lines', line=dict(color=c, width=2, dash='dash'), 
                            opacity=0.85, hoverinfo='skip', showlegend=False
                        ))

        # 3. Draw the ASSIGNED roots with numbers
        add_roots(s_poles, 'x-thin', c, f"Stage {stage_num} Poles", [stage_num] * len(s_poles))
        add_roots(s_zeros, 'circle-open', c, f"Stage {stage_num} Zeros", [stage_num] * len(s_zeros))

    # 4. Draw UNASSIGNED (Orphaned) Roots in Stark Black
    unassigned_p = [p for p in poles if not any(np.isclose(p, ap, atol=1e-5) for ap in assigned_poles)]
    unassigned_z = [z for z in zeros if not any(np.isclose(z, az, atol=1e-5) for az in assigned_zeros)]
    
    add_roots(unassigned_p, 'x-thin', '#000000', "Unassigned Poles", ["!"] * len(unassigned_p))
    add_roots(unassigned_z, 'circle-open', '#000000', "Unassigned Zeros", ["!"] * len(unassigned_z))

    # --- AXIS SCALING & LAYOUT ---
    all_roots = list(poles) + list(zeros)
    if len(all_roots) > 0:
        max_x, max_y = max([abs(r.real) for r in all_roots]), max([abs(r.imag) for r in all_roots])
    else:
        max_x, max_y = 1.0, 1.0

    limit_x = max(max_x, 0.1) * 1.10
    limit_y = max(max_y, 0.1) * 1.15

    x_title = "Real" if scale_type == "Normalized" else f"Real ({unit_label})"
    y_title = "Imaginary" if scale_type == "Normalized" else f"Imaginary ({unit_label})"

    layout_kwargs = dict(
        xaxis_title=x_title, yaxis_title=y_title,
        xaxis=dict(range=[-limit_x, limit_x]),
        yaxis=dict(range=[-limit_y, limit_y]), 
        margin=dict(l=20, r=20, t=30, b=20), height=600, showlegend=False
    )
        
    fig.update_layout(**layout_kwargs)
    
    # Crosshairs
    fig.add_hline(y=0, line_width=1, line_color="black", opacity=0.3)
    fig.add_vline(x=0, line_width=1, line_color="black", opacity=0.3)
    
    return fig