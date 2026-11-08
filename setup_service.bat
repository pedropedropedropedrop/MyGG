@echo off
REM MyGG — Windows service installer (uses nssm)
REM Run as Administrator
REM
REM Prerequisites:
REM   - nssm installed and on PATH (https://nssm.cc, or `winget install nssm.nssm`)
REM   - Python venv created at .\venv (see ONBOARDING.md)
REM   - .env populated and the bot tested via `python main.py`
REM
REM This script registers MyGG as a Windows service named "MyGG".
REM Logs are written to .\logs\mygg.log and .\logs\mygg-error.log.

setlocal
set "MYGG_HOME=%~dp0"
REM Strip trailing backslash for cleanliness
if "%MYGG_HOME:~-1%"=="\" set "MYGG_HOME=%MYGG_HOME:~0,-1%"

set "MYGG_PYTHON=%MYGG_HOME%\venv\Scripts\python.exe"
set "MYGG_MAIN=%MYGG_HOME%\main.py"
set "MYGG_LOGS=%MYGG_HOME%\logs"
set "SERVICE_NAME=MyGG"

echo Installing %SERVICE_NAME% as a Windows service via nssm...
echo   Working dir: %MYGG_HOME%

REM Check nssm is installed
where nssm >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo nssm not found on PATH.
    echo Install via:  winget install nssm.nssm
    echo Or download from https://nssm.cc and add to PATH.
    pause
    exit /b 1
)

REM Verify python venv exists
if not exist "%MYGG_PYTHON%" (
    echo.
    echo Python venv not found at: %MYGG_PYTHON%
    echo Create it first:
    echo   python -m venv venv
    echo   venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

REM Create logs directory
if not exist "%MYGG_LOGS%" mkdir "%MYGG_LOGS%"

"%MYGG_PYTHON%" "%MYGG_HOME%\validate.py" || exit /b 1
REM Install the service
nssm install %SERVICE_NAME% "%MYGG_PYTHON%" "%MYGG_MAIN%" || exit /b 1
nssm set %SERVICE_NAME% AppDirectory "%MYGG_HOME%" || exit /b 1
nssm set %SERVICE_NAME% DisplayName "MyGG Personal AI Assistant" || exit /b 1
nssm set %SERVICE_NAME% Description "Self-hosted Telegram personal assistant" || exit /b 1
nssm set %SERVICE_NAME% Start SERVICE_AUTO_START || exit /b 1
nssm set %SERVICE_NAME% AppStdout "%MYGG_LOGS%\mygg.log" || exit /b 1
nssm set %SERVICE_NAME% AppStderr "%MYGG_LOGS%\mygg-error.log" || exit /b 1
nssm set %SERVICE_NAME% AppRotateFiles 1 || exit /b 1
nssm set %SERVICE_NAME% AppRotateBytes 10485760 || exit /b 1
nssm set %SERVICE_NAME% AppExit Default Restart || exit /b 1
nssm set %SERVICE_NAME% AppRestartDelay 5000 || exit /b 1

echo Service installed. To manage MyGG, run:
echo   nssm start %SERVICE_NAME%
echo   nssm stop %SERVICE_NAME%
echo   nssm restart %SERVICE_NAME%
echo   nssm status %SERVICE_NAME%
echo   nssm remove %SERVICE_NAME% confirm
choice /C YN /M "Start MyGG now"
if errorlevel 2 exit /b 0
nssm start %SERVICE_NAME%
exit /b %errorlevel%
