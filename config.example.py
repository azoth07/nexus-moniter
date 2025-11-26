# 配置文件示例
# 复制此文件为 config.py 并修改相应配置

# 服务端配置（server.py使用）
SERVER_KEY = "your-secret-key-here"  # 设置一个安全的密钥
ALERT_INTERVAL_MINUTES = 20  # 超过多少分钟没收到消息视为断联

# 客户端配置（client.py使用）
SERVER_URL = "http://your-server-ip:5000/api/status"  # 修改为你的服务器IP和端口
# SERVER_KEY 需要与服务端保持一致

