@echo off
chcp 65001 >nul
echo VPS监控客户端启动脚本
echo.
if exist "%~dp0windows-client\nexus-client.exe" (
    start "" "%~dp0windows-client\nexus-client.exe" %*
    exit /b
)
python "%~dp0client.py" %*
pause

