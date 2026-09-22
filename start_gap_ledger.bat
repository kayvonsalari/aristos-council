taskkill /F /IM python.exe >nul 2>&1
cd /d "%~dp0"
streamlit run gap_ledger_app.py
