@echo off
rem Double-click to open StockFlow in your browser. Close this window to stop it.
cd /d "%~dp0"
python -c "import streamlit, plotly, openpyxl, scipy" 2>nul || python -m pip install --quiet -r requirements.txt
if not exist "data\demo\sales.csv" python make_demo_data.py
python -m streamlit run app.py --browser.gatherUsageStats false
