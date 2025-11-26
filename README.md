# VPS监控系统

Windows Server VPS监控系统，包含客户端和服务端。赛博朋克风格
<img width="1690" height="1292" alt="image" src="https://github.com/user-attachments/assets/4725c64b-f7c7-4fae-ab7f-3a05a040e9c1" />





## 功能特性

- ✅ 每15分钟自动发送VPS状态信息
- ✅ 实时监控CPU、内存、磁盘使用率
- ✅ Web界面显示所有VPS状态
- ✅ 自动检测断联（超过20分钟未收到消息）
- ✅ 历史记录查询

## 安装步骤

### 1. 安装Python依赖

在客户端和服务端都需要安装：

```bash
pip install -r requirements.txt
```

### 2. 配置服务端（server.py）

编辑 `server.py`，修改以下配置：

```python
SERVER_KEY = "your-secret-key"  # 设置一个密钥
ALERT_INTERVAL_MINUTES = 20  # 断联检测时间（分钟）
```

启动服务端：

```bash
python server.py
```

服务端会在 `http://0.0.0.0:5000` 启动，访问该地址查看监控界面。

### 3. 配置客户端（client.py）

编辑 `client.py`，修改以下配置：

```python
SERVER_URL = "http://your-server-ip:5000/api/status"  # 修改为你的服务器IP
SERVER_KEY = "your-secret-key"  # 与服务端保持一致
```

在Windows Server上运行客户端：

```bash
python client.py
```

### 4. 设置Windows服务（可选）

如果需要让客户端在后台运行，可以使用以下方法：

**方法1：使用nssm（推荐）**

1. 下载 nssm：https://nssm.cc/download
2. 安装服务：
```bash
nssm install VPSMonitor "C:\Python\python.exe" "C:\path\to\client.py"
```
3. 启动服务：
```bash
nssm start VPSMonitor
```

**方法2：使用任务计划程序**

1. 打开"任务计划程序"
2. 创建基本任务
3. 触发器：系统启动时
4. 操作：启动程序 `python.exe`
5. 参数：`client.py` 的完整路径
6. 起始于：`client.py` 所在目录

## 文件说明

- `client.py` - 客户端脚本，运行在被监控的VPS上
- `server.py` - 服务端脚本，接收状态并提供Web界面
- `monitor.db` - SQLite数据库，存储所有状态记录（自动创建）
- `monitor_client.log` - 客户端日志文件

## Web界面功能

- **状态卡片**：显示每个VPS的实时状态
- **断联检测**：超过20分钟未收到消息会显示为"断联"
- **历史记录**：按时间顺序显示所有状态记录
- **自动刷新**：每30秒自动刷新数据

## 注意事项

1. 确保防火墙允许5000端口（服务端）
2. 确保客户端能访问服务端的IP和端口
3. 建议使用HTTPS（生产环境）
4. 定期备份 `monitor.db` 数据库文件

