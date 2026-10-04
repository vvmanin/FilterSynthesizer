@echo off
REM ========================================================================
REM  FilterSynthesizer bundle build (Windows)
REM  Run from the project root:  build.bat
REM  Output:  dist\FilterSynthesizer\FilterSynthesizer.exe  (+ _internal\ folder beside it)
REM ========================================================================

setlocal ENABLEEXTENSIONS

REM --- 1) sanity: is python available on PATH? ---
where python >nul 2>&1
if errorlevel 1 (
    echo [error] python.exe is not on PATH. Install Python 3.11 or 3.12 first.
    pause
    exit /b 1
)

REM --- 2) create / reuse an isolated build venv -----------------------------
if not exist build_venv (
    echo [info] creating build venv ...
    python -m venv build_venv
)
call build_venv\Scripts\activate.bat

REM --- 3) install pinned build-time deps + the app's runtime deps ----------
echo [info] installing dependencies ...
python -m pip install --upgrade pip
python -m pip install pyinstaller==6.*
python -m pip install -r requirements.txt

REM --- 4) clean any previous build artifacts ------------------------------
if exist build  rmdir /s /q build
if exist dist   rmdir /s /q dist

REM --- 5) bundle ----------------------------------------------------------
echo [info] running PyInstaller ...
pyinstaller --noconfirm --clean FilterSynthesizer.spec
if errorlevel 1 (
    echo [error] PyInstaller failed -- read the messages above.
    pause
    exit /b 1
)

REM --- 6) drop a user-editable SVG folder NEXT TO the EXE ------------------
REM      (the launcher prefers this copy over the bundled fallback)
xcopy /E /I /Y "Section_Schematic_Diagrams" "dist\FilterSynthesizer\Section_Schematic_Diagrams" >nul
REM      Built-in op-amp library, same rule (user parts go to %LOCALAPPDATA%)
copy /Y "opamp_library.json" "dist\FilterSynthesizer\" >nul
REM      LTspice library (op-amp dummies, templates), same rule (user models go to
REM      %LOCALAPPDATA%\FilterSynthesizer\LTspice_Library). Vendor SPICE models
REM      (models\, git-ignored) are never shipped -- only models\README.txt.
REM      robocopy exit codes 0-7 are success; 8+ is a real failure.
robocopy "LTspice_Library" "dist\FilterSynthesizer\LTspice_Library" /E /XD models /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
    echo [error] copying LTspice_Library failed.
    pause
    exit /b 1
)
if not exist "dist\FilterSynthesizer\LTspice_Library\models" mkdir "dist\FilterSynthesizer\LTspice_Library\models"
copy /Y "LTspice_Library\models\README.txt" "dist\FilterSynthesizer\LTspice_Library\models\" >nul

REM --- 7) user documents NEXT TO the EXE -----------------------------------
REM      Committed PDFs only -- they are built at release time with
REM      python docs\manual\tools\build_pdf.py, never here.
set "DOCS_MISSING="
for %%F in (Quick_Start.pdf User_Manual.pdf) do (
    if exist "docs\%%F" (
        copy /Y "docs\%%F" "dist\FilterSynthesizer\" >nul
    ) else (
        echo [warn] docs\%%F not found -- the bundle ships without it.
        set "DOCS_MISSING=1"
    )
)
if defined DOCS_MISSING (
    echo [warn] Build the manuals with:  python docs\manual\tools\build_pdf.py
)

echo.
echo [ok]   build finished.
echo        Double-click  dist\FilterSynthesizer\FilterSynthesizer.exe  to run.
echo        Ship the whole  dist\FilterSynthesizer\  directory (zip it for distribution).
echo.
pause
