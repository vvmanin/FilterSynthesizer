# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""Every widget key / label, session-state key and patch point the harness uses.

When the UI changes, this is the file to update: a renamed key or label makes
tasks_ui fail with "widget not found: <name>" pointing here. Keyless widgets are
addressed by their label (exact match), keyed ones by key.
"""

# ---- sidebar ----------------------------------------------------------------
RESPONSE = "Response"                       # keyless radio
FILTER_TYPE = "widget_filter_type"
FILTER_TYPE_DELAY = "widget_filter_type_delay"
UNIT = "Unit"                               # keyless radio
ORDER = "widget_sym_order"
ORDER_LP, ORDER_HP = "widget_lp_order", "widget_hp_order"
ASYM = "is_asym_checkbox"
FC, F1, F2 = "widget_fc", "widget_f1", "widget_f2"
GAIN = "widget_gain"
ALPHA_LABELS = ("Passband Ripple α_max (dB)", "Passband Attenuation (dB)")   # keyless
AS_LABEL = "Stopband Attenuation A_s (dB)"  # keyless where it has no key
AS_SYM, AS_LOWER, AS_UPPER = "sym_as_val", "as_sl_val", "as_su_val"
MODS = {                                     # keyless checkboxes
    "pb_even": "Passband Even Order Modification",
    "sb_rolloff": "Stopband Rolloff",
    "sb_rolloff_l": "Lower Stopband Rolloff",
    "sb_rolloff_u": "Upper Stopband Rolloff",
    "coincident": "Coincident Stopband Notches",
}
# delay responses (FS-006)
DELAY_ORDER_MODE = "widget_delay_order_mode"          # options: Manual / From specs
DELAY_ANCHOR = "widget_delay_anchor"                  # Corner frequency / Group delay
TAU0 = "widget_tau0"
DELAY_BP_MAP = "widget_delay_bp_map"                  # Delay-preserving / Classic
DELAY_RIPPLE = "widget_delay_ripple"
CRIT_CORNER, CRIT_DELAY = "widget_crit_corner", "widget_crit_delay"
CRIT_INPUT = {"tau_max": "widget_crit_tau_max", "f_min": "widget_crit_f_min",
              "fd": "widget_crit_fd", "eps": "widget_crit_eps", "fs": "widget_crit_fs"}
# Custom H(s) (FS-007)
CUSTOM_MODE = "widget_custom_mode"                    # options[0] complete, [1] prototype
CUSTOM_SCALE = "widget_custom_scale"
CUSTOM_FN = "widget_custom_fn"
CUSTOM_PBDEF = "widget_custom_pbdef"
CUSTOM_BW = "widget_custom_bw"
CUSTOM_GAIN_MODE = "widget_custom_gain_mode"
CUSTOM_FORM = "custom_form"                           # main-area radio
SS_CUSTOM_SPEC, SS_CUSTOM_REV = "_custom_spec", "_custom_rev"

# ---- Biquad Pairing tab -------------------------------------------------------
ABSORB = "Enable 3rd-Order Sections (Absorb 1st-Order Poles)"     # keyless checkbox
GAIN_DIST = "Remaining Gain Distribution"                          # keyless radio
GAIN_DIST_PREFIX = {"even": "Distribute", "equalize": "Equalize",
                    "first": "Apply Remaining Gain to First", "last": "Apply Remaining Gain to Last"}

# ---- Topology tab -------------------------------------------------------------
BATCH = "hw_batch"
OPAMP = "hw_opamp_choice_all"
ENV = {"cmin": "hw_cmin_all", "cmax": "hw_cmax_all", "rmin": "hw_rmin_all",
       "rmax": "hw_rmax_all", "ratio": "hw_ratio_all"}
CSER = "hw_cser_all"
RSER = "hw_rser_all_{}"                                 # E12 / E24 / E48 / E96
R_SERIES = ("E12", "E24", "E48", "E96")
EFFORT = "hw_effort"
SOLVE_ALL = "hw_solve_all"
FAM = "hw_fam_{}"
FAM_PREFIX = {"VCVS": "VCVS", "MFB": "MFB", "AM": "AM"}
DC_CHK, DC_VAL = "hw_dc_chk_{}", "hw_dc_val_{}"
AMEQ = "hw_ameq_{}"
MFB_LS = "hw_mfbls_{}"
MFB_GAINED = "hw_mfbgain_{}"
FO_REAL = "hw_fo_real_{}"                                # 1st-order: Non-inverting / Inverting
SORT = "hw_sortf_{}"
SORT_BY = "Snap cost"
DF = "hw_df_{}"                                          # BOM table selection state

# ---- Resulting Response tab ---------------------------------------------------
MC_RUNS = "resp_runs"
MC_BUTTON = "resp_mc_run"
REPORT_OPTS = ("rep2_cover", "rep2_coeff", "rep2_norm", "rep2_metrics", "rep2_warn",
               "rep2_phgd", "rep2_mc", "rep2_zoom")
REPORT_TITLE = "rep2_title"
REPORT_BUTTON = "report_btn"
SPICE_VS, SPICE_MC_RUNS = "spice_vs", "spice_mc_runs"

# ---- session state --------------------------------------------------------------
SS_SECTIONS = "hw_sections"
SS_ENGINE = "report_engine"
SS_PAIRING = "report_pairing"
SS_SPEC = "report_spec"
SS_SPEC_SHORT = "report_spec_short"
SS_UNASSIGNED = "unassigned_zero_ids"
SS_INCOMPLETE = "incomplete_stage_nums"
SS_JOBS, SS_RESULTS = "hw_jobs", "hw_results"
SS_PICKS = "bom_picks"
SS_REPORT_PDF = "report_pdf"

# ---- patch points (module, attribute) ---------------------------------------------
PATCH_SUBMIT = ("topology_tab", "_proc_submit")         # every section solve goes through it
PATCH_ENGINE_POOL = ("pool_utils", "run_in_pool")       # engine pool -> inline in QA workers
PATCH_FIRST_ORDER = ("first_order_solver", "synthesize_first_order")
PATCH_EXPORT = ("spice_export", "build_export")
PICKS_GLOBAL = ("topology_tab", "_PICKS")               # module-level durable pick store

# ---- st.error texts that are the app refusing a spec it cannot design (it stops) -----
REFUSAL_MESSAGES = (
    r"Band too wide for the delay-preserving bandpass",
    r"This H\(s\) cannot be used",
    r"Mathematical Constraint Violation",
)
# ---- st.error texts that report a spec the design could not meet (it continues) ------
LIMIT_MESSAGES = (
    r"Stopband Attenuation does not meet requirements",
)

# ---- messages that are part of normal operation (regex, case-insensitive) ----------
EXPECTED_MESSAGES = (
    r"Report generation is not available yet",
    r"Pick a BOM for every section",
    r"Finalize the cascade",
    r"still (has|have) no selected BOM",
    r"No realization fits the constraints",
    r"no BOM",
    r"Inter-stage loading",
)
