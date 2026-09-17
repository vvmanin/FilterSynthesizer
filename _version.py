# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""Single source of truth for the application name and version."""

# 1.0.1 -- bug fixes only, no API or feature changes:
#   * workers no longer re-execute app.py (ImportError: cannot load module
#     more than once per process / BrokenProcessPool)
#   * the engine pool rebuilds itself instead of staying broken for the session
#   * errors carry a traceback and an environment summary
#   * the Response tab refreshes on a BOM pick without a manual rerun

__version__ = "1.0.1"

APP_NAME = "Filter Synthesizer"     # display: UI headers, PDF reports
APP_SLUG = "FilterSynthesizer"      # filesystem-safe: exe name, %LOCALAPPDATA%