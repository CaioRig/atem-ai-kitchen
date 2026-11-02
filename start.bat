@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto venv
for %%P in ("%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "%LOCALAPPDATA%\Python\pythoncore-3.12-64\python.exe") do if exist "%%~P" (
  "%%~P" scripts\bootstrap.py %*
  exit /b
)
where py >nul 2>nul
if not errorlevel 1 (
  py -3.12 -c "import sys" >nul 2>nul
  if not errorlevel 1 goto launcher
)
where python >nul 2>nul
if not errorlevel 1 (
  python -c "import sys; sys.exit(not ((3,11) <= sys.version_info[:2] < (3,13)))" >nul 2>nul
  if not errorlevel 1 goto python
)
if defined LUNA_PYTHON_INSTALL_TRIED goto missing
for %%A in (%*) do if "%%~A"=="--offline" goto missing
for %%A in (%*) do if "%%~A"=="--check" goto missing
where winget >nul 2>nul
if errorlevel 1 goto missing
echo A Luna precisa de Python 3.12. O Winget pode solicitar autorizacao administrativa.
set /p "LUNA_INSTALL=Autoriza instalar Python 3.12 pelo Winget? [s/N] "
if /i not "%LUNA_INSTALL%"=="s" exit /b 1
winget install --id Python.Python.3.12 --exact --source winget
if errorlevel 1 exit /b 1
set "LUNA_PYTHON_INSTALL_TRIED=1"
call "%~f0" %*
exit /b
:venv
".venv\Scripts\python.exe" scripts\bootstrap.py %*
exit /b %errorlevel%
:launcher
py -3.12 scripts\bootstrap.py %*
exit /b %errorlevel%
:python
python scripts\bootstrap.py %*
exit /b %errorlevel%
:missing
echo Instale Python 3.12 de https://www.python.org/downloads/windows/ e execute start.bat novamente.
exit /b 1
