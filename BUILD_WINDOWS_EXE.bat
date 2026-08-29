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
for /f "usebackq delims=" %%i in (`".venv\Scripts\python.exe" -c "import sys;print(sys.base_prefix)"`) do set "PY_BASE=%%i"
if exist "%PY_BASE%\python3.dll" (
  ".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed --name NiuMaMail-v0.91.0 --icon "assets\niuma-mail-icon.ico" --add-data "assets;assets" --add-binary "%PY_BASE%\python3.dll;." --collect-all PIL --collect-all playwright app.py
) else (
  ".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed --name NiuMaMail-v0.91.0 --icon "assets\niuma-mail-icon.ico" --add-data "assets;assets" --collect-all PIL --collect-all playwright app.py
)
if errorlevel 1 goto :error
echo EXE created: dist\NiuMaMail-v0.91.0.exe
start "" "%~dp0dist"
pause
exit /b 0

:error
echo Build failed.
pause
exit /b 1
