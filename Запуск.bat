@echo off
chcp 65001 >nul
cd /d "%~dp0"
where pythonw >nul 2>nul
if errorlevel 1 (
    echo Python не найден. Установите Python с python.org ^(галочка "Add python.exe to PATH"^).
    pause
    exit /b 1
)
python -c "import sv_ttk" >nul 2>nul
if errorlevel 1 (
    echo Устанавливаю тему оформления...
    python -m pip install -r requirements.txt
)
start "" pythonw app.py
