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
title 作业智评 本机演示 - 关闭此窗口即停止服务
echo 正在启动“作业智评”本机演示（离线 mock，不联网、不花钱）……
echo 浏览器会自动打开；不要关这个黑色窗口，演示结束后点页面右上角“关闭演示”。
echo.
".venv\Scripts\python.exe" run.py --demo %*
echo.
echo 演示服务已停止，可以关闭这个窗口。
pause
