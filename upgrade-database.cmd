@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0"
set "PYTHONIOENCODING=utf-8"
if not exist ".venv\Scripts\python.exe" (
    echo Python environment missing: .venv\Scripts\python.exe
    echo Install the project dependencies before upgrading the database.
    popd
    pause
    exit /b 1
)
".venv\Scripts\python.exe" "scripts\upgrade_database.py" %*
set "MIGRATION_EXIT=%ERRORLEVEL%"
echo.
if not "%MIGRATION_EXIT%"=="0" echo Database upgrade did not finish successfully.
popd
pause
exit /b %MIGRATION_EXIT%
