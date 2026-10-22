@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
    python disable_lm_studio_startup.py
) else (
    py -3 disable_lm_studio_startup.py
)
pause
