@echo off
setlocal

echo ============================================================
echo  Telegram Trader -- Setup
echo ============================================================
echo.

:: ── 1. Check Python ───────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found.
    echo Install Python 3.11+ from https://python.org then re-run this script.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('python --version') do echo [OK] %%v found

:: ── 2. Install pip dependencies ───────────────────────────────
echo.
echo Installing Python dependencies...
pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: pip install failed. Check your Python/pip installation.
    pause
    exit /b 1
)
echo [OK] Python dependencies installed

:: ── 3. Check if MT5 terminal is already installed ─────────────
echo.
set MT5_EXE=
if exist "%ProgramFiles%\MetaTrader 5\terminal64.exe" (
    set MT5_EXE=%ProgramFiles%\MetaTrader 5\terminal64.exe
) else if exist "%ProgramFiles(x86)%\MetaTrader 5\terminal64.exe" (
    set MT5_EXE=%ProgramFiles(x86)%\MetaTrader 5\terminal64.exe
)

if defined MT5_EXE (
    echo [OK] MT5 terminal already installed at:
    echo      %MT5_EXE%
    goto :env_check
)

:: ── 4. Download and silently install MT5 ──────────────────────
echo MT5 terminal not found. Downloading installer...
set INSTALLER=%TEMP%\mt5setup.exe
powershell -Command "Invoke-WebRequest -Uri 'https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe' -OutFile '%INSTALLER%'" 2>nul
if errorlevel 1 (
    echo Download failed. Install MT5 manually from:
    echo   https://www.metatrader5.com/en/download
    echo Then re-run this script.
    pause
    exit /b 1
)
echo Installing MT5 silently (this may take a minute)...
"%INSTALLER%" /S
if errorlevel 1 (
    echo MT5 silent install failed. Run the installer manually: %INSTALLER%
    pause
    exit /b 1
)
echo [OK] MT5 installed

:: ── 5. Check for .env file ─────────────────────────────────────
:env_check
echo.
if exist ".env" (
    echo [OK] .env file found
) else (
    echo [!] .env file not found.
    echo     Copy .env.example to .env and fill in your credentials:
    echo       copy .env.example .env
)

:: ── Done ───────────────────────────────────────────────────────
echo.
echo ============================================================
echo  Setup complete. Next steps:
echo ============================================================
echo  1. Fill in .env with your credentials (see .env.example)
echo  2. python auth.py         (once -- creates Telegram session)
echo  3. python main.py         (start bot -- DRY_RUN=true by default)
echo ============================================================
echo.
pause
endlocal
