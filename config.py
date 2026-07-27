# ====================== 环境初始化 ======================
import os
import sys
import io

# 自动加载 .env 文件（优先级高于系统环境变量）
try:
    from dotenv import load_dotenv
    _env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    load_dotenv(_env_file)
    if os.path.exists(_env_file):
        print(f"[配置] 已加载 {_env_file}")
except ImportError:
    pass  # python-dotenv 未安装时忽略，用户可以手动设环境变量

# 修复 Windows GBK 终端打印 emoji 崩溃的问题
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ====================== 数据库配置 ======================
DB_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", "your_mysql_password"),
    "port": 3306,
    "database": "db_demo",
    "charset": "utf8mb4"
}

# ====================== 路径常量 ======================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_CACHE_DIR = os.path.join(BASE_DIR, "upload_cache")
YOLO_WEIGHTS = os.path.join(BASE_DIR, "..", "day04", "yolo11n.pt")
