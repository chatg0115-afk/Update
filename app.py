import os
import json
import sqlite3
import threading
import time
import hashlib
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path
import secrets
import string
from werkzeug.utils import secure_filename
from flask import Flask, request, jsonify, render_template_string, send_file, Response
import logging
import requests

# ==========================
# CONFIGURATION
# ==========================
BOT_TOKEN = os.environ.get('BOT_TOKEN', '8419010897:AAGgELVt2Lv5mIjYDrCtW8Fr1IEUuBINTzE')
ADMIN_ID = int(os.environ.get('ADMIN_ID', 6493515910))
API = f"https://api.telegram.org/bot{BOT_TOKEN}"
PORT = int(os.environ.get('PORT', 10000))

# File upload configuration
UPLOAD_FOLDER = Path('uploads')
UPLOAD_FOLDER.mkdir(exist_ok=True)
ALLOWED_EXTENSIONS = {'txt', 'json', 'conf', 'config', 'yaml', 'yml', 'xml', 'ini', 'cfg', 'properties'}

# Maximum file size: 10MB
MAX_CONTENT_LENGTH = 10 * 1024 * 1024

# ==========================
# LOGGING SETUP
# ==========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('file_hosting.log', encoding='utf-8')
    ]
)
logger = logging.getLogger()

# ==========================
# DATABASE
# ==========================
class FileHostingDB:
    def __init__(self):
        self.db_path = '/tmp/file_hosting.db' if 'RENDER' in os.environ else 'file_hosting.db'
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.init_tables()
    
    def init_tables(self):
        cursor = self.conn.cursor()
        
        # Files table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                file_type TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                version TEXT NOT NULL,
                storage_path TEXT UNIQUE NOT NULL,
                public_url TEXT UNIQUE NOT NULL,
                raw_url TEXT UNIQUE NOT NULL,
                download_url TEXT NOT NULL,
                release_notes TEXT,
                checksum TEXT NOT NULL,
                uploader_id INTEGER NOT NULL,
                uploader_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP DEFAULT (datetime('now', '+365 days')),
                is_active BOOLEAN DEFAULT 1,
                download_count INTEGER DEFAULT 0,
                last_download TIMESTAMP
            )
        ''')
        
        # File versions
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS file_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                version TEXT NOT NULL,
                storage_path TEXT NOT NULL,
                raw_url TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Access logs
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS access_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id INTEGER NOT NULL,
                ip_address TEXT,
                user_agent TEXT,
                referrer TEXT,
                action TEXT,
                accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (file_id) REFERENCES files (id)
            )
        ''')
        
        # System stats
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS system_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                total_files INTEGER DEFAULT 0,
                total_size INTEGER DEFAULT 0,
                total_downloads INTEGER DEFAULT 0,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        self.conn.commit()
        logger.info("✅ Database initialized")
    
    def generate_unique_filename(self, original_filename: str) -> str:
        """Generate unique filename with random string"""
        ext = original_filename.rsplit('.', 1)[1] if '.' in original_filename else ''
        random_str = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))
        timestamp = int(time.time())
        
        if ext:
            return f"{timestamp}_{random_str}.{ext}"
        return f"{timestamp}_{random_str}"
    
    def add_file(self, file_data: dict) -> bool:
        """Add new file to database"""
        try:
            cursor = self.conn.cursor()
            
            cursor.execute('''
                INSERT INTO files (
                    filename, original_filename, file_type, file_size, version,
                    storage_path, public_url, raw_url, download_url, release_notes,
                    checksum, uploader_id, uploader_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                file_data['filename'],
                file_data['original_filename'],
                file_data['file_type'],
                file_data['file_size'],
                file_data['version'],
                file_data['storage_path'],
                file_data['public_url'],
                file_data['raw_url'],
                file_data['download_url'],
                file_data['release_notes'],
                file_data['checksum'],
                file_data['uploader_id'],
                file_data['uploader_name']
            ))
            
            # Add to versions
            cursor.execute('''
                INSERT INTO file_versions (filename, version, storage_path, raw_url)
                VALUES (?, ?, ?, ?)
            ''', (
                file_data['original_filename'],
                file_data['version'],
                file_data['storage_path'],
                file_data['raw_url']
            ))
            
            self.conn.commit()
            logger.info(f"✅ File added: {file_data['original_filename']}")
            return True
            
        except Exception as e:
            logger.error(f"Error adding file: {e}")
            return False
    
    def get_file_by_id(self, file_id: int) -> Optional[dict]:
        """Get file by ID"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT id, filename, original_filename, file_type, file_size, version,
                       storage_path, public_url, raw_url, download_url, release_notes,
                       checksum, uploader_name, created_at, download_count, is_active
                FROM files WHERE id = ?
            ''', (file_id,))
            
            row = cursor.fetchone()
            if row:
                return {
                    'id': row[0],
                    'filename': row[1],
                    'original_filename': row[2],
                    'file_type': row[3],
                    'file_size': row[4],
                    'version': row[5],
                    'storage_path': row[6],
                    'public_url': row[7],
                    'raw_url': row[8],
                    'download_url': row[9],
                    'release_notes': row[10],
                    'checksum': row[11],
                    'uploader_name': row[12],
                    'created_at': row[13],
                    'download_count': row[14],
                    'is_active': bool(row[15])
                }
        except Exception as e:
            logger.error(f"Error getting file: {e}")
        
        return None
    
    def get_file_by_filename(self, filename: str) -> Optional[dict]:
        """Get file by filename"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT id, filename, original_filename, file_type, file_size, version,
                       storage_path, public_url, raw_url, download_url, release_notes,
                       checksum, uploader_name, created_at, download_count, is_active
                FROM files WHERE filename = ?
            ''', (filename,))
            
            row = cursor.fetchone()
            if row:
                return {
                    'id': row[0],
                    'filename': row[1],
                    'original_filename': row[2],
                    'file_type': row[3],
                    'file_size': row[4],
                    'version': row[5],
                    'storage_path': row[6],
                    'public_url': row[7],
                    'raw_url': row[8],
                    'download_url': row[9],
                    'release_notes': row[10],
                    'checksum': row[11],
                    'uploader_name': row[12],
                    'created_at': row[13],
                    'download_count': row[14],
                    'is_active': bool(row[15])
                }
        except Exception as e:
            logger.error(f"Error getting file by name: {e}")
        
        return None
    
    def get_all_files(self) -> List[dict]:
        """Get all files"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT id, original_filename, file_type, file_size, version,
                       raw_url, download_url, release_notes, uploader_name,
                       created_at, download_count, is_active
                FROM files ORDER BY created_at DESC
            ''')
            
            files = []
            for row in cursor.fetchall():
                files.append({
                    'id': row[0],
                    'original_filename': row[1],
                    'file_type': row[2],
                    'file_size': row[3],
                    'version': row[4],
                    'raw_url': row[5],
                    'download_url': row[6],
                    'release_notes': row[7],
                    'uploader_name': row[8],
                    'created_at': row[9],
                    'download_count': row[10],
                    'is_active': bool(row[11])
                })
            
            return files
        except Exception as e:
            logger.error(f"Error getting files: {e}")
            return []
    
    def record_download(self, file_id: int, ip: str = None, user_agent: str = None):
        """Record file download"""
        try:
            cursor = self.conn.cursor()
            
            # Update download count
            cursor.execute('''
                UPDATE files 
                SET download_count = download_count + 1, 
                    last_download = CURRENT_TIMESTAMP 
                WHERE id = ?
            ''', (file_id,))
            
            # Log access
            cursor.execute('''
                INSERT INTO access_logs (file_id, ip_address, user_agent, action)
                VALUES (?, ?, ?, ?)
            ''', (file_id, ip, user_agent, 'download'))
            
            self.conn.commit()
        except Exception as e:
            logger.error(f"Error recording download: {e}")
    
    def record_view(self, file_id: int, ip: str = None, user_agent: str = None):
        """Record file view"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT INTO access_logs (file_id, ip_address, user_agent, action)
                VALUES (?, ?, ?, ?)
            ''', (file_id, ip, user_agent, 'view'))
            
            self.conn.commit()
        except Exception as e:
            logger.error(f"Error recording view: {e}")
    
    def get_statistics(self) -> dict:
        """Get system statistics"""
        try:
            cursor = self.conn.cursor()
            
            # Total files
            cursor.execute("SELECT COUNT(*) FROM files")
            total_files = cursor.fetchone()[0]
            
            # Active files
            cursor.execute("SELECT COUNT(*) FROM files WHERE is_active = 1")
            active_files = cursor.fetchone()[0]
            
            # Total downloads
            cursor.execute("SELECT SUM(download_count) FROM files")
            total_downloads = cursor.fetchone()[0] or 0
            
            # Total size
            cursor.execute("SELECT SUM(file_size) FROM files")
            total_size = cursor.fetchone()[0] or 0
            
            # Recent downloads (24h)
            cursor.execute('''
                SELECT COUNT(*) FROM access_logs 
                WHERE action = 'download' AND accessed_at > datetime('now', '-1 day')
            ''')
            daily_downloads = cursor.fetchone()[0]
            
            # Storage usage
            storage_usage = self.get_storage_usage()
            
            return {
                'total_files': total_files,
                'active_files': active_files,
                'total_downloads': total_downloads,
                'daily_downloads': daily_downloads,
                'total_size': total_size,
                'storage_usage': storage_usage,
                'upload_folder': str(UPLOAD_FOLDER.absolute())
            }
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return {}
    
    def get_storage_usage(self) -> dict:
        """Get storage usage statistics"""
        try:
            total_size = 0
            file_count = 0
            
            for file_path in UPLOAD_FOLDER.rglob('*'):
                if file_path.is_file():
                    total_size += file_path.stat().st_size
                    file_count += 1
            
            return {
                'files_count': file_count,
                'total_bytes': total_size,
                'total_mb': round(total_size / (1024 * 1024), 2),
                'folder_path': str(UPLOAD_FOLDER.absolute())
            }
        except Exception as e:
            logger.error(f"Error getting storage usage: {e}")
            return {}
    
    def search_files(self, query: str) -> List[dict]:
        """Search files"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT id, original_filename, file_type, version, release_notes,
                       created_at, download_count, raw_url
                FROM files 
                WHERE is_active = 1 AND (
                    original_filename LIKE ? OR 
                    version LIKE ? OR 
                    release_notes LIKE ?
                )
                ORDER BY created_at DESC
            ''', (f'%{query}%', f'%{query}%', f'%{query}%'))
            
            return [{
                'id': row[0],
                'original_filename': row[1],
                'file_type': row[2],
                'version': row[3],
                'release_notes': row[4],
                'created_at': row[5],
                'download_count': row[6],
                'raw_url': row[7]
            } for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error searching: {e}")
            return []
    
    def get_file_versions(self, filename: str) -> List[dict]:
        """Get file versions"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT version, storage_path, raw_url, created_at 
                FROM file_versions 
                WHERE filename = ? 
                ORDER BY created_at DESC
            ''', (filename,))
            
            return [{
                'version': row[0],
                'storage_path': row[1],
                'raw_url': row[2],
                'created_at': row[3]
            } for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting versions: {e}")
            return []

db = FileHostingDB()

# ==========================
# UTILITY FUNCTIONS
# ==========================
def allowed_file(filename: str) -> bool:
    """Check if file extension is allowed"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def calculate_checksum(file_path: Path) -> str:
    """Calculate SHA256 checksum of file"""
    sha256_hash = hashlib.sha256()
    
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    
    return sha256_hash.hexdigest()

def format_file_size(size: int) -> str:
    """Format file size in human readable format"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"

def telegram_request(method: str, data: Dict = None) -> Optional[Dict]:
    """Send Telegram API request"""
    try:
        url = f"{API}/{method}"
        response = requests.post(url, json=data or {}, timeout=10)
        
        if response.status_code == 200:
            return response.json()
        logger.error(f"API error {response.status_code}: {method}")
    except Exception as e:
        logger.error(f"API request failed: {e}")
    return None

def send_telegram_message(chat_id: int, text: str, parse_mode: str = "HTML") -> bool:
    """Send message to Telegram"""
    try:
        result = telegram_request("sendMessage", {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        })
        return bool(result and result.get("ok"))
    except Exception as e:
        logger.error(f"Send message error: {e}")
        return False

# ==========================
# FLASK APP
# ==========================
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

# ==========================
# TELEGRAM BOT HANDLER
# ==========================
def handle_telegram_bot():
    """Telegram bot polling"""
    offset = 0
    
    logger.info("🤖 Starting File Hosting Bot...")
    
    while True:
        try:
            result = telegram_request("getUpdates", {
                "offset": offset,
                "timeout": 30,
                "allowed_updates": ["message"]
            })
            
            if result and result.get("ok"):
                updates = result.get("result", [])
                
                for update in updates:
                    offset = update["update_id"] + 1
                    
                    if "message" in update:
                        msg = update["message"]
                        chat_id = msg.get("from", {}).get("id")
                        text = msg.get("text", "").strip()
                        user_name = msg.get("from", {}).get("first_name", "User")
                        
                        if not chat_id:
                            continue
                        
                        logger.info(f"📨 Message from {chat_id} ({user_name}): {text[:50]}")
                        
                        # Only admin can use bot
                        if chat_id != ADMIN_ID:
                            send_telegram_message(
                                chat_id,
                                "⛔ <b>Access Denied</b>\n\n"
                                "Only admin can use this bot.\n\n"
                                f"🌐 Use web interface:\nhttps://{request.host if 'request' in locals() else 'your-domain.com'}"
                            )
                            continue
                        
                        # Handle commands
                        if text == "/start":
                            welcome_msg = f"""
📁 <b>FILE HOSTING BOT</b> 📁

Hello Admin! 👋

<b>Commands:</b>
/files - View all files
/upload - Upload instructions
/stats - System statistics
/help - Show help

<b>Web Interface:</b>
• Upload files directly
• Manage stored files
• Get raw download links
• View statistics

<b>Quick Stats:</b>
• Total Files: {len(db.get_all_files())}
• Storage Used: {db.get_storage_usage().get('total_mb', 0)} MB

🌐 <b>Dashboard:</b> https://{request.host if 'request' in locals() else 'your-domain.com'}
                            """
                            
                            keyboard = {
                                "inline_keyboard": [
                                    [
                                        {"text": "🌐 Open Dashboard", "url": f"https://{request.host if 'request' in locals() else 'your-domain.com'}"},
                                        {"text": "📊 View Stats", "callback_data": "view_stats"}
                                    ],
                                    [
                                        {"text": "📁 List Files", "callback_data": "list_files"},
                                        {"text": "🔄 Refresh", "callback_data": "refresh"}
                                    ]
                                ]
                            }
                            
                            telegram_request("sendMessage", {
                                "chat_id": chat_id,
                                "text": welcome_msg,
                                "parse_mode": "HTML",
                                "reply_markup": keyboard
                            })
                        
                        elif text == "/files":
                            files = db.get_all_files()
                            
                            if files:
                                files_list = "\n".join([
                                    f"• <code>{f['original_filename']}</code> (v{f['version']})"
                                    for f in files[:5]
                                ])
                                
                                if len(files) > 5:
                                    files_list += f"\n\n... and {len(files) - 5} more files"
                                
                                message = f"""
📁 <b>STORED FILES</b>

{files_list}

📊 <b>Total:</b> {len(files)} files
💾 <b>Storage:</b> {db.get_storage_usage().get('total_mb', 0)} MB

🌐 <b>Dashboard:</b> https://{request.host if 'request' in locals() else 'your-domain.com'}
                                """
                            else:
                                message = "📭 <b>No files found</b>\n\nUpload your first file via web interface!"
                            
                            send_telegram_message(chat_id, message)
                        
                        elif text == "/upload":
                            message = """
📤 <b>HOW TO UPLOAD FILES</b>

<b>Via Web Interface:</b>
1. Go to https://{host}
2. Click "Upload File"
3. Select file from device
4. Enter version & notes
5. File will be stored

<b>Supported Formats:</b>
• JSON (.json)
• Text (.txt)
• Config (.conf, .config)
• YAML (.yaml, .yml)
• XML (.xml)
• INI (.ini, .cfg)
• Properties (.properties)

<b>Max File Size:</b> 10MB

<b>After Upload:</b>
• File saved to server storage
• Raw download link generated
• Direct access via URL
• Download statistics tracked

🌐 <b>Upload Now:</b> https://{host}/upload
                            """
                            
                            send_telegram_message(chat_id, message)
                        
                        elif text == "/stats":
                            stats = db.get_statistics()
                            storage = db.get_storage_usage()
                            
                            message = f"""
📊 <b>SYSTEM STATISTICS</b>

📁 <b>Files:</b> {stats.get('total_files', 0)} total, {stats.get('active_files', 0)} active
📥 <b>Downloads:</b> {stats.get('total_downloads', 0)} total, {stats.get('daily_downloads', 0)} today
💾 <b>Storage:</b> {storage.get('total_mb', 0)} MB ({storage.get('files_count', 0)} files)

📂 <b>Storage Path:</b>
<code>{storage.get('folder_path', 'N/A')}</code>

🕒 <b>Updated:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

🌐 <b>Dashboard:</b> https://{request.host if 'request' in locals() else 'your-domain.com'}
                            """
                            
                            send_telegram_message(chat_id, message)
                        
                        elif text == "/help":
                            help_msg = """
🆘 <b>HELP & SUPPORT</b>

<b>Bot Commands:</b>
/files - List all files
/upload - Upload instructions
/stats - View statistics
/help - This message

<b>Web Interface Features:</b>
• Direct file upload
• File management
• Raw download links
• Statistics tracking
• Search functionality

<b>API Endpoints:</b>
• Upload: POST /api/upload
• Download: GET /raw/{filename}
• List: GET /api/files
• Stats: GET /api/stats

<b>Need help?</b>
Contact admin for support.
                            """
                            
                            send_telegram_message(chat_id, help_msg)
            
            time.sleep(1)
            
        except Exception as e:
            logger.error(f"Bot polling error: {e}")
            time.sleep(5)

# ==========================
# WEB ROUTES
# ==========================
@app.route('/')
def index():
    """Home page"""
    stats = db.get_statistics()
    recent_files = db.get_all_files()[:5]
    
    html = '''
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>📁 File Hosting - Pastebin Alternative</title>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <style>
            :root {
                --primary: #4361ee;
                --primary-dark: #3a56d4;
                --success: #2ec4b6;
                --warning: #ff9f1c;
                --danger: #e71d36;
                --dark: #1a1a2e;
                --light: #f8f9fa;
                --gray: #6c757d;
                --card-bg: rgba(255, 255, 255, 0.95);
                --shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
                --radius: 12px;
                --transition: all 0.3s ease;
            }
            
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }
            
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                color: var(--dark);
                padding: 20px;
            }
            
            .container {
                max-width: 1200px;
                margin: 0 auto;
            }
            
            /* Header */
            .header {
                background: var(--card-bg);
                backdrop-filter: blur(20px);
                border-radius: var(--radius);
                padding: 30px;
                margin-bottom: 30px;
                box-shadow: var(--shadow);
                border: 1px solid rgba(255, 255, 255, 0.2);
                text-align: center;
            }
            
            .logo {
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 20px;
                margin-bottom: 20px;
            }
            
            .logo-icon {
                width: 60px;
                height: 60px;
                background: linear-gradient(135deg, var(--primary), var(--secondary));
                border-radius: 15px;
                display: flex;
                align-items: center;
                justify-content: center;
                color: white;
                font-size: 28px;
                box-shadow: 0 8px 25px rgba(67, 97, 238, 0.3);
            }
            
            .logo-text h1 {
                font-size: 2.5rem;
                color: var(--dark);
                margin-bottom: 10px;
            }
            
            .logo-text p {
                color: var(--gray);
                font-size: 1.1rem;
            }
            
            /* Stats Cards */
            .stats-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                gap: 20px;
                margin-bottom: 30px;
            }
            
            .stat-card {
                background: var(--card-bg);
                border-radius: var(--radius);
                padding: 25px;
                text-align: center;
                box-shadow: var(--shadow);
                transition: var(--transition);
            }
            
            .stat-card:hover {
                transform: translateY(-5px);
                box-shadow: 0 8px 25px rgba(0, 0, 0, 0.15);
            }
            
            .stat-icon {
                width: 60px;
                height: 60px;
                margin: 0 auto 20px;
                border-radius: 12px;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 24px;
                color: white;
            }
            
            .stat-value {
                font-size: 2.2rem;
                font-weight: 700;
                color: var(--dark);
                margin-bottom: 10px;
            }
            
            .stat-label {
                font-size: 0.9rem;
                color: var(--gray);
                text-transform: uppercase;
                letter-spacing: 1px;
            }
            
            /* Action Buttons */
            .action-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
                gap: 20px;
                margin-bottom: 30px;
            }
            
            .action-card {
                background: var(--card-bg);
                border-radius: var(--radius);
                padding: 30px;
                text-align: center;
                text-decoration: none;
                color: var(--dark);
                box-shadow: var(--shadow);
                transition: var(--transition);
                display: flex;
                flex-direction: column;
                align-items: center;
                gap: 20px;
            }
            
            .action-card:hover {
                transform: translateY(-5px);
                box-shadow: 0 10px 30px rgba(0, 0, 0, 0.2);
                background: var(--primary);
                color: white;
            }
            
            .action-icon {
                width: 70px;
                height: 70px;
                background: linear-gradient(135deg, var(--primary), var(--secondary));
                border-radius: 15px;
                display: flex;
                align-items: center;
                justify-content: center;
                color: white;
                font-size: 30px;
            }
            
            .action-card:hover .action-icon {
                background: white;
                color: var(--primary);
            }
            
            .action-text {
                font-size: 1.2rem;
                font-weight: 600;
            }
            
            .action-desc {
                font-size: 0.9rem;
                opacity: 0.8;
            }
            
            /* Recent Files */
            .recent-files {
                background: var(--card-bg);
                border-radius: var(--radius);
                padding: 30px;
                margin-bottom: 30px;
                box-shadow: var(--shadow);
            }
            
            .section-title {
                font-size: 1.5rem;
                font-weight: 700;
                color: var(--dark);
                margin-bottom: 25px;
                display: flex;
                align-items: center;
                gap: 15px;
            }
            
            .files-table {
                width: 100%;
                border-collapse: collapse;
                overflow: hidden;
                border-radius: 8px;
            }
            
            .files-table th {
                background: rgba(67, 97, 238, 0.1);
                padding: 15px;
                text-align: left;
                font-weight: 600;
                color: var(--primary);
                border-bottom: 2px solid rgba(67, 97, 238, 0.2);
            }
            
            .files-table td {
                padding: 15px;
                border-bottom: 1px solid rgba(0, 0, 0, 0.05);
            }
            
            .files-table tr:hover {
                background: rgba(67, 97, 238, 0.05);
            }
            
            .file-link {
                color: var(--primary);
                text-decoration: none;
                font-weight: 500;
            }
            
            .file-link:hover {
                text-decoration: underline;
            }
            
            .badge {
                display: inline-block;
                padding: 4px 12px;
                border-radius: 20px;
                font-size: 0.8rem;
                font-weight: 600;
            }
            
            .badge-success {
                background: rgba(46, 196, 182, 0.2);
                color: var(--success);
            }
            
            .badge-warning {
                background: rgba(255, 159, 28, 0.2);
                color: var(--warning);
            }
            
            .btn {
                display: inline-block;
                padding: 10px 20px;
                background: var(--primary);
                color: white;
                border: none;
                border-radius: 8px;
                font-weight: 600;
                text-decoration: none;
                cursor: pointer;
                transition: var(--transition);
            }
            
            .btn:hover {
                background: var(--primary-dark);
                transform: translateY(-2px);
            }
            
            .btn-small {
                padding: 6px 12px;
                font-size: 0.85rem;
            }
            
            /* Footer */
            .footer {
                text-align: center;
                padding: 20px;
                color: var(--gray);
                font-size: 0.9rem;
                margin-top: 30px;
            }
            
            /* Responsive */
            @media (max-width: 768px) {
                .container {
                    padding: 10px;
                }
                
                .header {
                    padding: 20px;
                }
                
                .logo {
                    flex-direction: column;
                    text-align: center;
                }
                
                .stats-grid {
                    grid-template-columns: repeat(2, 1fr);
                }
                
                .action-grid {
                    grid-template-columns: 1fr;
                }
                
                .files-table {
                    display: block;
                    overflow-x: auto;
                }
            }
            
            @media (max-width: 480px) {
                .stats-grid {
                    grid-template-columns: 1fr;
                }
                
                .logo-text h1 {
                    font-size: 1.8rem;
                }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <!-- Header -->
            <div class="header">
                <div class="logo">
                    <div class="logo-icon">
                        <i class="fas fa-server"></i>
                    </div>
                    <div class="logo-text">
                        <h1>File Hosting Service</h1>
                        <p>Upload, store, and share configuration files</p>
                    </div>
                </div>
            </div>
            
            <!-- Stats -->
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-icon" style="background: var(--primary);">
                        <i class="fas fa-file-alt"></i>
                    </div>
                    <div class="stat-value">''' + str(stats.get('total_files', 0)) + '''</div>
                    <div class="stat-label">Total Files</div>
                </div>
                
                <div class="stat-card">
                    <div class="stat-icon" style="background: var(--success);">
                        <i class="fas fa-download"></i>
                    </div>
                    <div class="stat-value">''' + str(stats.get('total_downloads', 0)) + '''</div>
                    <div class="stat-label">Total Downloads</div>
                </div>
                
                <div class="stat-card">
                    <div class="stat-icon" style="background: var(--warning);">
                        <i class="fas fa-database"></i>
                    </div>
                    <div class="stat-value">''' + str(stats.get('storage_usage', {}).get('total_mb', 0)) + ''' MB</div>
                    <div class="stat-label">Storage Used</div>
                </div>
                
                <div class="stat-card">
                    <div class="stat-icon" style="background: var(--danger);">
                        <i class="fas fa-bolt"></i>
                    </div>
                    <div class="stat-value">''' + str(stats.get('daily_downloads', 0)) + '''</div>
                    <div class="stat-label">Today's Downloads</div>
                </div>
            </div>
            
            <!-- Action Cards -->
            <div class="action-grid">
                <a href="/upload" class="action-card">
                    <div class="action-icon">
                        <i class="fas fa-upload"></i>
                    </div>
                    <div>
                        <div class="action-text">Upload File</div>
                        <div class="action-desc">Upload config files to server</div>
                    </div>
                </a>
                
                <a href="/files" class="action-card">
                    <div class="action-icon">
                        <i class="fas fa-list"></i>
                    </div>
                    <div>
                        <div class="action-text">Browse Files</div>
                        <div class="action-desc">View all stored files</div>
                    </div>
                </a>
                
                <a href="/search" class="action-card">
                    <div class="action-icon">
                        <i class="fas fa-search"></i>
                    </div>
                    <div>
                        <div class="action-text">Search Files</div>
                        <div class="action-desc">Find files by name or content</div>
                    </div>
                </a>
                
                <a href="/api" class="action-card">
                    <div class="action-icon">
                        <i class="fas fa-code"></i>
                    </div>
                    <div>
                        <div class="action-text">API Documentation</div>
                        <div class="action-desc">Learn how to use our API</div>
                    </div>
                </a>
            </div>
            
            <!-- Recent Files -->
            <div class="recent-files">
                <div class="section-title">
                    <i class="fas fa-history"></i>
                    Recently Uploaded Files
                </div>
                
                <table class="files-table">
                    <thead>
                        <tr>
                            <th>Filename</th>
                            <th>Type</th>
                            <th>Version</th>
                            <th>Size</th>
                            <th>Uploaded</th>
                            <th>Downloads</th>
                            <th>Action</th>
                        </tr>
                    </thead>
                    <tbody>
    '''
    
    if recent_files:
        for file in recent_files:
            file_size = format_file_size(file['file_size'])
            uploaded_date = file['created_at'][:10] if len(file['created_at']) > 10 else file['created_at']
            
            html += f'''
                        <tr>
                            <td>
                                <a href="/file/{file['id']}" class="file-link">
                                    <i class="fas fa-file"></i> {file['original_filename']}
                                </a>
                            </td>
                            <td><span class="badge badge-success">{file['file_type'].upper()}</span></td>
                            <td><span class="badge badge-warning">v{file['version']}</span></td>
                            <td>{file_size}</td>
                            <td>{uploaded_date}</td>
                            <td>{file['download_count']}</td>
                            <td>
                                <a href="{file['raw_url']}" target="_blank" class="btn btn-small">
                                    <i class="fas fa-external-link-alt"></i> Raw
                                </a>
                            </td>
                        </tr>
            '''
    else:
        html += '''
                        <tr>
                            <td colspan="7" style="text-align: center; padding: 40px; color: var(--gray);">
                                <i class="fas fa-folder-open" style="font-size: 3rem; margin-bottom: 20px; opacity: 0.5;"></i>
                                <p>No files uploaded yet</p>
                                <a href="/upload" class="btn" style="margin-top: 20px;">
                                    <i class="fas fa-upload"></i> Upload First File
                                </a>
                            </td>
                        </tr>
        '''
    
    html += '''
                    </tbody>
                </table>
                
                <div style="text-align: center; margin-top: 25px;">
                    <a href="/files" class="btn">
                        <i class="fas fa-list"></i> View All Files
                    </a>
                </div>
            </div>
            
            <!-- Footer -->
            <div class="footer">
                <p>📁 File Hosting Service • Upload and share configuration files • Max file size: 10MB</p>
                <p style="margin-top: 10px;">
                    <a href="/api" style="color: var(--primary); margin: 0 15px;">API</a> •
                    <a href="/stats" style="color: var(--primary); margin: 0 15px;">Statistics</a> •
                    <a href="/help" style="color: var(--primary); margin: 0 15px;">Help</a>
                </p>
            </div>
        </div>
    </body>
    </html>
    '''
    
    return html

@app.route('/upload', methods=['GET', 'POST'])
def upload_file():
    """File upload page"""
    if request.method == 'POST':
        # Check if file was uploaded
        if 'file' not in request.files:
            return render_template_string('''
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="UTF-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                    <title>Upload Error</title>
                    <style>
                        body { font-family: Arial, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                               min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }
                        .error { background: white; padding: 40px; border-radius: 20px; text-align: center; 
                                box-shadow: 0 10px 30px rgba(0,0,0,0.2); max-width: 500px; width: 100%; }
                        .error-icon { font-size: 4rem; color: #e74c3c; margin-bottom: 20px; }
                        h1 { color: #333; margin-bottom: 10px; }
                        p { color: #666; margin-bottom: 30px; }
                        .btn { display: inline-block; background: #4361ee; color: white; padding: 12px 30px; 
                               border-radius: 10px; text-decoration: none; font-weight: bold; margin: 0 10px; }
                    </style>
                </head>
                <body>
                    <div class="error">
                        <div class="error-icon">❌</div>
                        <h1>No File Selected</h1>
                        <p>Please select a file to upload.</p>
                        <div style="margin-top: 30px;">
                            <a href="/upload" class="btn">Try Again</a>
                            <a href="/" class="btn" style="background: #666;">Go Home</a>
                        </div>
                    </div>
                </body>
                </html>
            ''')
        
        file = request.files['file']
        
        # If user does not select file, browser submits empty file
        if file.filename == '':
            return render_template_string('''
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="UTF-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                    <title>Upload Error</title>
                    <style>
                        body { font-family: Arial, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                               min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }
                        .error { background: white; padding: 40px; border-radius: 20px; text-align: center; 
                                box-shadow: 0 10px 30px rgba(0,0,0,0.2); max-width: 500px; width: 100%; }
                        .error-icon { font-size: 4rem; color: #e74c3c; margin-bottom: 20px; }
                        h1 { color: #333; margin-bottom: 10px; }
                        p { color: #666; margin-bottom: 30px; }
                        .btn { display: inline-block; background: #4361ee; color: white; padding: 12px 30px; 
                               border-radius: 10px; text-decoration: none; font-weight: bold; margin: 0 10px; }
                    </style>
                </head>
                <body>
                    <div class="error">
                        <div class="error-icon">❌</div>
                        <h1>No File Selected</h1>
                        <p>Please select a file to upload.</p>
                        <div style="margin-top: 30px;">
                            <a href="/upload" class="btn">Try Again</a>
                            <a href="/" class="btn" style="background: #666;">Go Home</a>
                        </div>
                    </div>
                </body>
                </html>
            ''')
        
        # Check file extension
        if not allowed_file(file.filename):
            return render_template_string(f'''
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="UTF-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                    <title>Upload Error</title>
                    <style>
                        body {{ font-family: Arial, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                               min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }}
                        .error {{ background: white; padding: 40px; border-radius: 20px; text-align: center; 
                                box-shadow: 0 10px 30px rgba(0,0,0,0.2); max-width: 500px; width: 100%; }}
                        .error-icon {{ font-size: 4rem; color: #e74c3c; margin-bottom: 20px; }}
                        h1 {{ color: #333; margin-bottom: 10px; }}
                        p {{ color: #666; margin-bottom: 30px; }}
                        .btn {{ display: inline-block; background: #4361ee; color: white; padding: 12px 30px; 
                               border-radius: 10px; text-decoration: none; font-weight: bold; margin: 0 10px; }}
                    </style>
                </head>
                <body>
                    <div class="error">
                        <div class="error-icon">❌</div>
                        <h1>Invalid File Type</h1>
                        <p>File type not allowed. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}</p>
                        <div style="margin-top: 30px;">
                            <a href="/upload" class="btn">Try Again</a>
                            <a href="/" class="btn" style="background: #666;">Go Home</a>
                        </div>
                    </div>
                </body>
                </html>
            ''')
        
        # Get form data
        version = request.form.get('version', '1.0.0')
        release_notes = request.form.get('release_notes', '')
        
        # Secure filename and generate unique name
        original_filename = secure_filename(file.filename)
        unique_filename = db.generate_unique_filename(original_filename)
        file_type = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else 'txt'
        
        # Save file
        storage_path = UPLOAD_FOLDER / unique_filename
        file.save(storage_path)
        
        # Get file info
        file_size = storage_path.stat().st_size
        checksum = calculate_checksum(storage_path)
        
        # Generate URLs
        base_url = request.host_url.rstrip('/')
        public_url = f"{base_url}file/{unique_filename}"
        raw_url = f"{base_url}raw/{unique_filename}"
        download_url = f"{base_url}download/{unique_filename}"
        
        # Prepare file data
        file_data = {
            'filename': unique_filename,
            'original_filename': original_filename,
            'file_type': file_type,
            'file_size': file_size,
            'version': version,
            'storage_path': str(storage_path),
            'public_url': public_url,
            'raw_url': raw_url,
            'download_url': download_url,
            'release_notes': release_notes,
            'checksum': checksum,
            'uploader_id': ADMIN_ID,
            'uploader_name': 'Admin'
        }
        
        # Save to database
        if db.add_file(file_data):
            # Send success page
            file_size_formatted = format_file_size(file_size)
            
            success_html = f'''
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Upload Successful</title>
                <style>
                    :root {{
                        --primary: #4361ee;
                        --success: #2ec4b6;
                    }}
                    
                    body {{
                        font-family: Arial, sans-serif;
                        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                        min-height: 100vh;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        padding: 20px;
                    }}
                    
                    .success {{
                        background: white;
                        padding: 50px;
                        border-radius: 20px;
                        text-align: center;
                        box-shadow: 0 15px 35px rgba(0,0,0,0.2);
                        max-width: 600px;
                        width: 100%;
                    }}
                    
                    .success-icon {{
                        font-size: 5rem;
                        color: var(--success);
                        margin-bottom: 30px;
                    }}
                    
                    h1 {{
                        color: #333;
                        margin-bottom: 20px;
                        font-size: 2.2rem;
                    }}
                    
                    .file-info {{
                        background: #f8f9fa;
                        border-radius: 12px;
                        padding: 25px;
                        margin: 25px 0;
                        text-align: left;
                    }}
                    
                    .info-row {{
                        display: flex;
                        justify-content: space-between;
                        padding: 12px 0;
                        border-bottom: 1px solid rgba(0,0,0,0.05);
                    }}
                    
                    .info-label {{
                        font-weight: 600;
                        color: #666;
                    }}
                    
                    .info-value {{
                        color: #333;
                        font-family: monospace;
                    }}
                    
                    .url-box {{
                        background: #1a1a2e;
                        color: white;
                        padding: 15px;
                        border-radius: 8px;
                        font-family: monospace;
                        word-break: break-all;
                        margin: 15px 0;
                        text-align: left;
                    }}
                    
                    .btn {{
                        display: inline-block;
                        background: var(--primary);
                        color: white;
                        padding: 14px 35px;
                        border-radius: 10px;
                        text-decoration: none;
                        font-weight: bold;
                        margin: 10px;
                        transition: all 0.3s;
                    }}
                    
                    .btn:hover {{
                        transform: translateY(-3px);
                        box-shadow: 0 5px 15px rgba(67, 97, 238, 0.3);
                    }}
                    
                    .btn-secondary {{
                        background: #666;
                    }}
                </style>
            </head>
            <body>
                <div class="success">
                    <div class="success-icon">✅</div>
                    <h1>File Uploaded Successfully!</h1>
                    
                    <div class="file-info">
                        <div class="info-row">
                            <span class="info-label">Filename:</span>
                            <span class="info-value">{original_filename}</span>
                        </div>
                        <div class="info-row">
                            <span class="info-label">File Type:</span>
                            <span class="info-value">{file_type.upper()}</span>
                        </div>
                        <div class="info-row">
                            <span class="info-label">File Size:</span>
                            <span class="info-value">{file_size_formatted}</span>
                        </div>
                        <div class="info-row">
                            <span class="info-label">Version:</span>
                            <span class="info-value">v{version}</span>
                        </div>
                        <div class="info-row">
                            <span class="info-label">Checksum (SHA256):</span>
                            <span class="info-value" style="font-size: 0.85rem;">{checksum}</span>
                        </div>
                    </div>
                    
                    <h3 style="margin: 25px 0 15px; text-align: left;">Raw URL:</h3>
                    <div class="url-box">{raw_url}</div>
                    
                    <h3 style="margin: 25px 0 15px; text-align: left;">Download URL:</h3>
                    <div class="url-box">{download_url}</div>
                    
                    <div style="margin-top: 40px;">
                        <a href="{raw_url}" target="_blank" class="btn">
                            <i class="fas fa-external-link-alt"></i> Open Raw URL
                        </a>
                        <a href="/upload" class="btn">
                            <i class="fas fa-upload"></i> Upload Another
                        </a>
                        <a href="/" class="btn btn-secondary">
                            <i class="fas fa-home"></i> Go Home
                        </a>
                    </div>
                </div>
            </body>
            </html>
            '''
            
            return success_html
        else:
            # Delete file if database failed
            if storage_path.exists():
                storage_path.unlink()
            
            return render_template_string('''
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="UTF-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                    <title>Upload Error</title>
                    <style>
                        body { font-family: Arial, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                               min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }
                        .error { background: white; padding: 40px; border-radius: 20px; text-align: center; 
                                box-shadow: 0 10px 30px rgba(0,0,0,0.2); max-width: 500px; width: 100%; }
                        .error-icon { font-size: 4rem; color: #e74c3c; margin-bottom: 20px; }
                        h1 { color: #333; margin-bottom: 10px; }
                        p { color: #666; margin-bottom: 30px; }
                        .btn { display: inline-block; background: #4361ee; color: white; padding: 12px 30px; 
                               border-radius: 10px; text-decoration: none; font-weight: bold; margin: 0 10px; }
                    </style>
                </head>
                <body>
                    <div class="error">
                        <div class="error-icon">❌</div>
                        <h1>Upload Failed</h1>
                        <p>Failed to save file information to database. Please try again.</p>
                        <div style="margin-top: 30px;">
                            <a href="/upload" class="btn">Try Again</a>
                            <a href="/" class="btn" style="background: #666;">Go Home</a>
                        </div>
                    </div>
                </body>
                </html>
            ''')
    
    # GET request - show upload form
    html = '''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Upload File - File Hosting</title>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <style>
            :root {
                --primary: #4361ee;
                --primary-dark: #3a56d4;
                --gray: #6c757d;
                --light: #f8f9fa;
                --dark: #1a1a2e;
            }
            
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                padding: 20px;
                display: flex;
                align-items: center;
                justify-content: center;
            }
            
            .upload-container {
                background: white;
                border-radius: 20px;
                box-shadow: 0 15px 35px rgba(0,0,0,0.2);
                max-width: 800px;
                width: 100%;
                overflow: hidden;
            }
            
            .upload-header {
                background: var(--primary);
                color: white;
                padding: 30px;
                text-align: center;
            }
            
            .upload-header h1 {
                font-size: 2.2rem;
                margin-bottom: 10px;
            }
            
            .upload-header p {
                opacity: 0.9;
                font-size: 1.1rem;
            }
            
            .upload-body {
                padding: 40px;
            }
            
            .upload-form {
                max-width: 600px;
                margin: 0 auto;
            }
            
            .form-group {
                margin-bottom: 25px;
            }
            
            .form-label {
                display: block;
                margin-bottom: 10px;
                font-weight: 600;
                color: var(--dark);
                font-size: 1.1rem;
            }
            
            .file-input-wrapper {
                border: 3px dashed rgba(67, 97, 238, 0.3);
                border-radius: 12px;
                padding: 40px;
                text-align: center;
                transition: all 0.3s;
                cursor: pointer;
            }
            
            .file-input-wrapper:hover {
                border-color: var(--primary);
                background: rgba(67, 97, 238, 0.05);
            }
            
            .file-input-wrapper.dragover {
                border-color: var(--primary);
                background: rgba(67, 97, 238, 0.1);
            }
            
            .file-icon {
                font-size: 3.5rem;
                color: var(--primary);
                margin-bottom: 20px;
            }
            
            .file-input-text {
                font-size: 1.2rem;
                color: var(--dark);
                margin-bottom: 10px;
            }
            
            .file-input-subtext {
                color: var(--gray);
                margin-bottom: 20px;
            }
            
            .file-input {
                display: none;
            }
            
            .file-name {
                margin-top: 15px;
                font-weight: 600;
                color: var(--primary);
                word-break: break-all;
            }
            
            .form-input {
                width: 100%;
                padding: 15px;
                border: 2px solid rgba(0,0,0,0.1);
                border-radius: 10px;
                font-size: 1rem;
                transition: all 0.3s;
            }
            
            .form-input:focus {
                outline: none;
                border-color: var(--primary);
                box-shadow: 0 0 0 3px rgba(67, 97, 238, 0.1);
            }
            
            .form-textarea {
                min-height: 120px;
                resize: vertical;
            }
            
            .form-help {
                font-size: 0.9rem;
                color: var(--gray);
                margin-top: 5px;
            }
            
            .upload-actions {
                display: flex;
                gap: 20px;
                margin-top: 40px;
            }
            
            .btn {
                flex: 1;
                padding: 18px;
                border: none;
                border-radius: 10px;
                font-size: 1.1rem;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.3s;
                text-align: center;
                text-decoration: none;
            }
            
            .btn-primary {
                background: var(--primary);
                color: white;
            }
            
            .btn-primary:hover {
                background: var(--primary-dark);
                transform: translateY(-3px);
                box-shadow: 0 5px 15px rgba(67, 97, 238, 0.3);
            }
            
            .btn-secondary {
                background: var(--light);
                color: var(--dark);
                border: 2px solid rgba(0,0,0,0.1);
            }
            
            .btn-secondary:hover {
                background: white;
                transform: translateY(-3px);
            }
            
            .supported-formats {
                background: #f8f9fa;
                border-radius: 12px;
                padding: 20px;
                margin-top: 30px;
            }
            
            .formats-title {
                font-weight: 600;
                color: var(--dark);
                margin-bottom: 10px;
            }
            
            .formats-list {
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
                margin-top: 10px;
            }
            
            .format-badge {
                background: white;
                border: 1px solid rgba(0,0,0,0.1);
                padding: 8px 16px;
                border-radius: 20px;
                font-size: 0.9rem;
                color: var(--dark);
            }
            
            @media (max-width: 768px) {
                .upload-body {
                    padding: 20px;
                }
                
                .upload-actions {
                    flex-direction: column;
                }
                
                .file-input-wrapper {
                    padding: 30px 20px;
                }
            }
        </style>
    </head>
    <body>
        <div class="upload-container">
            <div class="upload-header">
                <h1><i class="fas fa-upload"></i> Upload File</h1>
                <p>Upload configuration files to server storage</p>
            </div>
            
            <div class="upload-body">
                <form class="upload-form" method="POST" enctype="multipart/form-data" id="uploadForm">
                    <!-- File Upload -->
                    <div class="form-group">
                        <label class="form-label">Select File *</label>
                        <div class="file-input-wrapper" id="fileDropArea">
                            <div class="file-icon">
                                <i class="fas fa-cloud-upload-alt"></i>
                            </div>
                            <div class="file-input-text">Choose a file or drag & drop here</div>
                            <div class="file-input-subtext">Max file size: 10MB</div>
                            <input type="file" name="file" id="fileInput" class="file-input" required 
                                   accept=".txt,.json,.conf,.config,.yaml,.yml,.xml,.ini,.cfg,.properties">
                            <label for="fileInput" class="btn btn-secondary" style="display: inline-block; width: auto; padding: 12px 30px;">
                                <i class="fas fa-folder-open"></i> Browse Files
                            </label>
                            <div id="fileName" class="file-name"></div>
                        </div>
                    </div>
                    
                    <!-- Version -->
                    <div class="form-group">
                        <label class="form-label">Version *</label>
                        <input type="text" name="version" class="form-input" 
                               placeholder="e.g., 2.5.0" value="1.0.0" required>
                        <div class="form-help">Version number for tracking updates</div>
                    </div>
                    
                    <!-- Release Notes -->
                    <div class="form-group">
                        <label class="form-label">Release Notes</label>
                        <textarea name="release_notes" class="form-input form-textarea" 
                                  placeholder="Describe changes, bug fixes, or new features..."></textarea>
                        <div class="form-help">Optional notes about this version</div>
                    </div>
                    
                    <!-- Supported Formats -->
                    <div class="supported-formats">
                        <div class="formats-title">Supported File Formats:</div>
                        <div class="formats-list">
    '''
    
    for ext in sorted(ALLOWED_EXTENSIONS):
        html += f'<span class="format-badge">.{ext}</span>'
    
    html += '''
                        </div>
                    </div>
                    
                    <!-- Actions -->
                    <div class="upload-actions">
                        <a href="/" class="btn btn-secondary">
                            <i class="fas fa-arrow-left"></i> Cancel
                        </a>
                        <button type="submit" class="btn btn-primary" id="submitBtn">
                            <i class="fas fa-upload"></i> Upload File
                        </button>
                    </div>
                </form>
            </div>
        </div>
        
        <script>
            // File upload handling
            const fileInput = document.getElementById('fileInput');
            const fileDropArea = document.getElementById('fileDropArea');
            const fileName = document.getElementById('fileName');
            const submitBtn = document.getElementById('submitBtn');
            
            // File input change
            fileInput.addEventListener('change', function(e) {
                if (this.files.length > 0) {
                    fileName.textContent = this.files[0].name;
                    fileDropArea.classList.add('file-selected');
                    
                    // Check file size
                    const fileSize = this.files[0].size;
                    const maxSize = 10 * 1024 * 1024; // 10MB
                    
                    if (fileSize > maxSize) {
                        alert('File size exceeds 10MB limit!');
                        this.value = '';
                        fileName.textContent = '';
                    }
                }
            });
            
            // Drag and drop
            fileDropArea.addEventListener('dragover', function(e) {
                e.preventDefault();
                this.classList.add('dragover');
            });
            
            fileDropArea.addEventListener('dragleave', function(e) {
                this.classList.remove('dragover');
            });
            
            fileDropArea.addEventListener('drop', function(e) {
                e.preventDefault();
                this.classList.remove('dragover');
                
                if (e.dataTransfer.files.length > 0) {
                    fileInput.files = e.dataTransfer.files;
                    
                    // Trigger change event
                    const event = new Event('change', { bubbles: true });
                    fileInput.dispatchEvent(event);
                }
            });
            
            // Form submission
            document.getElementById('uploadForm').addEventListener('submit', function(e) {
                if (!fileInput.files.length) {
                    e.preventDefault();
                    alert('Please select a file to upload!');
                    return;
                }
                
                // Show loading
                submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Uploading...';
                submitBtn.disabled = true;
            });
            
            // Click on drop area
            fileDropArea.addEventListener('click', function() {
                fileInput.click();
            });
        </script>
    </body>
    </html>
    '''
    
    return html

@app.route('/files')
def list_files():
    """List all files"""
    files = db.get_all_files()
    stats = db.get_statistics()
    
    html = '''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>All Files - File Hosting</title>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <style>
            :root {
                --primary: #4361ee;
                --primary-dark: #3a56d4;
                --success: #2ec4b6;
                --warning: #ff9f1c;
                --danger: #e71d36;
                --dark: #1a1a2e;
                --gray: #6c757d;
                --light: #f8f9fa;
                --card-bg: rgba(255, 255, 255, 0.95);
                --shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
                --radius: 12px;
            }
            
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                color: var(--dark);
                padding: 20px;
            }
            
            .container {
                max-width: 1200px;
                margin: 0 auto;
            }
            
            .header {
                background: var(--card-bg);
                border-radius: var(--radius);
                padding: 30px;
                margin-bottom: 30px;
                box-shadow: var(--shadow);
                border: 1px solid rgba(255, 255, 255, 0.2);
            }
            
            .back-btn {
                display: inline-flex;
                align-items: center;
                gap: 10px;
                background: rgba(67, 97, 238, 0.1);
                color: var(--primary);
                text-decoration: none;
                padding: 12px 24px;
                border-radius: 10px;
                font-weight: 600;
                margin-bottom: 20px;
                transition: all 0.3s;
            }
            
            .back-btn:hover {
                background: var(--primary);
                color: white;
                transform: translateX(-5px);
            }
            
            .header-content {
                display: flex;
                justify-content: space-between;
                align-items: center;
                flex-wrap: wrap;
                gap: 20px;
            }
            
            .header-title h1 {
                font-size: 2rem;
                color: var(--dark);
                margin-bottom: 10px;
            }
            
            .header-title p {
                color: var(--gray);
            }
            
            .header-stats {
                display: flex;
                gap: 20px;
                flex-wrap: wrap;
            }
            
            .stat-badge {
                background: rgba(67, 97, 238, 0.1);
                padding: 12px 24px;
                border-radius: 10px;
                text-align: center;
            }
            
            .stat-value {
                font-size: 1.5rem;
                font-weight: 700;
                color: var(--primary);
            }
            
            .stat-label {
                font-size: 0.9rem;
                color: var(--gray);
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }
            
            /* Search Bar */
            .search-container {
                background: var(--card-bg);
                border-radius: var(--radius);
                padding: 20px;
                margin-bottom: 30px;
                box-shadow: var(--shadow);
            }
            
            .search-box {
                position: relative;
            }
            
            .search-input {
                width: 100%;
                padding: 18px 20px 18px 55px;
                border: 2px solid rgba(0, 0, 0, 0.1);
                border-radius: 12px;
                font-size: 1rem;
                background: white;
                transition: all 0.3s;
            }
            
            .search-input:focus {
                outline: none;
                border-color: var(--primary);
                box-shadow: 0 0 0 3px rgba(67, 97, 238, 0.1);
            }
            
            .search-icon {
                position: absolute;
                left: 20px;
                top: 50%;
                transform: translateY(-50%);
                color: var(--gray);
                font-size: 1.2rem;
            }
            
            /* Files Table */
            .files-container {
                background: var(--card-bg);
                border-radius: var(--radius);
                padding: 30px;
                box-shadow: var(--shadow);
                overflow-x: auto;
            }
            
            .files-table {
                width: 100%;
                border-collapse: collapse;
                min-width: 800px;
            }
            
            .files-table thead {
                background: rgba(67, 97, 238, 0.1);
            }
            
            .files-table th {
                padding: 18px 15px;
                text-align: left;
                font-weight: 600;
                color: var(--primary);
                border-bottom: 2px solid rgba(67, 97, 238, 0.2);
                white-space: nowrap;
            }
            
            .files-table td {
                padding: 18px 15px;
                border-bottom: 1px solid rgba(0, 0, 0, 0.05);
                vertical-align: middle;
            }
            
            .files-table tr:hover {
                background: rgba(67, 97, 238, 0.05);
            }
            
            .file-name-cell {
                min-width: 200px;
            }
            
            .file-name {
                font-weight: 600;
                color: var(--dark);
                display: flex;
                align-items: center;
                gap: 12px;
            }
            
            .file-icon {
                width: 40px;
                height: 40px;
                background: rgba(67, 97, 238, 0.1);
                border-radius: 8px;
                display: flex;
                align-items: center;
                justify-content: center;
                color: var(--primary);
                font-size: 18px;
            }
            
            .file-type {
                display: inline-block;
                padding: 6px 12px;
                background: rgba(46, 196, 182, 0.1);
                color: var(--success);
                border-radius: 20px;
                font-size: 0.85rem;
                font-weight: 600;
            }
            
            .file-version {
                display: inline-block;
                padding: 6px 12px;
                background: rgba(255, 159, 28, 0.1);
                color: var(--warning);
                border-radius: 20px;
                font-size: 0.85rem;
                font-weight: 600;
            }
            
            .file-actions {
                display: flex;
                gap: 8px;
            }
            
            .action-btn {
                padding: 8px 16px;
                border-radius: 8px;
                text-decoration: none;
                font-size: 0.85rem;
                font-weight: 600;
                transition: all 0.3s;
                display: inline-flex;
                align-items: center;
                gap: 6px;
                white-space: nowrap;
            }
            
            .btn-view {
                background: rgba(67, 97, 238, 0.1);
                color: var(--primary);
                border: 1px solid rgba(67, 97, 238, 0.2);
            }
            
            .btn-view:hover {
                background: var(--primary);
                color: white;
                transform: translateY(-2px);
            }
            
            .btn-download {
                background: var(--primary);
                color: white;
                border: 1px solid var(--primary);
            }
            
            .btn-download:hover {
                background: var(--primary-dark);
                transform: translateY(-2px);
            }
            
            .btn-raw {
                background: rgba(231, 29, 54, 0.1);
                color: var(--danger);
                border: 1px solid rgba(231, 29, 54, 0.2);
            }
            
            .btn-raw:hover {
                background: var(--danger);
                color: white;
                transform: translateY(-2px);
            }
            
            .no-files {
                text-align: center;
                padding: 60px 20px;
                color: var(--gray);
            }
            
            .no-files-icon {
                font-size: 4rem;
                margin-bottom: 20px;
                opacity: 0.5;
            }
            
            .pagination {
                display: flex;
                justify-content: center;
                gap: 10px;
                margin-top: 30px;
            }
            
            .page-btn {
                padding: 10px 16px;
                background: white;
                border: 1px solid rgba(0,0,0,0.1);
                border-radius: 8px;
                color: var(--dark);
                text-decoration: none;
                font-weight: 600;
                transition: all 0.3s;
            }
            
            .page-btn.active {
                background: var(--primary);
                color: white;
                border-color: var(--primary);
            }
            
            .page-btn:hover:not(.active) {
                background: rgba(67, 97, 238, 0.1);
                transform: translateY(-2px);
            }
            
            .footer {
                text-align: center;
                padding: 30px;
                color: var(--gray);
                margin-top: 30px;
            }
            
            @media (max-width: 768px) {
                .header-content {
                    flex-direction: column;
                    align-items: flex-start;
                }
                
                .header-stats {
                    width: 100%;
                    justify-content: space-between;
                }
                
                .files-container {
                    padding: 15px;
                }
                
                .files-table {
                    font-size: 0.9rem;
                }
                
                .file-actions {
                    flex-direction: column;
                }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <a href="/" class="back-btn">
                    <i class="fas fa-arrow-left"></i> Back to Home
                </a>
                
                <div class="header-content">
                    <div class="header-title">
                        <h1><i class="fas fa-folder"></i> All Stored Files</h1>
                        <p>Browse and manage all uploaded configuration files</p>
                    </div>
                    
                    <div class="header-stats">
                        <div class="stat-badge">
                            <div class="stat-value">''' + str(stats.get('total_files', 0)) + '''</div>
                            <div class="stat-label">Total Files</div>
                        </div>
                        <div class="stat-badge">
                            <div class="stat-value">''' + str(stats.get('total_downloads', 0)) + '''</div>
                            <div class="stat-label">Total Downloads</div>
                        </div>
                        <div class="stat-badge">
                            <div class="stat-value">''' + str(stats.get('storage_usage', {}).get('total_mb', 0)) + ''' MB</div>
                            <div class="stat-label">Storage Used</div>
                        </div>
                    </div>
                </div>
            </div>
            
            <div class="search-container">
                <div class="search-box">
                    <i class="fas fa-search search-icon"></i>
                    <input type="text" class="search-input" placeholder="Search files by name, version, or notes..." 
                           id="searchInput" onkeyup="searchFiles()">
                </div>
            </div>
            
            <div class="files-container">
    '''
    
    if files:
        html += '''
                <table class="files-table" id="filesTable">
                    <thead>
                        <tr>
                            <th class="file-name-cell">Filename</th>
                            <th>Type</th>
                            <th>Version</th>
                            <th>Size</th>
                            <th>Uploaded</th>
                            <th>Downloads</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody>
        '''
        
        for file in files:
            file_size = format_file_size(file['file_size'])
            uploaded_date = file['created_at'][:10] if len(file['created_at']) > 10 else file['created_at']
            file_icon = {
                'json': 'fas fa-code',
                'txt': 'fas fa-file-alt',
                'conf': 'fas fa-cog',
                'config': 'fas fa-cog',
                'yaml': 'fas fa-file-code',
                'yml': 'fas fa-file-code',
                'xml': 'fas fa-file-code',
                'ini': 'fas fa-cogs',
                'cfg': 'fas fa-cogs',
                'properties': 'fas fa-list'
            }.get(file['file_type'], 'fas fa-file')
            
            html += f'''
                        <tr>
                            <td class="file-name-cell">
                                <div class="file-name">
                                    <div class="file-icon">
                                        <i class="{file_icon}"></i>
                                    </div>
                                    <div>
                                        <div style="font-weight: 600;">{file['original_filename']}</div>
                                        <div style="font-size: 0.85rem; color: var(--gray); margin-top: 4px;">
                                            by {file['uploader_name']}
                                        </div>
                                    </div>
                                </div>
                            </td>
                            <td><span class="file-type">{file['file_type'].upper()}</span></td>
                            <td><span class="file-version">v{file['version']}</span></td>
                            <td>{file_size}</td>
                            <td>{uploaded_date}</td>
                            <td>{file['download_count']}</td>
                            <td>
                                <div class="file-actions">
                                    <a href="/file/{file['id']}" class="action-btn btn-view" title="View Details">
                                        <i class="fas fa-eye"></i> View
                                    </a>
                                    <a href="{file['download_url']}" class="action-btn btn-download" title="Download">
                                        <i class="fas fa-download"></i> Get
                                    </a>
                                    <a href="{file['raw_url']}" target="_blank" class="action-btn btn-raw" title="Raw URL">
                                        <i class="fas fa-external-link-alt"></i> Raw
                                    </a>
                                </div>
                            </td>
                        </tr>
            '''
        
        html += '''
                    </tbody>
                </table>
        '''
    else:
        html += '''
                <div class="no-files">
                    <div class="no-files-icon">
                        <i class="fas fa-folder-open"></i>
                    </div>
                    <h3 style="color: var(--gray); margin-bottom: 15px;">No Files Yet</h3>
                    <p style="color: var(--gray); margin-bottom: 30px;">
                        Upload your first configuration file to get started.
                    </p>
                    <a href="/upload" style="display: inline-block; background: var(--primary); color: white; 
                       padding: 15px 30px; border-radius: 10px; text-decoration: none; font-weight: 600;">
                        <i class="fas fa-upload"></i> Upload First File
                    </a>
                </div>
        '''
    
    html += '''
            </div>
            
            <div class="footer">
                <p>📁 File Hosting Service • All files are stored on server • Max file size: 10MB</p>
            </div>
        </div>
        
        <script>
            function searchFiles() {
                const input = document.getElementById('searchInput');
                const filter = input.value.toLowerCase();
                const table = document.getElementById('filesTable');
                
                if (!table) return;
                
                const rows = table.getElementsByTagName('tr');
                
                for (let i = 1; i < rows.length; i++) {
                    const row = rows[i];
                    const cells = row.getElementsByTagName('td');
                    let found = false;
                    
                    for (let j = 0; j < cells.length; j++) {
                        const cell = cells[j];
                        if (cell.textContent.toLowerCase().indexOf(filter) > -1) {
                            found = true;
                            break;
                        }
                    }
                    
                    row.style.display = found ? '' : 'none';
                }
            }
            
            // Auto-focus search on page load
            document.addEventListener('DOMContentLoaded', function() {
                const searchInput = document.getElementById('searchInput');
                if (searchInput) {
                    searchInput.focus();
                }
            });
        </script>
    </body>
    </html>
    '''
    
    return html

@app.route('/file/<int:file_id>')
def view_file(file_id: int):
    """View file details"""
    file_data = db.get_file_by_id(file_id)
    
    if not file_data:
        return '''
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>File Not Found</title>
            <style>
                body { 
                    font-family: Arial, sans-serif; 
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    min-height: 100vh; 
                    display: flex; 
                    align-items: center; 
                    justify-content: center; 
                    padding: 20px;
                }
                .error { 
                    background: white; 
                    padding: 40px; 
                    border-radius: 20px; 
                    text-align: center; 
                    box-shadow: 0 10px 30px rgba(0,0,0,0.2); 
                    max-width: 500px; 
                    width: 100%;
                }
                .error-icon { 
                    font-size: 4rem; 
                    color: #e74c3c; 
                    margin-bottom: 20px;
                }
                h1 { color: #333; margin-bottom: 10px; }
                p { color: #666; margin-bottom: 30px; }
                a { 
                    display: inline-block; 
                    background: #4361ee; 
                    color: white; 
                    padding: 12px 30px; 
                    border-radius: 10px; 
                    text-decoration: none; 
                    font-weight: bold;
                }
            </style>
        </head>
        <body>
            <div class="error">
                <div class="error-icon">❌</div>
                <h1>File Not Found</h1>
                <p>The requested file does not exist or has been removed.</p>
                <a href="/files">Browse All Files</a>
            </div>
        </body>
        </html>
        ''', 404
    
    # Record view
    db.record_view(file_id, request.remote_addr, request.headers.get('User-Agent'))
    
    file_size = format_file_size(file_data['file_size'])
    versions = db.get_file_versions(file_data['original_filename'])
    
    # Try to read file content (for display)
    file_content = None
    try:
        storage_path = Path(file_data['storage_path'])
        if storage_path.exists():
            with open(storage_path, 'r', encoding='utf-8') as f:
                file_content = f.read()
    except:
        file_content = "Unable to display file content"
    
    html = f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{file_data['original_filename']} - File Details</title>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <style>
            :root {{
                --primary: #4361ee;
                --primary-dark: #3a56d4;
                --success: #2ec4b6;
                --warning: #ff9f1c;
                --danger: #e71d36;
                --dark: #1a1a2e;
                --gray: #6c757d;
                --light: #f8f9fa;
                --card-bg: rgba(255, 255, 255, 0.95);
                --shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
                --radius: 12px;
            }}
            
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                color: var(--dark);
                padding: 20px;
            }}
            
            .container {{
                max-width: 1200px;
                margin: 0 auto;
            }}
            
            .header {{
                background: var(--card-bg);
                border-radius: var(--radius);
                padding: 30px;
                margin-bottom: 30px;
                box-shadow: var(--shadow);
            }}
            
            .back-btn {{
                display: inline-flex;
                align-items: center;
                gap: 10px;
                background: rgba(67, 97, 238, 0.1);
                color: var(--primary);
                text-decoration: none;
                padding: 12px 24px;
                border-radius: 10px;
                font-weight: 600;
                margin-bottom: 30px;
                transition: all 0.3s;
            }}
            
            .back-btn:hover {{
                background: var(--primary);
                color: white;
                transform: translateX(-5px);
            }}
            
            .file-header {{
                display: flex;
                align-items: center;
                gap: 25px;
                margin-bottom: 30px;
                flex-wrap: wrap;
            }}
            
            .file-icon-large {{
                width: 80px;
                height: 80px;
                background: linear-gradient(135deg, var(--primary), var(--secondary));
                border-radius: 15px;
                display: flex;
                align-items: center;
                justify-content: center;
                color: white;
                font-size: 32px;
                box-shadow: 0 8px 25px rgba(67, 97, 238, 0.3);
            }}
            
            .file-title {{
                flex: 1;
            }}
            
            .file-title h1 {{
                font-size: 2rem;
                color: var(--dark);
                margin-bottom: 10px;
                word-break: break-all;
            }}
            
            .file-subtitle {{
                color: var(--gray);
                font-size: 1.1rem;
            }}
            
            .status-badge {{
                display: inline-block;
                padding: 8px 20px;
                background: rgba(46, 196, 182, 0.2);
                color: var(--success);
                border-radius: 20px;
                font-weight: 600;
                font-size: 1rem;
                margin-top: 15px;
            }}
            
            /* File Info Cards */
            .info-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                gap: 20px;
                margin-bottom: 30px;
            }}
            
            .info-card {{
                background: var(--card-bg);
                border-radius: var(--radius);
                padding: 25px;
                box-shadow: var(--shadow);
            }}
            
            .info-title {{
                font-size: 0.9rem;
                color: var(--gray);
                text-transform: uppercase;
                letter-spacing: 0.5px;
                margin-bottom: 10px;
            }}
            
            .info-value {{
                font-size: 1.3rem;
                font-weight: 600;
                color: var(--dark);
                word-break: break-all;
            }}
            
            .info-checksum {{
                font-family: monospace;
                font-size: 0.85rem;
                background: #1a1a2e;
                color: white;
                padding: 10px;
                border-radius: 8px;
                margin-top: 10px;
                word-break: break-all;
            }}
            
            /* Content Preview */
            .content-card {{
                background: var(--card-bg);
                border-radius: var(--radius);
                padding: 30px;
                margin-bottom: 30px;
                box-shadow: var(--shadow);
            }}
            
            .card-title {{
                font-size: 1.3rem;
                font-weight: 700;
                color: var(--dark);
                margin-bottom: 20px;
                display: flex;
                align-items: center;
                gap: 12px;
            }}
            
            .content-preview {{
                background: #1a1a2e;
                color: #e2e8f0;
                padding: 25px;
                border-radius: 12px;
                font-family: 'Courier New', monospace;
                white-space: pre-wrap;
                word-break: break-all;
                max-height: 400px;
                overflow-y: auto;
                line-height: 1.6;
            }}
            
            /* URLs Section */
            .urls-card {{
                background: var(--card-bg);
                border-radius: var(--radius);
                padding: 30px;
                margin-bottom: 30px;
                box-shadow: var(--shadow);
            }}
            
            .url-item {{
                background: rgba(67, 97, 238, 0.05);
                border-radius: 12px;
                padding: 20px;
                margin-bottom: 20px;
                border-left: 4px solid var(--primary);
            }}
            
            .url-label {{
                font-weight: 600;
                color: var(--dark);
                margin-bottom: 10px;
                display: flex;
                align-items: center;
                gap: 10px;
            }}
            
            .url-value {{
                font-family: monospace;
                color: var(--primary);
                word-break: break-all;
                padding: 12px;
                background: white;
                border-radius: 8px;
                border: 1px solid rgba(0,0,0,0.1);
            }}
            
            /* Action Buttons */
            .actions-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 20px;
                margin-bottom: 30px;
            }}
            
            .action-button {{
                background: var(--card-bg);
                border-radius: var(--radius);
                padding: 25px;
                text-align: center;
                text-decoration: none;
                color: var(--dark);
                box-shadow: var(--shadow);
                transition: all 0.3s;
                display: flex;
                flex-direction: column;
                align-items: center;
                gap: 15px;
            }}
            
            .action-button:hover {{
                transform: translateY(-5px);
                box-shadow: 0 10px 30px rgba(0, 0, 0, 0.2);
            }}
            
            .action-icon {{
                width: 60px;
                height: 60px;
                border-radius: 12px;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 24px;
                color: white;
            }}
            
            .action-text {{
                font-weight: 600;
                font-size: 1.1rem;
            }}
            
            /* Versions */
            .versions-card {{
                background: var(--card-bg);
                border-radius: var(--radius);
                padding: 30px;
                box-shadow: var(--shadow);
            }}
            
            .version-list {{
                display: flex;
                flex-direction: column;
                gap: 15px;
            }}
            
            .version-item {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 20px;
                background: rgba(0, 0, 0, 0.02);
                border-radius: 12px;
                border-left: 4px solid var(--primary);
            }}
            
            .version-info {{
                flex: 1;
            }}
            
            .version-number {{
                font-weight: 600;
                color: var(--dark);
            }}
            
            .version-date {{
                font-size: 0.9rem;
                color: var(--gray);
            }}
            
            .version-action {{
                background: var(--primary);
                color: white;
                padding: 10px 20px;
                border-radius: 8px;
                text-decoration: none;
                font-weight: 600;
                transition: all 0.3s;
            }}
            
            .version-action:hover {{
                background: var(--primary-dark);
                transform: scale(1.05);
            }}
            
            .footer {{
                text-align: center;
                padding: 30px;
                color: var(--gray);
                margin-top: 30px;
            }}
            
            @media (max-width: 768px) {{
                .file-header {{
                    flex-direction: column;
                    align-items: flex-start;
                }}
                
                .info-grid {{
                    grid-template-columns: 1fr;
                }}
                
                .actions-grid {{
                    grid-template-columns: 1fr;
                }}
                
                .version-item {{
                    flex-direction: column;
                    align-items: flex-start;
                    gap: 15px;
                }}
                
                .version-action {{
                    width: 100%;
                    text-align: center;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <a href="/files" class="back-btn">
                    <i class="fas fa-arrow-left"></i> Back to Files
                </a>
                
                <div class="file-header">
                    <div class="file-icon-large">
    '''
    
    # File icon based on type
    file_icons = {
        'json': 'fas fa-code',
        'txt': 'fas fa-file-alt',
        'conf': 'fas fa-cog',
        'config': 'fas fa-cog',
        'yaml': 'fas fa-file-code',
        'yml': 'fas fa-file-code',
        'xml': 'fas fa-file-code',
        'ini': 'fas fa-cogs',
        'cfg': 'fas fa-cogs',
        'properties': 'fas fa-list'
    }
    
    icon_class = file_icons.get(file_data['file_type'], 'fas fa-file')
    html += f'''
                        <i class="{icon_class}"></i>
                    </div>
                    
                    <div class="file-title">
                        <h1>{file_data['original_filename']}</h1>
                        <div class="file-subtitle">
                            <span style="margin-right: 20px;">Type: {file_data['file_type'].upper()}</span>
                            <span style="margin-right: 20px;">Size: {file_size}</span>
                            <span>Version: v{file_data['version']}</span>
                        </div>
                        <div class="status-badge">
                            <i class="fas fa-check-circle"></i> Active • {file_data['download_count']} Downloads
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- File Information -->
            <div class="info-grid">
                <div class="info-card">
                    <div class="info-title">Uploaded By</div>
                    <div class="info-value">
                        <i class="fas fa-user"></i> {file_data['uploader_name']}
                    </div>
                </div>
                
                <div class="info-card">
                    <div class="info-title">Upload Date</div>
                    <div class="info-value">
                        <i class="fas fa-calendar"></i> {file_data['created_at']}
                    </div>
                </div>
                
                <div class="info-card">
                    <div class="info-title">File Type</div>
                    <div class="info-value">
                        <i class="fas fa-file-code"></i> {file_data['file_type'].upper()}
                    </div>
                </div>
                
                <div class="info-card">
                    <div class="info-title">Checksum (SHA256)</div>
                    <div class="info-checksum">
                        {file_data['checksum']}
                    </div>
                </div>
            </div>
            
            <!-- Content Preview -->
            <div class="content-card">
                <div class="card-title">
                    <i class="fas fa-eye"></i> File Content Preview
                </div>
                <div class="content-preview">
    '''
    
    if file_content:
        # Limit preview to 2000 characters
        preview = file_content[:2000]
        if len(file_content) > 2000:
            preview += "\n\n... (content truncated)"
        html += preview
    else:
        html += "Unable to display file content"
    
    html += f'''
                </div>
            </div>
            
            <!-- URLs -->
            <div class="urls-card">
                <div class="card-title">
                    <i class="fas fa-link"></i> File URLs
                </div>
                
                <div class="url-item">
                    <div class="url-label">
                        <i class="fas fa-external-link-alt"></i> Raw URL (Direct Access)
                    </div>
                    <div class="url-value">{file_data['raw_url']}</div>
                </div>
                
                <div class="url-item">
                    <div class="url-label">
                        <i class="fas fa-download"></i> Download URL
                    </div>
                    <div class="url-value">{file_data['download_url']}</div>
                </div>
                
                <div class="url-item">
                    <div class="url-label">
                        <i class="fas fa-globe"></i> Public URL
                    </div>
                    <div class="url-value">{file_data['public_url']}</div>
                </div>
            </div>
            
            <!-- Action Buttons -->
            <div class="actions-grid">
                <a href="{file_data['raw_url']}" target="_blank" class="action-button">
                    <div class="action-icon" style="background: var(--primary);">
                        <i class="fas fa-external-link-alt"></i>
                    </div>
                    <div class="action-text">Open Raw URL</div>
                </a>
                
                <a href="{file_data['download_url']}" class="action-button">
                    <div class="action-icon" style="background: var(--success);">
                        <i class="fas fa-download"></i>
                    </div>
                    <div class="action-text">Download File</div>
                </a>
                
                <a href="#" onclick="copyToClipboard('{file_data['raw_url']}')" class="action-button">
                    <div class="action-icon" style="background: var(--warning);">
                        <i class="fas fa-copy"></i>
                    </div>
                    <div class="action-text">Copy Raw URL</div>
                </a>
                
                <a href="/upload" class="action-button">
                    <div class="action-icon" style="background: var(--danger);">
                        <i class="fas fa-upload"></i>
                    </div>
                    <div class="action-text">Upload New Version</div>
                </a>
            </div>
    '''
    
    if versions and len(versions) > 1:
        html += f'''
            <!-- Version History -->
            <div class="versions-card">
                <div class="card-title">
                    <i class="fas fa-history"></i> Version History ({len(versions)} versions)
                </div>
                <div class="version-list">
        '''
        
        for version in versions:
            html += f'''
                    <div class="version-item">
                        <div class="version-info">
                            <div class="version-number">v{version['version']}</div>
                            <div class="version-date">{version['created_at']}</div>
                        </div>
                        <a href="{version['raw_url']}" target="_blank" class="version-action">
                            <i class="fas fa-external-link-alt"></i> Get This Version
                        </a>
                    </div>
            '''
        
        html += '''
                </div>
            </div>
        '''
    
    html += '''
            <div class="footer">
                <p>📁 File Hosting Service • File ID: ''' + str(file_id) + ''' • Last accessed: ''' + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + '''</p>
            </div>
        </div>
        
        <script>
            function copyToClipboard(text) {
                navigator.clipboard.writeText(text).then(() => {
                    alert('✅ Raw URL copied to clipboard!\\n\\n' + text);
                }).catch(err => {
                    alert('❌ Failed to copy: ' + err);
                });
            }
        </script>
    </body>
    </html>
    '''
    
    return html

@app.route('/raw/<filename>')
def raw_file(filename: str):
    """Serve raw file (like Pastebin)"""
    file_data = db.get_file_by_filename(filename)
    
    if not file_data:
        return "File not found", 404
    
    storage_path = Path(file_data['storage_path'])
    
    if not storage_path.exists():
        return "File not found on server", 404
    
    # Record download
    db.record_download(file_data['id'], request.remote_addr, request.headers.get('User-Agent'))
    
    # Serve raw file
    return send_file(
        storage_path,
        as_attachment=False,
        download_name=file_data['original_filename'],
        mimetype='text/plain'
    )

@app.route('/download/<filename>')
def download_file(filename: str):
    """Download file with attachment"""
    file_data = db.get_file_by_filename(filename)
    
    if not file_data:
        return "File not found", 404
    
    storage_path = Path(file_data['storage_path'])
    
    if not storage_path.exists():
        return "File not found on server", 404
    
    # Record download
    db.record_download(file_data['id'], request.remote_addr, request.headers.get('User-Agent'))
    
    # Serve file as attachment
    return send_file(
        storage_path,
        as_attachment=True,
        download_name=file_data['original_filename']
    )

@app.route('/api/upload', methods=['POST'])
def api_upload():
    """API endpoint for file upload"""
    try:
        # Check if file was uploaded
        if 'file' not in request.files:
            return jsonify({
                'success': False,
                'error': 'No file provided'
            }), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({
                'success': False,
                'error': 'No file selected'
            }), 400
        
        # Check file extension
        if not allowed_file(file.filename):
            return jsonify({
                'success': False,
                'error': f'File type not allowed. Allowed: {", ".join(ALLOWED_EXTENSIONS)}'
            }), 400
        
        # Get form data
        version = request.form.get('version', '1.0.0')
        release_notes = request.form.get('release_notes', '')
        
        # Secure filename and generate unique name
        original_filename = secure_filename(file.filename)
        unique_filename = db.generate_unique_filename(original_filename)
        file_type = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else 'txt'
        
        # Save file
        storage_path = UPLOAD_FOLDER / unique_filename
        file.save(storage_path)
        
        # Get file info
        file_size = storage_path.stat().st_size
        checksum = calculate_checksum(storage_path)
        
        # Generate URLs
        base_url = request.host_url.rstrip('/')
        public_url = f"{base_url}file/{unique_filename}"
        raw_url = f"{base_url}raw/{unique_filename}"
        download_url = f"{base_url}download/{unique_filename}"
        
        # Prepare file data
        file_data = {
            'filename': unique_filename,
            'original_filename': original_filename,
            'file_type': file_type,
            'file_size': file_size,
            'version': version,
            'storage_path': str(storage_path),
            'public_url': public_url,
            'raw_url': raw_url,
            'download_url': download_url,
            'release_notes': release_notes,
            'checksum': checksum,
            'uploader_id': ADMIN_ID,
            'uploader_name': 'API User'
        }
        
        # Save to database
        if db.add_file(file_data):
            return jsonify({
                'success': True,
                'message': 'File uploaded successfully',
                'file': {
                    'id': file_data['filename'],
                    'original_name': file_data['original_filename'],
                    'type': file_data['file_type'],
                    'size': file_data['file_size'],
                    'version': file_data['version'],
                    'urls': {
                        'raw': file_data['raw_url'],
                        'download': file_data['download_url'],
                        'public': file_data['public_url']
                    },
                    'checksum': file_data['checksum'],
                    'created_at': datetime.now().isoformat()
                }
            })
        else:
            # Delete file if database failed
            if storage_path.exists():
                storage_path.unlink()
            
            return jsonify({
                'success': False,
                'error': 'Database error'
            }), 500
            
    except Exception as e:
        logger.error(f"API upload error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/files')
def api_files():
    """API endpoint to list all files"""
    files = db.get_all_files()
    
    return jsonify({
        'success': True,
        'count': len(files),
        'files': files
    })

@app.route('/api/file/<filename>')
def api_file_info(filename: str):
    """API endpoint to get file info"""
    file_data = db.get_file_by_filename(filename)
    
    if not file_data:
        return jsonify({
            'success': False,
            'error': 'File not found'
        }), 404
    
    return jsonify({
        'success': True,
        'file': file_data
    })

@app.route('/api/stats')
def api_stats():
    """API endpoint for statistics"""
    stats = db.get_statistics()
    
    return jsonify({
        'success': True,
        'statistics': stats,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api')
def api_docs():
    """API documentation"""
    html = '''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>API Documentation - File Hosting</title>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <style>
            :root {
                --primary: #4361ee;
                --dark: #1a1a2e;
                --gray: #6c757d;
                --light: #f8f9fa;
            }
            
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                color: var(--dark);
                padding: 20px;
            }
            
            .container {
                max-width: 1000px;
                margin: 0 auto;
                background: white;
                border-radius: 20px;
                padding: 40px;
                box-shadow: 0 15px 35px rgba(0,0,0,0.2);
            }
            
            .back-btn {
                display: inline-flex;
                align-items: center;
                gap: 10px;
                background: rgba(67, 97, 238, 0.1);
                color: var(--primary);
                text-decoration: none;
                padding: 12px 24px;
                border-radius: 10px;
                font-weight: 600;
                margin-bottom: 30px;
            }
            
            h1 {
                color: var(--dark);
                margin-bottom: 30px;
                font-size: 2.5rem;
            }
            
            .api-endpoint {
                background: #f8f9fa;
                border-radius: 12px;
                padding: 25px;
                margin-bottom: 25px;
                border-left: 4px solid var(--primary);
            }
            
            .method {
                display: inline-block;
                padding: 8px 16px;
                background: var(--primary);
                color: white;
                border-radius: 6px;
                font-weight: 600;
                margin-right: 15px;
            }
            
            .url {
                font-family: monospace;
                font-size: 1.1rem;
                color: var(--dark);
                word-break: break-all;
            }
            
            .description {
                color: var(--gray);
                margin: 15px 0;
                line-height: 1.6;
            }
            
            .code-block {
                background: #1a1a2e;
                color: #e2e8f0;
                padding: 20px;
                border-radius: 8px;
                font-family: monospace;
                margin: 15px 0;
                overflow-x: auto;
            }
            
            .param {
                margin: 20px 0;
            }
            
            .param-name {
                font-weight: 600;
                color: var(--dark);
            }
            
            .param-desc {
                color: var(--gray);
                margin-left: 10px;
            }
            
            .response {
                margin: 25px 0;
            }
            
            .response-title {
                font-weight: 600;
                color: var(--dark);
                margin-bottom: 10px;
            }
            
            .footer {
                text-align: center;
                padding: 30px;
                color: var(--gray);
                margin-top: 40px;
                border-top: 1px solid rgba(0,0,0,0.1);
            }
        </style>
    </head>
    <body>
        <div class="container">
            <a href="/" class="back-btn">
                <i class="fas fa-arrow-left"></i> Back to Home
            </a>
            
            <h1><i class="fas fa-code"></i> API Documentation</h1>
            
            <div class="api-endpoint">
                <div>
                    <span class="method">POST</span>
                    <span class="url">/api/upload</span>
                </div>
                <div class="description">
                    Upload a new file to the server. Returns file information and raw URL.
                </div>
                
                <div class="param">
                    <span class="param-name">file (multipart/form-data)</span>
                    <span class="param-desc">The file to upload (max 10MB)</span>
                </div>
                
                <div class="param">
                    <span class="param-name">version (form-data)</span>
                    <span class="param-desc">Version number (default: 1.0.0)</span>
                </div>
                
                <div class="param">
                    <span class="param-name">release_notes (form-data)</span>
                    <span class="param-desc">Optional release notes</span>
                </div>
                
                <div class="response">
                    <div class="response-title">Response (Success):</div>
                    <div class="code-block">
{
  "success": true,
  "message": "File uploaded successfully",
  "file": {
    "id": "12345678_abc123.json",
    "original_name": "config.json",
    "type": "json",
    "size": 1024,
    "version": "2.5.0",
    "urls": {
      "raw": "https://your-domain.com/raw/12345678_abc123.json",
      "download": "https://your-domain.com/download/12345678_abc123.json",
      "public": "https://your-domain.com/file/12345678_abc123.json"
    },
    "checksum": "sha256_hash_here",
    "created_at": "2024-01-15T12:00:00"
  }
}
                    </div>
                </div>
            </div>
            
            <div class="api-endpoint">
                <div>
                    <span class="method">GET</span>
                    <span class="url">/api/files</span>
                </div>
                <div class="description">
                    Get list of all uploaded files with their information.
                </div>
                
                <div class="response">
                    <div class="response-title">Response:</div>
                    <div class="code-block">
{
  "success": true,
  "count": 5,
  "files": [
    {
      "id": 1,
      "original_filename": "config.json",
      "file_type": "json",
      "file_size": 1024,
      "version": "2.5.0",
      "raw_url": "https://your-domain.com/raw/12345678_abc123.json",
      "download_url": "https://your-domain.com/download/12345678_abc123.json",
      "release_notes": "Bug fixes",
      "uploader_name": "Admin",
      "created_at": "2024-01-15 12:00:00",
      "download_count": 10,
      "is_active": true
    }
  ]
}
                    </div>
                </div>
            </div>
            
            <div class="api-endpoint">
                <div>
                    <span class="method">GET</span>
                    <span class="url">/api/file/{filename}</span>
                </div>
                <div class="description">
                    Get detailed information about a specific file.
                </div>
                
                <div class="param">
                    <span class="param-name">filename (path parameter)</span>
                    <span class="param-desc">The unique filename from upload response</span>
                </div>
            </div>
            
            <div class="api-endpoint">
                <div>
                    <span class="method">GET</span>
                    <span class="url">/api/stats</span>
                </div>
                <div class="description">
                    Get system statistics including file counts and storage usage.
                </div>
            </div>
            
            <div class="api-endpoint">
                <div>
                    <span class="method">GET</span>
                    <span class="url">/raw/{filename}</span>
                </div>
                <div class="description">
                    <strong>Raw file access</strong> - Returns the raw file content (like Pastebin).
                    Use this URL in your applications to access the file directly.
                </div>
                
                <div class="param">
                    <span class="param-name">filename (path parameter)</span>
                    <span class="param-desc">The unique filename from upload response</span>
                </div>
                
                <div class="response">
                    <div class="response-title">Response:</div>
                    <div class="code-block">
Raw file content (text/plain)
                    </div>
                </div>
            </div>
            
            <div class="api-endpoint">
                <div>
                    <span class="method">GET</span>
                    <span class="url">/download/{filename}</span>
                </div>
                <div class="description">
                    Download file with attachment headers (forces download in browser).
                </div>
            </div>
            
            <div class="footer">
                <p>📁 File Hosting Service API • All endpoints return JSON unless otherwise specified</p>
                <p style="margin-top: 10px;">
                    Base URL: <code>''' + request.host_url.rstrip('/') + '''</code>
                </p>
            </div>
        </div>
    </body>
    </html>
    '''
    
    return html

# ==========================
# STARTUP
# ==========================
def startup():
    """Startup sequence"""
    print("\n" + "="*70)
    print("📁 FILE HOSTING SERVICE - PASTEBIN ALTERNATIVE".center(70))
    print("="*70)
    
    stats = db.get_statistics()
    storage = db.get_storage_usage()
    
    print(f"\n✅ Database initialized")
    print(f"📂 Upload folder: {UPLOAD_FOLDER.absolute()}")
    print(f"🤖 Bot ready for admin: {ADMIN_ID}")
    
    print(f"\n📊 Current Stats:")
    print(f"   • Total Files: {stats.get('total_files', 0)}")
    print(f"   • Storage Used: {storage.get('total_mb', 0)} MB")
    print(f"   • Total Downloads: {stats.get('total_downloads', 0)}")
    
    print(f"\n🌐 Web Interface:")
    print(f"   • Home: http://localhost:{PORT}")
    print(f"   • Upload: http://localhost:{PORT}/upload")
    print(f"   • Browse Files: http://localhost:{PORT}/files")
    print(f"   • API Docs: http://localhost:{PORT}/api")
    
    print(f"\n🔗 API Endpoints:")
    print(f"   • Upload: POST /api/upload")
    print(f"   • List Files: GET /api/files")
    print(f"   • Raw Access: GET /raw/{{filename}}")
    print(f"   • Download: GET /download/{{filename}}")
    print(f"   • Stats: GET /api/stats")
    
    print(f"\n📱 Features:")
    print(f"   • File upload (max 10MB)")
    print(f"   • Raw URL generation")
    print(f"   • Version tracking")
    print(f"   • Download statistics")
    print(f"   • Search functionality")
    print(f"   • Telegram bot integration")
    
    print("="*70)
    print("🚀 SYSTEM READY - FILES HOSTING ACTIVE")
    print("="*70 + "\n")

# ==========================
# MAIN
# ==========================
if __name__ == "__main__":
    # Start bot in background
    bot_thread = threading.Thread(target=handle_telegram_bot, daemon=True)
    bot_thread.start()
    
    # Show startup info
    startup()
    
    # Start Flask app
    app.run(
        host='0.0.0.0',
        port=PORT,
        debug=False,
        threaded=True,
        use_reloader=False

    )
