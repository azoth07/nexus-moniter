"""
Resource usage checker for Nexus Monitor
Check database size, log file size, and data statistics
"""
import os
import sqlite3
import json
from datetime import datetime, timedelta

def format_size(bytes_size):
    """Format bytes to human readable size"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.2f} TB"

def load_config():
    """Load configuration"""
    config_path = 'config.json'
    if not os.path.exists(config_path):
        print("⚠️  config.json not found, using default values")
        return {}
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def check_database():
    """Check database size and statistics"""
    db_file = 'monitor.db'
    
    if not os.path.exists(db_file):
        print(f"❌ Database file not found: {db_file}")
        return
    
    # Get file size
    db_size = os.path.getsize(db_file)
    print(f"\n📊 Database Information")
    print(f"{'='*50}")
    print(f"File: {db_file}")
    print(f"Size: {format_size(db_size)}")
    
    # Connect and get statistics
    try:
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        
        # Total records
        cursor.execute("SELECT COUNT(*) FROM status_log")
        total_records = cursor.fetchone()[0]
        print(f"Total records: {total_records:,}")
        
        # Records by VPS
        cursor.execute("""
            SELECT hostname, COUNT(*) as count 
            FROM status_log 
            GROUP BY hostname 
            ORDER BY count DESC
        """)
        vps_stats = cursor.fetchall()
        print(f"VPS count: {len(vps_stats)}")
        
        # Date range
        cursor.execute("""
            SELECT 
                MIN(COALESCE(server_timestamp, client_timestamp)) as oldest,
                MAX(COALESCE(server_timestamp, client_timestamp)) as newest
            FROM status_log
        """)
        date_range = cursor.fetchone()
        if date_range[0] and date_range[1]:
            oldest = datetime.strptime(date_range[0], '%Y-%m-%d %H:%M:%S')
            newest = datetime.strptime(date_range[1], '%Y-%m-%d %H:%M:%S')
            days = (newest - oldest).days
            print(f"Date range: {date_range[0]} to {date_range[1]} ({days} days)")
        
        # Alert log statistics
        cursor.execute("SELECT COUNT(*) FROM alert_log")
        alert_count = cursor.fetchone()[0]
        print(f"Alert records: {alert_count:,}")
        
        print(f"\n📈 Per-VPS Statistics")
        print(f"{'='*50}")
        for hostname, count in vps_stats[:10]:  # Top 10
            print(f"  {hostname}: {count:,} records")
        if len(vps_stats) > 10:
            print(f"  ... and {len(vps_stats) - 10} more VPS")
        
        conn.close()
        
    except Exception as e:
        print(f"❌ Error reading database: {e}")

def check_log_files():
    """Check client log files"""
    print(f"\n📝 Client Log Files")
    print(f"{'='*50}")
    
    log_files = ['monitor_client.log', 'monitor_client.log.1', 
                 'monitor_client.log.2', 'monitor_client.log.3']
    
    total_size = 0
    found_files = 0
    
    for log_file in log_files:
        if os.path.exists(log_file):
            size = os.path.getsize(log_file)
            total_size += size
            found_files += 1
            print(f"  {log_file}: {format_size(size)}")
    
    if found_files == 0:
        print("  No client log files found (this is normal if you're on server)")
    else:
        print(f"  Total log size: {format_size(total_size)}")
        print(f"  Max expected: 40 MB (with rotation)")

def check_config():
    """Check current configuration"""
    config = load_config()
    
    print(f"\n⚙️  Configuration")
    print(f"{'='*50}")
    
    retention_days = config.get('data_retention_days', 30)
    cleanup_hours = config.get('cleanup_interval_hours', 24)
    
    print(f"Data retention: {retention_days} days")
    print(f"Cleanup interval: {cleanup_hours} hours")
    print(f"Server port: {config.get('server_port', 9000)}")
    print(f"Alert interval: {config.get('alert_interval_minutes', 20)} minutes")

def estimate_growth():
    """Estimate database growth rate"""
    db_file = 'monitor.db'
    
    if not os.path.exists(db_file):
        return
    
    try:
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        
        # Get records in last 24 hours
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("""
            SELECT COUNT(*) FROM status_log 
            WHERE COALESCE(server_timestamp, client_timestamp) >= ?
        """, (yesterday,))
        records_per_day = cursor.fetchone()[0]
        
        # Get VPS count
        cursor.execute("SELECT COUNT(DISTINCT hostname) FROM status_log")
        vps_count = cursor.fetchone()[0]
        
        conn.close()
        
        if records_per_day > 0 and vps_count > 0:
            print(f"\n📈 Growth Estimation")
            print(f"{'='*50}")
            print(f"Records per day: {records_per_day:,}")
            print(f"Records per VPS per day: ~{records_per_day//vps_count if vps_count > 0 else 0}")
            
            # Estimate size
            db_size = os.path.getsize(db_file)
            cursor = sqlite3.connect(db_file).cursor()
            cursor.execute("SELECT COUNT(*) FROM status_log")
            total_records = cursor.fetchone()[0]
            
            if total_records > 0:
                bytes_per_record = db_size / total_records
                config = load_config()
                retention_days = config.get('data_retention_days', 30)
                
                estimated_records = records_per_day * retention_days
                estimated_size = estimated_records * bytes_per_record
                
                print(f"Estimated stable size ({retention_days} days): {format_size(estimated_size)}")
                print(f"Current size: {format_size(db_size)}")
                
                if db_size > estimated_size * 1.5:
                    print("⚠️  Database is larger than expected. Consider manual cleanup.")
        
    except Exception as e:
        print(f"❌ Error estimating growth: {e}")

def main():
    """Main function"""
    print("="*50)
    print("Nexus Monitor - Resource Usage Check")
    print("="*50)
    
    check_config()
    check_database()
    check_log_files()
    estimate_growth()
    
    print(f"\n{'='*50}")
    print("✅ Check completed!")
    print("="*50)

if __name__ == '__main__':
    main()
