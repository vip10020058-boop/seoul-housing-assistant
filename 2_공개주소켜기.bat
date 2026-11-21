@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0" || goto BAD_DIRECTORY
echo Seoul Ordinance QA - Public URL
echo.

if not exist ".venv\Scripts\python.exe" goto NO_PYTHON
if not exist "start_tunnel.py" goto NO_SCRIPT
".venv\Scripts\python.exe" -X utf8 "start_tunnel.py"
set "TUNNEL_EXIT=%errorlevel%"
echo.
echo Tunnel launcher exited with code %TUNNEL_EXIT%.
pause
exit /b %TUNNEL_EXIT%

:BAD_DIRECTORY
echo [ERROR] Could not open the project folder. Extract the ZIP first.
goto FAIL

:NO_PYTHON
echo [ERROR] Python environment is missing. Run the server batch file first.
goto FAIL

:NO_SCRIPT
echo [ERROR] start_tunnel.py is missing. Copy it next to this batch file.

:FAIL
pause
exit /b 1
