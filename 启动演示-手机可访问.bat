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
title 作业智评 本机演示（手机可访问） - 关闭此窗口即停止服务
echo 正在启动“作业智评”本机演示，允许同一 WiFi 下的手机访问……
echo 第一次运行如果弹出 Windows 防火墙提示，请选“允许专用网络”。
echo.
".venv\Scripts\python.exe" run.py --demo --host 0.0.0.0 %*
echo.
echo 演示服务已停止，可以关闭这个窗口。
pause
