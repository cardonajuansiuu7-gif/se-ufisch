@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Preparando el clonador de voz (solo la primera vez tarda)...
if not exist venv (
  python -m venv venv || (echo No se encontro Python. Instalalo desde python.org y marca "Add to PATH". & pause & exit /b)
)
call venv\Scripts\activate.bat
pip install -q -r requirements.txt
start "" http://localhost:5000
python app.py
pause
