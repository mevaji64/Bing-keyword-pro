@echo off
title Bing Keyword Pro
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel% neq 0 (
  echo Python nahi mila. https://www.python.org/downloads/ se Python 3 install karein ^(Add to PATH tick karein^).
  pause
  exit /b
)
python app.py
pause
