@echo off
REM ======================================================================
REM  FilterSynthesizer - diagnostic report
REM
REM  Put this file NEXT TO FilterSynthesizer.exe and double-click it.
REM
REM  It starts the program in diagnostic mode instead of opening the app:
REM  it checks the environment, the installed files and the calculation
REM  engine, then writes a text report to your Desktop and tells you the
REM  file name. Nothing on your computer is changed.
REM
REM  It takes about a minute. Please send the resulting file.
REM ======================================================================

cd /d "%~dp0"
"%~dp0FilterSynthesizer.exe" --selftest
