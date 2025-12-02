"""
VPS监控服务端脚本
接收VPS状态信息并提供Web界面显示
"""
from flask import Flask, request, jsonify, render_template_string
import json
import os
from datetime import datetime, timedelta
import sqlite3
from threading import Lock, Thread
import requests
import time

app = Flask(__name__)

# 配置
DB_FILE = "monitor.db"
SERVER_KEY = "your-secret-key"  # 与客户端保持一致
ALERT_INTERVAL_MINUTES = 20  # 超过20分钟没收到消息视为断联
TIMEZONE_OFFSET_HOURS = 0  # 时区偏移（小时），例如：+8表示东八区，-5表示西五区

# PushPlus推送配置
PUSHPLUS_TOKEN = "your key"
PUSHPLUS_URL = "https://www.pushplus.plus/send"

# 数据库锁
db_lock = Lock()

def init_database():
    """初始化数据库"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 检查表是否存在
    cursor.execute('''
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name='status_log'
    ''')
    table_exists = cursor.fetchone() is not None
    
    if not table_exists:
        # 创建新表
        cursor.execute('''
            CREATE TABLE status_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hostname TEXT NOT NULL,
                local_ip TEXT,
                client_timestamp TEXT NOT NULL,
                server_timestamp TEXT NOT NULL,
                cpu_percent REAL,
                memory_total_gb REAL,
                memory_used_gb REAL,
                memory_percent REAL,
                disk_total_gb REAL,
                disk_used_gb REAL,
                disk_percent REAL,
                boot_time TEXT,
                uptime_seconds INTEGER,
                status TEXT DEFAULT 'online'
            )
        ''')
        cursor.execute('''
            CREATE INDEX idx_server_timestamp ON status_log(server_timestamp DESC)
        ''')
        cursor.execute('''
            CREATE INDEX idx_hostname ON status_log(hostname)
        ''')
    else:
        # 检查是否需要迁移旧数据
        cursor.execute('PRAGMA table_info(status_log)')
        columns = [col[1] for col in cursor.fetchall()]
        
        if 'timestamp' in columns and 'server_timestamp' not in columns:
            # 迁移旧数据：将timestamp改为client_timestamp，received_at改为server_timestamp
            cursor.execute('''
                ALTER TABLE status_log 
                RENAME COLUMN timestamp TO client_timestamp
            ''')
            cursor.execute('''
                ALTER TABLE status_log 
                RENAME COLUMN received_at TO server_timestamp
            ''')
            cursor.execute('DROP INDEX IF EXISTS idx_timestamp')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_server_timestamp 
                ON status_log(server_timestamp DESC)
            ''')
        elif 'server_timestamp' not in columns:
            # 添加新字段
            cursor.execute('''
                ALTER TABLE status_log 
                ADD COLUMN server_timestamp TEXT
            ''')
            cursor.execute('''
                ALTER TABLE status_log 
                ADD COLUMN client_timestamp TEXT
            ''')
            # 迁移数据
            cursor.execute('''
                UPDATE status_log 
                SET server_timestamp = received_at,
                    client_timestamp = timestamp
                WHERE server_timestamp IS NULL
            ''')
            cursor.execute('DROP INDEX IF EXISTS idx_timestamp')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_server_timestamp 
                ON status_log(server_timestamp DESC)
            ''')
    
    # 创建通知记录表（用于避免重复发送通知）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS alert_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hostname TEXT NOT NULL,
            alert_time TEXT NOT NULL,
            alert_type TEXT DEFAULT 'offline',
            sent INTEGER DEFAULT 1,
            UNIQUE(hostname, alert_time)
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_alert_hostname ON alert_log(hostname)
    ''')
    
    conn.commit()
    conn.close()

def insert_status(data):
    """插入状态记录，返回是否是新VPS"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    hostname = data.get('hostname')
    
    # 检查是否是新VPS（首次出现）
    cursor.execute('SELECT COUNT(*) FROM status_log WHERE hostname = ?', (hostname,))
    is_new_vps = cursor.fetchone()[0] == 0
    
    # 使用服务端时间作为主要时间戳
    server_timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    client_timestamp = data.get('timestamp', server_timestamp)
    
    cursor.execute('''
        INSERT INTO status_log (
            hostname, local_ip, client_timestamp, server_timestamp, cpu_percent,
            memory_total_gb, memory_used_gb, memory_percent,
            disk_total_gb, disk_used_gb, disk_percent,
            boot_time, uptime_seconds, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        hostname,
        data.get('local_ip'),
        client_timestamp,
        server_timestamp,
        data.get('cpu_percent'),
        data.get('memory_total_gb'),
        data.get('memory_used_gb'),
        data.get('memory_percent'),
        data.get('disk_total_gb'),
        data.get('disk_used_gb'),
        data.get('disk_percent'),
        data.get('boot_time'),
        data.get('uptime_seconds'),
        'online'
    ))
    
    conn.commit()
    conn.close()
    
    return is_new_vps

def get_all_statuses(limit=1000, page=1, page_size=100, start_date=None, end_date=None, hostname=None):
    """获取所有状态记录，支持分页和日期区间查询"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 构建查询条件
    where_clauses = []
    params = []
    
    if start_date:
        where_clauses.append("COALESCE(server_timestamp, client_timestamp) >= ?")
        params.append(start_date)
    
    if end_date:
        where_clauses.append("COALESCE(server_timestamp, client_timestamp) <= ?")
        params.append(end_date)
    
    if hostname:
        where_clauses.append("hostname = ?")
        params.append(hostname)
    
    where_sql = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    
    # 获取总数
    count_sql = f"SELECT COUNT(*) FROM status_log{where_sql}"
    cursor.execute(count_sql, params)
    total_count = cursor.fetchone()[0]
    
    # 计算分页
    offset = (page - 1) * page_size
    
    # 查询数据
    query_sql = f'''
        SELECT * FROM status_log
        {where_sql}
        ORDER BY COALESCE(server_timestamp, client_timestamp) DESC
        LIMIT ? OFFSET ?
    '''
    params.extend([page_size, offset])
    cursor.execute(query_sql, params)
    
    columns = [description[0] for description in cursor.description]
    results = []
    for row in cursor.fetchall():
        results.append(dict(zip(columns, row)))
    
    conn.close()
    
    return {
        'data': results,
        'total': total_count,
        'page': page,
        'page_size': page_size,
        'total_pages': (total_count + page_size - 1) // page_size if page_size > 0 else 1
    }

def get_chart_data(start_date=None, end_date=None, hostname=None):
    """获取图表数据，按时间顺序显示VPS状态"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 构建查询条件
    where_clauses = []
    params = []
    
    if start_date:
        where_clauses.append("COALESCE(server_timestamp, client_timestamp) >= ?")
        params.append(start_date)
    
    if end_date:
        where_clauses.append("COALESCE(server_timestamp, client_timestamp) <= ?")
        params.append(end_date)
    
    if hostname:
        where_clauses.append("hostname = ?")
        params.append(hostname)
    
    where_sql = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    
    # 查询数据，按时间升序排列（用于图表）
    query_sql = f'''
        SELECT 
            hostname,
            COALESCE(server_timestamp, client_timestamp) as timestamp,
            status
        FROM status_log
        {where_sql}
        ORDER BY COALESCE(server_timestamp, client_timestamp) ASC
        LIMIT 1000
    '''
    cursor.execute(query_sql, params)
    
    results = cursor.fetchall()
    conn.close()
    
    # 按主机名分组
    chart_data = {}
    for row in results:
        hostname_val, timestamp, status = row
        if hostname_val not in chart_data:
            chart_data[hostname_val] = {
                'labels': [],
                'data': [],
                'status': []  # Add status array for color determination
            }
        chart_data[hostname_val]['labels'].append(timestamp)
        chart_data[hostname_val]['data'].append(1)  # Always 1 for bar height
        chart_data[hostname_val]['status'].append(status)  # Store actual status
    
    return chart_data

def get_latest_status_by_hostname():
    """获取每个主机的最新状态，并计算断联时间（基于服务端时间）"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 使用server_timestamp作为主要判断依据
    cursor.execute('''
        SELECT s1.* FROM status_log s1
        INNER JOIN (
            SELECT hostname, MAX(COALESCE(server_timestamp, client_timestamp)) as max_timestamp
            FROM status_log
            GROUP BY hostname
        ) s2 ON s1.hostname = s2.hostname 
        AND COALESCE(s1.server_timestamp, s1.client_timestamp) = s2.max_timestamp
        ORDER BY COALESCE(s1.server_timestamp, s1.client_timestamp) DESC
    ''')
    
    columns = [description[0] for description in cursor.description]
    results = []
    now = datetime.now()
    
    for row in cursor.fetchall():
        status_dict = dict(zip(columns, row))
        
        # 使用server_timestamp计算时间差（如果没有则使用client_timestamp）
        timestamp_key = 'server_timestamp' if status_dict.get('server_timestamp') else 'client_timestamp'
        timestamp_str = status_dict.get(timestamp_key) or status_dict.get('timestamp')
        
        # 兼容旧字段名
        if not timestamp_str:
            timestamp_str = status_dict.get('timestamp') or status_dict.get('received_at')
        
        try:
            if timestamp_str:
                status_time = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
                time_diff = now - status_time
                minutes_diff = time_diff.total_seconds() / 60
                
                # 更新状态和添加时间差信息
                status_dict['status'] = 'offline' if minutes_diff > ALERT_INTERVAL_MINUTES else 'online'
                status_dict['minutes_since_last'] = round(minutes_diff, 1)
                status_dict['display_timestamp'] = timestamp_str  # 用于显示的主要时间戳
            else:
                status_dict['minutes_since_last'] = None
                status_dict['display_timestamp'] = None
        except Exception as e:
            status_dict['minutes_since_last'] = None
            status_dict['display_timestamp'] = timestamp_str
        
        # 确保有client_timestamp和server_timestamp字段
        if 'client_timestamp' not in status_dict:
            status_dict['client_timestamp'] = status_dict.get('timestamp', '')
        if 'server_timestamp' not in status_dict:
            status_dict['server_timestamp'] = status_dict.get('received_at', '')
        
        results.append(status_dict)
    
    conn.close()
    return results

def send_pushplus_notification(title, content):
    """发送PushPlus通知（通用函数）"""
    try:
        # 按照用户要求的URL格式，参数在URL中
        url = f"{PUSHPLUS_URL}?token={PUSHPLUS_TOKEN}&title={requests.utils.quote(title)}&content={requests.utils.quote(content)}&template=html"
        
        # 发送POST请求
        response = requests.post(url, timeout=10)
        
        if response.status_code == 200:
            try:
                result = response.json()
                if result.get('code') == 200:
                    print(f"PushPlus通知发送成功: {title}")
                    return True
                else:
                    print(f"PushPlus通知发送失败: {result.get('msg', '未知错误')}")
                    return False
            except:
                # 如果返回的不是JSON，也认为成功（某些API可能返回纯文本）
                print(f"PushPlus通知发送成功: {title} (HTTP {response.status_code})")
                return True
        else:
            print(f"PushPlus通知请求失败: HTTP {response.status_code}")
            return False
            
    except Exception as e:
        print(f"发送PushPlus通知错误: {e}")
        return False

def send_offline_notification(hostname, minutes_offline):
    """发送PushPlus断联通知"""
    content = f"VPS断联警告\n主机名: {hostname}\n断联时间: {minutes_offline:.1f} 分钟\n检测时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n请及时检查VPS状态！"
    return send_pushplus_notification("warning", content)

def send_startup_notification():
    """发送启动通知"""
    content = f"VPS监控系统已启动\n启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n监控端口: 9000"
    return send_pushplus_notification("监控系统启动", content)

def send_new_vps_notification(hostname, local_ip):
    """发送新增VPS通知"""
    content = f"检测到新VPS上线\n主机名: {hostname}\nIP地址: {local_ip}\n检测时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    return send_pushplus_notification("新VPS上线", content)

def send_delete_vps_notification(hostname, deleted_count):
    """发送删除VPS通知"""
    content = f"VPS已删除\n主机名: {hostname}\n删除记录数: {deleted_count} 条\n删除时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    return send_pushplus_notification("VPS删除", content)

def has_sent_alert_recently(hostname, alert_time_str):
    """检查是否在最近1小时内已发送过通知（避免重复发送）"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 检查1小时内是否已发送过通知
    cursor.execute('''
        SELECT COUNT(*) FROM alert_log
        WHERE hostname = ? 
        AND alert_time >= datetime('now', '-1 hour')
        AND sent = 1
    ''', (hostname,))
    
    count = cursor.fetchone()[0]
    conn.close()
    return count > 0

def record_alert(hostname, alert_time_str):
    """记录已发送的通知"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            INSERT OR IGNORE INTO alert_log (hostname, alert_time, alert_type, sent)
            VALUES (?, ?, ?, ?)
        ''', (hostname, alert_time_str, 'offline', 1))
        conn.commit()
    except Exception as e:
        print(f"记录通知错误: {e}")
    finally:
        conn.close()

def check_connection_status():
    """检查连接状态，更新断联记录（基于服务端时间）"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    latest_statuses = get_latest_status_by_hostname()
    now = datetime.now()
    
    for status in latest_statuses:
        try:
            # 使用server_timestamp作为判断依据
            timestamp_str = status.get('server_timestamp') or status.get('client_timestamp') or status.get('timestamp')
            if not timestamp_str:
                continue
                
            status_time = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
            time_diff = now - status_time
            minutes_diff = time_diff.total_seconds() / 60
            
            old_status = status.get('status', 'online')
            new_status = 'offline' if minutes_diff > ALERT_INTERVAL_MINUTES else 'online'
            
            # 更新该主机的所有最新记录的状态
            cursor.execute('''
                UPDATE status_log
                SET status = ?
                WHERE hostname = ? AND COALESCE(server_timestamp, client_timestamp) = ?
            ''', (new_status, status['hostname'], timestamp_str))
            
            # 如果状态从online变为offline，发送通知
            if old_status == 'online' and new_status == 'offline':
                # 检查是否在最近1小时内已发送过通知
                if not has_sent_alert_recently(status['hostname'], timestamp_str):
                    print(f"检测到VPS断联: {status['hostname']}, 断联时间: {minutes_diff:.1f}分钟")
                    if send_offline_notification(status['hostname'], minutes_diff):
                        # 记录已发送的通知
                        record_alert(status['hostname'], timestamp_str)
                        
        except Exception as e:
            print(f"检查状态错误: {e}")
    
    conn.commit()
    conn.close()

@app.route('/api/status', methods=['POST'])
def receive_status():
    """接收VPS状态信息"""
    try:
        data = request.json
        
        # 验证密钥
        if data.get('key') != SERVER_KEY:
            return jsonify({"error": "Invalid key"}), 401
        
        status_data = data.get('data')
        if not status_data:
            return jsonify({"error": "No data provided"}), 400
        
        # 插入数据库并检测是否是新VPS
        is_new_vps = False
        with db_lock:
            is_new_vps = insert_status(status_data)
        
        # 如果是新VPS，发送通知
        if is_new_vps:
            print(f"检测到新VPS上线: {status_data.get('hostname')}")
            send_new_vps_notification(
                status_data.get('hostname', 'Unknown'),
                status_data.get('local_ip', 'Unknown')
            )
        
        # 接收状态后检查所有VPS的断联情况
        check_connection_status()
        
        return jsonify({"success": True, "message": "Status received"}), 200
        
    except Exception as e:
        print(f"接收状态错误: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/latest', methods=['GET'])
def get_latest():
    """获取最新状态（API）"""
    check_connection_status()
    latest = get_latest_status_by_hostname()
    return jsonify(latest)

@app.route('/api/history', methods=['GET'])
def get_history():
    """获取历史记录（API），支持分页和日期区间查询"""
    limit = request.args.get('limit', 100, type=int)
    page = request.args.get('page', 1, type=int)
    page_size = request.args.get('page_size', limit, type=int)
    start_date = request.args.get('start_date', None)
    end_date = request.args.get('end_date', None)
    hostname = request.args.get('hostname', None)
    
    # 如果提供了日期，确保格式正确
    if start_date and len(start_date) == 10:
        start_date += ' 00:00:00'
    if end_date and len(end_date) == 10:
        end_date += ' 23:59:59'
    
    history = get_all_statuses(limit=limit, page=page, page_size=page_size, 
                               start_date=start_date, end_date=end_date, hostname=hostname)
    return jsonify(history)

@app.route('/api/history/chart', methods=['GET'])
def get_history_chart():
    """获取历史记录图表数据"""
    start_date = request.args.get('start_date', None)
    end_date = request.args.get('end_date', None)
    hostname = request.args.get('hostname', None)
    
    # 如果提供了日期，确保格式正确
    if start_date and len(start_date) == 10:
        start_date += ' 00:00:00'
    if end_date and len(end_date) == 10:
        end_date += ' 23:59:59'
    
    chart_data = get_chart_data(start_date=start_date, end_date=end_date, hostname=hostname)
    return jsonify(chart_data)

@app.route('/api/delete/<path:hostname>', methods=['DELETE', 'POST'])
def delete_vps(hostname):
    """删除指定VPS的所有记录"""
    try:
        from urllib.parse import unquote
        # URL解码hostname
        hostname = unquote(hostname)
        print(f"[删除] 收到删除请求，hostname: {repr(hostname)}")
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # 检查是否存在该VPS的记录
        cursor.execute('SELECT COUNT(*) FROM status_log WHERE hostname = ?', (hostname,))
        count = cursor.fetchone()[0]
        print(f"[删除] 找到 {count} 条记录")
        
        if count == 0:
            conn.close()
            print(f"[删除] VPS不存在: {hostname}")
            return jsonify({"success": False, "error": "VPS不存在"}), 404
        
        # 删除该VPS的所有记录（包括status_log和alert_log）
        cursor.execute('DELETE FROM status_log WHERE hostname = ?', (hostname,))
        deleted_count = cursor.rowcount
        
        # 同时删除该VPS的通知记录
        cursor.execute('DELETE FROM alert_log WHERE hostname = ?', (hostname,))
        alert_deleted = cursor.rowcount
        
        conn.commit()
        conn.close()
        
        print(f"[删除] 成功删除VPS '{hostname}' 的 {deleted_count} 条状态记录和 {alert_deleted} 条通知记录")
        
        # 发送删除成功通知
        send_delete_vps_notification(hostname, deleted_count)
        
        return jsonify({
            "success": True,
            "message": f"已删除VPS '{hostname}' 的 {deleted_count} 条记录"
        }), 200
        
    except Exception as e:
        import traceback
        error_msg = str(e)
        print(f"[删除] 错误: {error_msg}")
        traceback.print_exc()
        return jsonify({"success": False, "error": error_msg}), 500

@app.route('/')
def index():
    """Web界面"""
    return render_template_string(HTML_TEMPLATE)

# HTML模板
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NEXUS MONITOR | VPS监控系统</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700&family=Rajdhani:wght@300;500;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        :root {
            --bg-dark: #050510;
            --card-bg: rgba(20, 20, 35, 0.6);
            --glass-border: rgba(255, 255, 255, 0.1);
            --neon-blue: #00f3ff;
            --neon-pink: #bc13fe;
            --neon-green: #0aff0a;
            --neon-red: #ff003c;
            --text-main: #e0e0e0;
            --text-dim: #8a8a9b;
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            scrollbar-width: thin;
            scrollbar-color: var(--neon-blue) var(--bg-dark);
        }

        body {
            font-family: 'Rajdhani', sans-serif;
            background-color: var(--bg-dark);
            background-image: 
                radial-gradient(circle at 15% 50%, rgba(188, 19, 254, 0.15), transparent 25%),
                radial-gradient(circle at 85% 30%, rgba(0, 243, 255, 0.15), transparent 25%);
            color: var(--text-main);
            min-height: 100vh;
            overflow-x: hidden;
            position: relative;
        }

        /* Cyberpunk Grid Background */
        body::before {
            content: "";
            position: fixed;
            top: 0;
            left: 0;
            width: 200%;
            height: 200%;
            background-image: 
                linear-gradient(rgba(0, 243, 255, 0.03) 1px, transparent 1px),
                linear-gradient(90deg, rgba(0, 243, 255, 0.03) 1px, transparent 1px);
            background-size: 40px 40px;
            transform: perspective(500px) rotateX(60deg) translateY(-100px) translateZ(-200px);
            animation: gridMove 20s linear infinite;
            z-index: -1;
            pointer-events: none;
        }

        @keyframes gridMove {
            0% { transform: perspective(500px) rotateX(60deg) translateY(0) translateZ(-200px); }
            100% { transform: perspective(500px) rotateX(60deg) translateY(40px) translateZ(-200px); }
        }

        .container {
            max-width: 1600px;
            margin: 0 auto;
            padding: 20px;
        }

        /* Glassmorphism Utilities */
        .glass-panel {
            background: var(--card-bg);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border: 1px solid var(--glass-border);
            border-radius: 16px;
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3);
        }

        /* Header */
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 25px 40px;
            margin-bottom: 40px;
            position: relative;
            overflow: hidden;
        }

        .header::after {
            content: '';
            position: absolute;
            bottom: 0;
            left: 0;
            width: 100%;
            height: 2px;
            background: linear-gradient(90deg, transparent, var(--neon-blue), var(--neon-pink), transparent);
        }

        .header h1 {
            font-family: 'Orbitron', sans-serif;
            font-size: 2.5rem;
            font-weight: 700;
            letter-spacing: 2px;
            text-transform: uppercase;
            background: linear-gradient(135deg, #fff, var(--neon-blue));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            text-shadow: 0 0 20px rgba(0, 243, 255, 0.5);
        }

        .header-controls {
            display: flex;
            align-items: center;
            gap: 20px;
        }

        .refresh-info {
            color: var(--text-dim);
            font-size: 0.9rem;
            font-family: 'Orbitron', sans-serif;
        }

        .refresh-info span {
            color: var(--neon-blue);
            font-weight: bold;
        }

        .cyber-btn {
            background: transparent;
            color: var(--neon-blue);
            border: 1px solid var(--neon-blue);
            padding: 10px 25px;
            font-family: 'Orbitron', sans-serif;
            font-size: 0.9rem;
            letter-spacing: 1px;
            cursor: pointer;
            transition: all 0.3s ease;
            position: relative;
            overflow: hidden;
            text-transform: uppercase;
        }

        .cyber-btn:hover {
            background: var(--neon-blue);
            color: #000;
            box-shadow: 0 0 20px var(--neon-blue);
        }

        .cyber-btn::before {
            content: '';
            position: absolute;
            top: 0;
            left: -100%;
            width: 100%;
            height: 100%;
            background: linear-gradient(90deg, transparent, rgba(255,255,255,0.4), transparent);
            transition: 0.5s;
        }

        .cyber-btn:hover::before {
            left: 100%;
        }

        /* Status Grid */
        .status-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(350px, 1fr));
            gap: 30px;
            margin-bottom: 40px;
        }

        .status-card {
            padding: 25px;
            transition: all 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275);
            position: relative;
            overflow: hidden;
        }

        .status-card:hover {
            transform: translateY(-10px) scale(1.02);
            box-shadow: 0 15px 40px rgba(0, 0, 0, 0.4);
            border-color: rgba(255, 255, 255, 0.3);
        }

        .status-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 4px;
            height: 100%;
            background: var(--text-dim);
            transition: 0.3s;
        }

        .status-card.online::before { background: var(--neon-green); box-shadow: 0 0 10px var(--neon-green); }
        .status-card.offline::before { background: var(--neon-red); box-shadow: 0 0 10px var(--neon-red); }

        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 20px;
            padding-bottom: 15px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        }

        .hostname {
            font-family: 'Orbitron', sans-serif;
            font-size: 1.4rem;
            font-weight: 700;
            color: #fff;
            margin-bottom: 5px;
        }

        .ip-address {
            color: var(--text-dim);
            font-size: 0.9rem;
            display: flex;
            align-items: center;
            gap: 5px;
        }

        .status-badge {
            padding: 5px 12px;
            border-radius: 4px;
            font-family: 'Orbitron', sans-serif;
            font-size: 0.7rem;
            font-weight: 700;
            letter-spacing: 1px;
            text-transform: uppercase;
        }

        .status-badge.online {
            background: rgba(10, 255, 10, 0.1);
            color: var(--neon-green);
            border: 1px solid var(--neon-green);
            box-shadow: 0 0 10px rgba(10, 255, 10, 0.2);
        }

        .status-badge.offline {
            background: rgba(255, 0, 60, 0.1);
            color: var(--neon-red);
            border: 1px solid var(--neon-red);
            box-shadow: 0 0 10px rgba(255, 0, 60, 0.2);
        }

        .card-body {
            display: flex;
            flex-direction: column;
            gap: 15px;
        }

        .metric-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 5px;
        }

        .metric-label {
            color: var(--text-dim);
            font-size: 0.9rem;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .metric-value {
            color: #fff;
            font-weight: 600;
            font-family: 'Orbitron', sans-serif;
        }

        .progress-container {
            height: 6px;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 3px;
            overflow: hidden;
            position: relative;
        }

        .progress-bar {
            height: 100%;
            border-radius: 3px;
            position: relative;
            transition: width 1s cubic-bezier(0.4, 0, 0.2, 1);
            overflow: hidden;
        }

        .progress-bar::after {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            bottom: 0;
            width: 50px;
            background: linear-gradient(90deg, transparent, rgba(255,255,255,0.8), transparent);
            transform: skewX(-20deg);
            animation: shimmer 2s infinite linear;
        }

        @keyframes shimmer {
            0% { left: -50px; }
            100% { left: 100%; }
        }

        .progress-cpu { background: linear-gradient(90deg, #00c6ff, #0072ff); box-shadow: 0 0 10px rgba(0, 198, 255, 0.5); }
        .progress-mem { background: linear-gradient(90deg, #f093fb, #f5576c); box-shadow: 0 0 10px rgba(240, 147, 251, 0.5); }
        .progress-disk { background: linear-gradient(90deg, #f83600, #f9d423); box-shadow: 0 0 10px rgba(248, 54, 0, 0.5); }

        .info-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 15px;
            margin-top: 15px;
            padding-top: 15px;
            border-top: 1px solid rgba(255, 255, 255, 0.05);
        }

        .mini-stat {
            display: flex;
            flex-direction: column;
        }

        .mini-label {
            font-size: 0.75rem;
            color: var(--text-dim);
            margin-bottom: 2px;
        }

        .mini-value {
            font-size: 0.9rem;
            color: var(--text-main);
        }

        .delete-btn {
            width: 100%;
            margin-top: 20px;
            padding: 8px;
            background: rgba(255, 0, 60, 0.1);
            border: 1px solid rgba(255, 0, 60, 0.3);
            color: var(--neon-red);
            border-radius: 4px;
            cursor: pointer;
            font-family: 'Rajdhani', sans-serif;
            font-weight: 600;
            transition: all 0.3s;
            opacity: 0.7;
        }

        .delete-btn:hover {
            background: var(--neon-red);
            color: #fff;
            opacity: 1;
            box-shadow: 0 0 15px var(--neon-red);
        }

        /* Charts & History */
        .section-title {
            font-family: 'Orbitron', sans-serif;
            font-size: 1.5rem;
            color: #fff;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .section-title i {
            color: var(--neon-pink);
        }

        .chart-container {
            padding: 20px;
            margin-bottom: 40px;
        }

        .history-container {
            padding: 20px;
            overflow-x: auto;
        }

        /* Filter Controls */
        .filter-controls {
            display: flex;
            gap: 15px;
            margin-bottom: 25px;
            flex-wrap: wrap;
        }

        .cyber-input {
            background: rgba(0, 0, 0, 0.3);
            border: 1px solid var(--glass-border);
            color: #fff;
            padding: 10px 15px;
            border-radius: 4px;
            font-family: 'Rajdhani', sans-serif;
            outline: none;
            transition: 0.3s;
        }

        .cyber-input:focus {
            border-color: var(--neon-blue);
            box-shadow: 0 0 10px rgba(0, 243, 255, 0.2);
        }

        /* Table */
        table {
            width: 100%;
            border-collapse: separate;
            border-spacing: 0 8px;
        }

        th {
            text-align: left;
            padding: 15px;
            color: var(--neon-blue);
            font-family: 'Orbitron', sans-serif;
            font-size: 0.9rem;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        td {
            padding: 15px;
            background: rgba(255, 255, 255, 0.02);
            color: var(--text-main);
            border-top: 1px solid transparent;
            border-bottom: 1px solid transparent;
        }

        tr td:first-child { border-top-left-radius: 8px; border-bottom-left-radius: 8px; border-left: 1px solid transparent; }
        tr td:last-child { border-top-right-radius: 8px; border-bottom-right-radius: 8px; border-right: 1px solid transparent; }

        tr:hover td {
            background: rgba(255, 255, 255, 0.05);
            border-color: rgba(255, 255, 255, 0.1);
            color: #fff;
        }

        /* Modal */
        .modal {
            display: none;
            position: fixed;
            z-index: 1000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(0, 0, 0, 0.8);
            backdrop-filter: blur(5px);
        }

        .modal-content {
            background: #1a1a2e;
            border: 1px solid var(--neon-red);
            box-shadow: 0 0 30px rgba(255, 0, 60, 0.3);
            margin: 15% auto;
            padding: 30px;
            border-radius: 10px;
            width: 400px;
            max-width: 90%;
            position: relative;
        }

        .modal-header {
            font-family: 'Orbitron', sans-serif;
            font-size: 1.2rem;
            color: var(--neon-red);
            margin-bottom: 20px;
            text-transform: uppercase;
        }

        .modal-footer {
            display: flex;
            justify-content: flex-end;
            gap: 15px;
            margin-top: 25px;
        }

        /* Loading */
        .loading {
            text-align: center;
            padding: 40px;
            color: var(--neon-blue);
            font-family: 'Orbitron', sans-serif;
            letter-spacing: 2px;
            animation: pulse 1.5s infinite;
        }

        @keyframes pulse {
            0% { opacity: 0.5; text-shadow: 0 0 5px var(--neon-blue); }
            50% { opacity: 1; text-shadow: 0 0 20px var(--neon-blue); }
            100% { opacity: 0.5; text-shadow: 0 0 5px var(--neon-blue); }
        }

        /* Responsive */
        @media (max-width: 768px) {
            .header {
                flex-direction: column;
                gap: 20px;
                text-align: center;
                padding: 20px;
            }
            
            .header h1 { font-size: 1.8rem; }
            
            .status-grid {
                grid-template-columns: 1fr;
            }
            
            .filter-controls {
                flex-direction: column;
            }
            
            .filter-controls input, .filter-controls select, .filter-controls button {
                width: 100%;
            }
            
            .history-container {
                padding: 10px;
            }
            
            th, td {
                padding: 10px;
                font-size: 0.85rem;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header glass-panel">
            <h1>Nexus Monitor</h1>
            <div class="header-controls">
                <div class="refresh-info">
                    AUTO REFRESH: <span id="autoRefresh">30</span>s
                </div>
                <button class="cyber-btn" onclick="loadData()">
                    <i class="fas fa-sync-alt"></i> REFRESH
                </button>
            </div>
        </div>
        
        <div id="statusGrid" class="status-grid">
            <div class="loading">INITIALIZING SYSTEM...</div>
        </div>
        
        <div class="chart-container glass-panel">
            <h2 class="section-title"><i class="fas fa-chart-line"></i> SYSTEM ANALYTICS</h2>
            <canvas id="statusChart" style="max-height: 200px;"></canvas>
        </div>
        
        <div class="history-container glass-panel">
            <h2 class="section-title"><i class="fas fa-history"></i> DATA LOGS</h2>
            <div class="filter-controls">
                <input type="date" id="startDate" class="cyber-input" placeholder="Start Date">
                <input type="date" id="endDate" class="cyber-input" placeholder="End Date">
                <select id="hostnameFilter" class="cyber-input">
                    <option value="">ALL HOSTS</option>
                </select>
                <button class="cyber-btn" onclick="applyFilters()">QUERY</button>
                <button class="cyber-btn" onclick="resetFilters()">RESET</button>
            </div>
            <div id="historyTable">
                <div class="loading">WAITING FOR DATA...</div>
            </div>
            <div class="pagination" id="pagination" style="display: none; margin-top: 20px; justify-content: center; gap: 10px;">
                <button class="cyber-btn" onclick="changePage(-1)" id="prevBtn">PREV</button>
                <span id="pageInfo" style="display: flex; align-items: center; color: var(--text-dim);"></span>
                <button class="cyber-btn" onclick="changePage(1)" id="nextBtn">NEXT</button>
            </div>
        </div>
    </div>
    
    <!-- Delete Modal -->
    <div id="deleteModal" class="modal">
        <div class="modal-content glass-panel">
            <div class="modal-header">WARNING: TERMINATION PROTOCOL</div>
            <div class="modal-body" style="color: #ccc;">
                Are you sure you want to purge all data for host "<span id="deleteHostname" style="color: #fff; font-weight: bold;"></span>"? This action is irreversible.
            </div>
            <div class="modal-footer">
                <button class="cyber-btn" onclick="closeDeleteModal()">CANCEL</button>
                <button class="cyber-btn" style="border-color: var(--neon-red); color: var(--neon-red);" onclick="confirmDelete()">CONFIRM PURGE</button>
            </div>
        </div>
    </div>
    
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <script>
        // Global Variables
        let autoRefreshInterval;
        let countdown = 30;
        let currentPage = 1;
        let currentPageSize = 50;
        let currentStartDate = null;
        let currentEndDate = null;
        let currentHostname = null;
        let statusChart = null;
        let allHostnames = [];
        let deleteTargetHostname = null;

        // Utility Functions
        function formatBytes(bytes) {
            if (bytes === 0) return '0 GB';
            return bytes.toFixed(2) + ' GB';
        }

        function formatUptime(seconds) {
            const days = Math.floor(seconds / 86400);
            const hours = Math.floor((seconds % 86400) / 3600);
            const minutes = Math.floor((seconds % 3600) / 60);
            return `${days}d ${hours}h ${minutes}m`;
        }

        function formatTime(timeStr) {
            return timeStr || '-';
        }

        // Chart Configuration
        Chart.defaults.color = '#8a8a9b';
        Chart.defaults.borderColor = 'rgba(255, 255, 255, 0.1)';
        Chart.defaults.font.family = "'Rajdhani', sans-serif";

        // Main Logic
        function loadLatestStatus() {
            fetch('/api/latest')
                .then(response => response.json())
                .then(data => {
                    const grid = document.getElementById('statusGrid');
                    if (data.length === 0) {
                        grid.innerHTML = '<div class="loading">NO ACTIVE NODES DETECTED</div>';
                        return;
                    }
                    
                    grid.innerHTML = data.map(status => {
                        const isOffline = status.status === 'offline';
                        const statusClass = isOffline ? 'offline' : 'online';
                        
                        return `
                            <div class="status-card glass-panel ${statusClass}">
                                <div class="card-header">
                                    <div>
                                        <div class="hostname">${status.hostname}</div>
                                        <div class="ip-address"><i class="fas fa-network-wired"></i> ${status.local_ip || 'Unknown'}</div>
                                    </div>
                                    <div class="status-badge ${statusClass}">
                                        ${isOffline ? 'DISCONNECTED' : 'ONLINE'}
                                    </div>
                                </div>
                                
                                <div class="card-body">
                                    <div class="metric-group">
                                        <div class="metric-row">
                                            <span class="metric-label"><i class="fas fa-microchip"></i> CPU Load</span>
                                            <span class="metric-value">${status.cpu_percent || 0}%</span>
                                        </div>
                                        <div class="progress-container">
                                            <div class="progress-bar progress-cpu" style="width: ${status.cpu_percent || 0}%"></div>
                                        </div>
                                    </div>

                                    <div class="metric-group">
                                        <div class="metric-row">
                                            <span class="metric-label"><i class="fas fa-memory"></i> Memory</span>
                                            <span class="metric-value">${status.memory_percent || 0}%</span>
                                        </div>
                                        <div class="progress-container">
                                            <div class="progress-bar progress-mem" style="width: ${status.memory_percent || 0}%"></div>
                                        </div>
                                        <div style="text-align: right; font-size: 0.75rem; color: var(--text-dim); margin-top: 2px;">
                                            ${formatBytes(status.memory_used_gb)} / ${formatBytes(status.memory_total_gb)}
                                        </div>
                                    </div>

                                    <div class="metric-group">
                                        <div class="metric-row">
                                            <span class="metric-label"><i class="fas fa-hdd"></i> Disk</span>
                                            <span class="metric-value">${status.disk_percent || 0}%</span>
                                        </div>
                                        <div class="progress-container">
                                            <div class="progress-bar progress-disk" style="width: ${status.disk_percent || 0}%"></div>
                                        </div>
                                        <div style="text-align: right; font-size: 0.75rem; color: var(--text-dim); margin-top: 2px;">
                                            ${formatBytes(status.disk_used_gb)} / ${formatBytes(status.disk_total_gb)}
                                        </div>
                                    </div>

                                    <div class="info-grid">
                                        <div class="mini-stat">
                                            <span class="mini-label">LAST SEEN</span>
                                            <span class="mini-value" style="color: var(--neon-blue)">
                                                ${formatTime(status.server_timestamp || status.display_timestamp || status.timestamp)}
                                            </span>
                                        </div>
                                        <div class="mini-stat">
                                            <span class="mini-label">UPTIME</span>
                                            <span class="mini-value">${formatUptime(status.uptime_seconds || 0)}</span>
                                        </div>
                                    </div>

                                    ${status.minutes_since_last !== undefined && status.minutes_since_last !== null ? `
                                    <div style="margin-top: 10px; font-size: 0.8rem; text-align: center; color: ${status.minutes_since_last > 20 ? 'var(--neon-red)' : 'var(--neon-green)'}">
                                        <i class="fas fa-clock"></i> Last signal: ${Math.round(status.minutes_since_last)} min ago
                                    </div>
                                    ` : ''}

                                    <button class="delete-btn" data-hostname="${(status.hostname || '').replace(/"/g, '&quot;')}" onclick="handleDeleteClick(this)" type="button">
                                        <i class="fas fa-trash-alt"></i> PURGE NODE
                                    </button>
                                </div>
                            </div>
                        `;
                    }).join('');
                })
                .catch(error => {
                    console.error('Load failed:', error);
                    document.getElementById('statusGrid').innerHTML = '<div class="loading" style="color: var(--neon-red)">SYSTEM ERROR: CONNECTION FAILED</div>';
                });
        }

        function loadHistory(page = 1) {
            currentPage = page;
            const params = new URLSearchParams({
                page: page,
                page_size: currentPageSize
            });
            
            if (currentStartDate) params.append('start_date', currentStartDate);
            if (currentEndDate) params.append('end_date', currentEndDate);
            if (currentHostname) params.append('hostname', currentHostname);
            
            fetch(`/api/history?${params}`)
                .then(response => response.json())
                .then(result => {
                    const table = document.getElementById('historyTable');
                    const pagination = document.getElementById('pagination');
                    
                    if (!result.data || result.data.length === 0) {
                        table.innerHTML = '<div class="loading" style="font-size: 1rem;">NO LOGS FOUND</div>';
                        pagination.style.display = 'none';
                        return;
                    }
                    
                    pagination.style.display = 'flex';
                    document.getElementById('pageInfo').textContent = `PAGE ${result.page} / ${result.total_pages}`;
                    document.getElementById('prevBtn').disabled = result.page <= 1;
                    document.getElementById('nextBtn').disabled = result.page >= result.total_pages;
                    
                    table.innerHTML = `
                        <table>
                            <thead>
                                <tr>
                                    <th>SERVER TIME</th>
                                    <th>HOST</th>
                                    <th>IP</th>
                                    <th>STATUS</th>
                                    <th>CPU</th>
                                    <th>MEM</th>
                                    <th>DISK</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${result.data.map(status => {
                                    const serverTime = formatTime(status.server_timestamp || status.display_timestamp || status.timestamp || status.received_at);
                                    return `
                                    <tr>
                                        <td style="color: var(--neon-blue); font-weight: 500;">${serverTime}</td>
                                        <td style="color: #fff; font-weight: bold;">${status.hostname}</td>
                                        <td>${status.local_ip || '-'}</td>
                                        <td>
                                            <span style="color: ${status.status === 'offline' ? 'var(--neon-red)' : 'var(--neon-green)'}">
                                                ${status.status === 'offline' ? 'OFFLINE' : 'ONLINE'}
                                            </span>
                                        </td>
                                        <td>${status.cpu_percent || 0}%</td>
                                        <td>${status.memory_percent || 0}%</td>
                                        <td>${status.disk_percent || 0}%</td>
                                    </tr>
                                `;
                                }).join('')}
                            </tbody>
                        </table>
                    `;
                });
        }

        function loadChart() {
            const params = new URLSearchParams();
            if (currentStartDate) params.append('start_date', currentStartDate);
            if (currentEndDate) params.append('end_date', currentEndDate);
            if (currentHostname) params.append('hostname', currentHostname);
            
            fetch(`/api/history/chart?${params}`)
                .then(response => response.json())
                .then(chartData => {
                    const ctx = document.getElementById('statusChart').getContext('2d');
                    if (statusChart) statusChart.destroy();
                    
                    const datasets = [];
                    // Base colors for different hosts (used for online status)
                    const onlineColors = ['#0aff0a', '#00f3ff', '#bc13fe', '#f9d423', '#00c6ff'];
                    const offlineColor = '#ff003c'; // Red for offline/disconnected
                    
                    let colorIndex = 0;
                    for (const [hostname, data] of Object.entries(chartData)) {
                        const baseColor = onlineColors[colorIndex % onlineColors.length];
                        // Use status array to determine colors (online=green/base, offline=red)
                        const statusArr = data.status || [];
                        const bgColors = statusArr.map(s => s === 'online' ? baseColor : offlineColor);
                        const borderColors = statusArr.map(s => s === 'online' ? baseColor : offlineColor);
                        
                        datasets.push({
                            label: hostname,
                            data: data.data,
                            backgroundColor: bgColors,
                            borderColor: borderColors,
                            borderWidth: 1,
                            pointBackgroundColor: '#fff',
                            pointRadius: 3,
                            tension: 0.4
                        });
                        colorIndex++;
                    }
                    
                    const labels = datasets.length > 0 ? chartData[Object.keys(chartData)[0]].labels : [];
                    
                    statusChart = new Chart(ctx, {
                        type: 'bar',
                        data: { labels: labels, datasets: datasets },
                        options: {
                            responsive: true,
                            maintainAspectRatio: false,
                            barPercentage: 1.0,
                            categoryPercentage: 1.0,
                            scales: {
                                y: {
                                    beginAtZero: true,
                                    max: 1,
                                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                                    ticks: { 
                                        stepSize: 1,
                                        callback: v => v === 1 ? 'ONLINE' : (v === 0 ? 'OFFLINE' : '') 
                                    }
                                },
                                x: {
                                    grid: { display: false }
                                }
                            },
                            plugins: {
                                legend: { labels: { color: '#fff', font: { family: "'Orbitron', sans-serif" } } },
                                tooltip: {
                                    callbacks: {
                                        label: function(context) {
                                            const datasetIndex = context.datasetIndex;
                                            const index = context.dataIndex;
                                            const hostname = context.dataset.label;
                                            const statusArr = chartData[hostname]?.status || [];
                                            const status = statusArr[index] || 'unknown';
                                            return `${hostname}: ${status === 'online' ? 'ONLINE' : 'OFFLINE'}`;
                                        }
                                    }
                                }
                            }
                        }
                    });
                });
        }

        // Filter & Pagination Logic
        function updateHostnameFilter() {
            fetch('/api/latest')
                .then(response => response.json())
                .then(data => {
                    const select = document.getElementById('hostnameFilter');
                    // Keep current selection if possible
                    const currentVal = select.value;
                    allHostnames = [...new Set(data.map(s => s.hostname))];
                    select.innerHTML = '<option value="">ALL HOSTS</option>' +
                        allHostnames.map(h => `<option value="${h}">${h}</option>`).join('');
                    if(allHostnames.includes(currentVal)) select.value = currentVal;
                });
        }

        function applyFilters() {
            currentStartDate = document.getElementById('startDate').value || null;
            currentEndDate = document.getElementById('endDate').value || null;
            currentHostname = document.getElementById('hostnameFilter').value || null;
            currentPage = 1;
            loadHistory(1);
            loadChart();
        }

        function resetFilters() {
            document.getElementById('startDate').value = '';
            document.getElementById('endDate').value = '';
            document.getElementById('hostnameFilter').value = '';
            currentStartDate = null;
            currentEndDate = null;
            currentHostname = null;
            currentPage = 1;
            loadHistory(1);
            loadChart();
        }

        function changePage(delta) {
            const newPage = currentPage + delta;
            if (newPage >= 1) loadHistory(newPage);
        }

        function loadData() {
            loadLatestStatus();
            loadHistory(currentPage);
            loadChart();
            updateHostnameFilter();
            countdown = 30;
        }

        function startAutoRefresh() {
            autoRefreshInterval = setInterval(() => {
                countdown--;
                document.getElementById('autoRefresh').textContent = countdown;
                if (countdown <= 0) {
                    loadData();
                    countdown = 30;
                }
            }, 1000);
        }

        // Delete Modal Logic
        function showDeleteModal(hostname) {
            hostname = String(hostname).trim();
            deleteTargetHostname = hostname;
            document.getElementById('deleteHostname').textContent = hostname;
            document.getElementById('deleteModal').style.display = 'block';
        }

        function closeDeleteModal() {
            document.getElementById('deleteModal').style.display = 'none';
            deleteTargetHostname = null;
        }

        function handleDeleteClick(button) {
            const hostname = button.getAttribute('data-hostname');
            if (hostname) showDeleteModal(hostname);
        }

        function confirmDelete() {
            if (!deleteTargetHostname) return;
            
            const hostnameEncoded = encodeURIComponent(deleteTargetHostname);
            fetch(`/api/delete/${hostnameEncoded}`, { method: 'DELETE' })
                .then(response => response.json())
                .then(data => {
                    if (data.success !== false) {
                        closeDeleteModal();
                        loadData();
                    } else {
                        alert('Delete failed: ' + data.error);
                    }
                })
                .catch(err => {
                    // Fallback to POST if DELETE fails
                    fetch(`/api/delete/${hostnameEncoded}`, { method: 'POST' })
                        .then(res => res.json())
                        .then(d => {
                            if (d.success !== false) {
                                closeDeleteModal();
                                loadData();
                            } else {
                                alert('Delete failed: ' + d.error);
                            }
                        });
                });
        }

        window.onclick = function(event) {
            if (event.target == document.getElementById('deleteModal')) closeDeleteModal();
        }

        // Init
        loadData();
        startAutoRefresh();
    </script>
</body>
</html>

'''

def background_checker():
    """后台定期检查断联状态"""
    while True:
        try:
            time.sleep(60)  # 每分钟检查一次
            check_connection_status()
        except Exception as e:
            print(f"后台检查错误: {e}")
            time.sleep(60)

if __name__ == '__main__':
    # 初始化数据库
    init_database()
    
    # 启动时检查一次状态
    check_connection_status()
    
    # 发送启动通知
    send_startup_notification()
    
    # 启动后台检查线程
    checker_thread = Thread(target=background_checker, daemon=True)
    checker_thread.start()
    
    print("监控服务器启动")
    print("访问 http://localhost:9000 查看监控界面")
    print("PushPlus通知已启用，断联时将自动发送通知")
    print("按 Ctrl+C 停止服务器")
    
    app.run(host='0.0.0.0', port=9000, debug=False)

