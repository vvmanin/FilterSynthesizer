# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""Single source of truth for the application name and version."""

# 1.1.0 -- Bessel, Equiripple delay, Custom H(s) responses added, LTspice import added, opamp library organized, topology sections solver optimized. 


__version__ = "1.1.0"

APP_NAME = "Filter Synthesizer"     # display: UI headers, PDF reports
APP_SLUG = "FilterSynthesizer"      # filesystem-safe: exe name, %LOCALAPPDATA%