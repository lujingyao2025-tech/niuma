@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  if errorlevel 1 goto :error
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements-lock.txt
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed --name NiuMaMail-v0.90.0 --icon "assets\niuma-mail-icon.ico" --add-data "assets;assets" --collect-all PIL --collect-all playwright app.py
if errorlevel 1 goto :error
echo EXE created: dist\NiuMaMail-v0.90.0.exe
start "" "%~dp0dist"
pause
exit /b 0

:error
echo Build failed.
pause
exit /b 1
