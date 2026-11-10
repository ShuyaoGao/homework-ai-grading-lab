@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 没有找到项目自带的 Python 环境 .venv，请先在本文件夹里运行：
  echo     python -m venv .venv
  echo     .venv\Scripts\pip install -r requirements.txt
  echo 详见 DEMO_GUIDE.md 的“出问题怎么办”。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" run.py --stop
echo.
timeout /t 5
