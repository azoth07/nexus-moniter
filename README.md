# Nexus Monitor | VPS 监控系统

一个基于 Python 的赛博朋克风格 Windows Server/VPS 监控系统，支持实时状态监控和多维度 PushPlus 推送告警。

<img width="1690" height="1292" alt="image" src="https://github.com/user-attachments/assets/4725c64b-f7c7-4fae-ab7f-3a05a040e9c1" />

## 🌟 功能特性

- ✅ **实时监控**：每 15 分钟自动采集并发送 CPU、内存、磁盘及系统运行时间。
- ✅ **赛博朋克 UI**：高性能 Web 界面，支持自动刷新、动态图表和历史日志查询。
- ✅ **智能通知系统**：
  - **下线警告**：VPS 断联后即刻推送。
  - **恢复在线**：VPS 重新连接后发送恢复通知。
  - **新机上线**：首次发现新 VPS 接入时推送。
  - **系统管理**：通过 Web 界面删除 VPS 时推送。
- ✅ **智能告警逻辑**：
  - **下线单次提醒**：当 VPS 掉线时，在本次离线周期内仅推送 **1 次**通知，避免重复骚扰。
  - **上线及时提醒**：VPS 恢复在线后即刻发送恢复通知（并重置下线提醒计数）。
- ✅ **资源管理**：自动清理 30 天前的历史数据，防止数据库无限膨胀。

## 🚀 快速开始

### 1. 安装环境
在服务端和客户端 VPS 上均需安装依赖：
```bash
pip install -r requirements.txt
```

### 2. 配置服务端 (server.py)
复制配置文件模板并更名为 `config.json`：
```json
{
    "server_key": "设置你的通信密钥",
    "web_username": "admin",
    "web_password": "设置监控页面登录密码",
    "web_secret_key": "设置浏览器会话签名密钥",
    "pushplus_token": "填入你的 PushPlus Token",
    "alert_interval_minutes": 2,
    "server_port": 9000
}
```
*   **web_username / web_password**: 访问监控页面和查询接口时需要输入的账户密码。
*   **web_secret_key**: 用于浏览器登录会话签名，建议设置为一段随机字符串。
*   **PushPlus Token**: 在 [PushPlus 官网](https://www.pushplus.plus/) 获取。
*   **alert_interval_minutes**: 建议测试设为 2，生产环境建议 15-20。

### 3. 配置客户端 (client.py)
修改 `client.py` 顶部的配置：
```python
SERVER_URL = "http://你的服务端IP:9000/api/status"
SERVER_KEY = "与服务端一致的密钥"
```

### 4. 运行
- **启动服务端**：`python server.py`
- **启动客户端**：`python client.py`

## 🛠️ 测试与调试

### Web 界面测试
在监控页面右上角点击 **[TEST NOTIFY]** 按钮，可以直接测试两种通知类型：
1.  **下线通知测试**：模拟 VPS 离线告警。
2.  **上线通知测试**：模拟 VPS 恢复在线。

### 客户端调试
查看服务端控制台输出，关注带有以下前缀的日志：
- `[检查]`：状态扫描逻辑。
- `[通知]`：PushPlus 发送状态及结果。

## 📂 文件说明

- `server.py`：监控服务端（API & Web 界面）。
- `client.py`：VPS 采集端。
- `monitor.db`：SQLite 数据库（自动创建）。
- `config.json`：系统核心配置文件。

## ⚠️ 注意事项
1. **时区一致性**：系统内部统一使用服务器本地时间，已解决 UTC 时区偏移导致的通知失效问题。
2. **防火墙**：请确保服务端防火墙已放行 9000 端口（或你设置的自定义端口）。
3. **数据安全**：请务必修改默认的 `server_key`。
