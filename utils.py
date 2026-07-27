# ====================== 工具函数 ======================
import hashlib
import socket
import base64
import uuid
import os
from io import BytesIO
from PIL import Image
import config


def hash_password(password):
    """SHA256 + 固定盐 密码哈希（演示级别安全，生产环境请用 bcrypt）"""
    salt = "smart_campus_2024_salt"
    return hashlib.sha256((password + salt).encode()).hexdigest()


def _uname(user_state):
    """从 user_state 中提取用户名（兼容旧版字符串和元组格式）"""
    return user_state[0] if isinstance(user_state, (list, tuple)) else user_state


def _role(user_state):
    """从 user_state 中提取角色"""
    return user_state[1] if isinstance(user_state, (list, tuple)) and len(user_state) > 1 else "user"


def get_local_ip():
    """获取本机局域网 IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def check_config():
    """检查关键配置，返回 (errors: list, warnings: list)"""
    warnings = []
    errors = []

    # 智谱 AI（核心，缺了完全不能用）
    if not os.getenv("zhipuai_api_key"):
        errors.append("❌ zhipuai_api_key 未设置 → AI对话、联网搜索、图像识别全部不可用")
    else:
        print(f"✅ 智谱AI Key: {os.getenv('zhipuai_api_key')[:8]}***")

    # 高德地图（天气功能需要）
    if not os.getenv("AMAP_API_KEY"):
        warnings.append("⚠️ AMAP_API_KEY 未设置 → 天气查询不可用（去 https://console.amap.com/ 免费申请）")
    else:
        print(f"✅ 高德地图 Key: {os.getenv('AMAP_API_KEY')[:8]}***")

    # MySQL（数据库、登录、历史记录需要）
    mysql_pwd = os.getenv("MYSQL_PASSWORD")
    if not mysql_pwd or mysql_pwd == "your_mysql_password":
        warnings.append("⚠️ MYSQL_PASSWORD 未设置或为默认值 → 数据库功能不可用（登录/历史记录/学生管理）")
    else:
        print(f"✅ MySQL: root@{os.getenv('MYSQL_HOST', 'localhost')}:{os.getenv('MYSQL_PORT', '3306')}")

    # QQ邮箱（可选）
    mail_user = os.getenv("MAIL_USER")
    if not mail_user or "your_email" in str(mail_user):
        warnings.append("💡 MAIL_USER/MAIL_PASS 未设置 → 邮件发送不可用（可选功能）")
    else:
        print(f"✅ 邮件: {mail_user}")

    return errors, warnings


def image_to_base64(image):
    """PIL图片转base64，用于多模态接口调用"""
    buffer = BytesIO()
    image.save(buffer, format="png")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def get_img_info_from_pil(pil_img):
    """把PIL图片保存到本地缓存目录，返回文件信息字典"""
    os.makedirs(config.UPLOAD_CACHE_DIR, exist_ok=True)
    unique_name = f"{uuid.uuid4()}.png"
    full_save_path = os.path.join(config.UPLOAD_CACHE_DIR, unique_name)
    pil_img.save(full_save_path)
    name_no_ext, ext = os.path.splitext(unique_name)
    return {
        "full_path": full_save_path,
        "file_name": unique_name,
        "name_without_ext": name_no_ext,
        "suffix": ext
    }
