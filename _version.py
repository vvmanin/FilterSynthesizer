# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""Single source of truth for the application name and version."""

# 1.0.2 -- diagnostics + launcher robustness:
#   * FilterSynthesizer.exe --selftest writes a full diagnostic report
#     (environment, sys.path, loaded DLLs, bundle inventory, live pool probe)
#   * no "We can't open this 'http' link" modal when no browser is registered;
#     the URL is always printed instead
#   * console renders ANSI colour instead of escape-code noise

__version__ = "1.0.2"

APP_NAME = "Filter Synthesizer"     # display: UI headers, PDF reports
APP_SLUG = "FilterSynthesizer"      # filesystem-safe: exe name, %LOCALAPPDATA%