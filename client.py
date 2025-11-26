"""
VPS监控客户端脚本
每15分钟向服务器发送系统状态信息
"""
import requests
import psutil
import socket
import json
import time
import platform
from datetime import datetime
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('monitor_client.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# 服务器配置
SERVER_URL = "http://your-server-ip:5000/api/status"  # 修改为你的服务器IP和端口
SERVER_KEY = "your-secret-key"  # 可选：用于身份验证的密钥

def get_system_info():
    """获取系统状态信息"""
    try:
        # CPU使用率
        cpu_percent = psutil.cpu_percent(interval=1)
        
        # 内存信息
        memory = psutil.virtual_memory()
        
        # 磁盘信息（Windows使用C盘）
        disk_path = 'C:\\' if platform.system() == 'Windows' else '/'
        disk = psutil.disk_usage(disk_path)
        
        # 网络信息
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        
        # 系统启动时间
        boot_time = datetime.fromtimestamp(psutil.boot_time()).strftime('%Y-%m-%d %H:%M:%S')
        
        info = {
            "hostname": hostname,
            "local_ip": local_ip,
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "cpu_percent": round(cpu_percent, 2),
            "memory_total_gb": round(memory.total / (1024**3), 2),
            "memory_used_gb": round(memory.used / (1024**3), 2),
            "memory_percent": round(memory.percent, 2),
            "disk_total_gb": round(disk.total / (1024**3), 2),
            "disk_used_gb": round(disk.used / (1024**3), 2),
            "disk_percent": round(disk.percent, 2),
            "boot_time": boot_time,
            "uptime_seconds": int(time.time() - psutil.boot_time())
        }
        
        return info
    except Exception as e:
        logging.error(f"获取系统信息失败: {e}")
        return None

def send_status():
    """发送状态信息到服务器"""
    info = get_system_info()
    if not info:
        logging.error("无法获取系统信息，跳过本次发送")
        return False
    
    try:
        payload = {
            "key": SERVER_KEY,
            "data": info
        }
        
        response = requests.post(
            SERVER_URL,
            json=payload,
            timeout=10
        )
        
        if response.status_code == 200:
            logging.info(f"状态发送成功: {info['timestamp']}")
            return True
        else:
            logging.warning(f"服务器返回错误: {response.status_code} - {response.text}")
            return False
            
    except requests.exceptions.RequestException as e:
        logging.error(f"发送状态失败: {e}")
        return False

def main():
    """主循环"""
    logging.info("监控客户端启动")
    logging.info(f"服务器地址: {SERVER_URL}")
    logging.info("每15分钟发送一次状态信息")
    
    # 立即发送一次
    send_status()
    
    # 每15分钟发送一次
    interval = 15 * 60  # 15分钟 = 900秒
    
    while True:
        try:
            time.sleep(interval)
            send_status()
        except KeyboardInterrupt:
            logging.info("收到停止信号，退出程序")
            break
        except Exception as e:
            logging.error(f"主循环错误: {e}")
            time.sleep(60)  # 出错后等待1分钟再继续

if __name__ == "__main__":
    main()

