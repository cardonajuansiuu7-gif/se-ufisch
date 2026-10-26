#!/bin/bash
cd "$(dirname "$0")"
echo "Preparando el clonador de voz (solo la primera vez tarda)..."
[ -d venv ] || python3 -m venv venv
source venv/bin/activate
pip install -q -r requirements.txt
(sleep 2; (open http://localhost:5000 || xdg-open http://localhost:5000) >/dev/null 2>&1) &
python app.py
