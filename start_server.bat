@echo off
chcp 65001 >nul
echo VPS监控服务端启动脚本
echo.
python server.py
pause

