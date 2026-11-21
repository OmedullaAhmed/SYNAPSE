@echo off
rem Double-click to open the Stock Mover app in your browser. Close this window to stop it.
cd /d "%~dp0"
python -c "import streamlit, plotly, openpyxl" 2>nul || python -m pip install --quiet streamlit plotly pandas openpyxl
rem The chatbot's Anthropic SDK lives in .\_lib (avoids the Windows long-path limit)
if not exist "_lib\anthropic" (set PIP_USER=0& python -m pip install --quiet --target "_lib" anthropic)
rem Optional: set ANTHROPIC_API_KEY before starting to turn on Claude answers for everyone using this PC
python -m streamlit run app.py --browser.gatherUsageStats false
