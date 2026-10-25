@echo off
cd /d %~dp0
echo Installing dependencies (first run only takes a while)...
python -m pip install -r requirements.txt -q
echo Starting bill analyzer at http://localhost:8501
python -m streamlit run app.py
pause
