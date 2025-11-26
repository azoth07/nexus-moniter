@echo off
chcp 65001 >nul
echo ========================================
echo VPS监控客户端 - Windows服务安装脚本
echo ========================================
echo.

REM 检查是否以管理员身份运行
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [错误] 请以管理员身份运行此脚本！
    pause
    exit /b 1
)

echo 请确保已安装 NSSM (Non-Sucking Service Manager)
echo 下载地址: https://nssm.cc/download
echo.
set /p NSSM_PATH="请输入NSSM.exe的完整路径 (例如: C:\nssm\nssm.exe): "

if not exist "%NSSM_PATH%" (
    echo [错误] NSSM路径不存在！
    pause
    exit /b 1
)

set /p PYTHON_PATH="请输入Python.exe的完整路径 (例如: C:\Python\python.exe): "

if not exist "%PYTHON_PATH%" (
    echo [错误] Python路径不存在！
    pause
    exit /b 1
)

set SCRIPT_DIR=%~dp0
set SCRIPT_PATH=%SCRIPT_DIR%client.py

if not exist "%SCRIPT_PATH%" (
    echo [错误] 找不到client.py文件！
    pause
    exit /b 1
)

echo.
echo 正在安装服务...
"%NSSM_PATH%" install VPSMonitor "%PYTHON_PATH%" "%SCRIPT_PATH%"

if %errorLevel% equ 0 (
    echo.
    echo [成功] 服务安装完成！
    echo.
    echo 服务名称: VPSMonitor
    echo 启动服务: net start VPSMonitor
    echo 停止服务: net stop VPSMonitor
    echo 卸载服务: "%NSSM_PATH%" remove VPSMonitor confirm
    echo.
) else (
    echo [错误] 服务安装失败！
)

pause

