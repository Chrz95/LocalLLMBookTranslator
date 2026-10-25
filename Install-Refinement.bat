@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
    python install_refinement.py
) else (
    py -3 install_refinement.py
)
pause
