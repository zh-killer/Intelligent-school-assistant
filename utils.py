# ====================== 工具函数 ======================
import base64
import hashlib
import hmac
import os
import socket
import uuid
from io import BytesIO

from PIL import Image

import config


# ====================== 密码哈希 ======================
# PBKDF2-HMAC-SHA256 + 每用户随机盐。早期版本用的是 SHA256 + 固定盐，
# 固定盐等于全库共用一张彩虹表，所以这里保留对老哈希的校验能力，
# 老用户下次登录成功时由 database.db_verify_user 自动升级成新格式。
_PBKDF2_ITERATIONS = 200_000
_LEGACY_SALT = "smart_campus_2024_salt"
_PBKDF2_PREFIX = "pbkdf2_sha256"


def hash_password(password):
    """生成 PBKDF2 密码哈希，格式：pbkdf2_sha256$迭代次数$盐$摘要"""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return "{}${}${}${}".format(
        _PBKDF2_PREFIX,
        _PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def _legacy_hash(password):
    """早期版本的哈希：SHA256 + 固定盐（仅用于校验存量数据）"""
    return hashlib.sha256((password + _LEGACY_SALT).encode("utf-8")).hexdigest()


def verify_password(password, stored):
    """校验密码，同时兼容旧的 SHA256 哈希与新的 PBKDF2 哈希"""
    if not password or not stored:
        return False
    if stored.startswith(_PBKDF2_PREFIX + "$"):
        try:
            _, iterations, salt_b64, digest_b64 = stored.split("$")
            salt = base64.b64decode(salt_b64)
            expected = base64.b64decode(digest_b64)
            actual = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), salt, int(iterations)
            )
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(actual, expected)
    return hmac.compare_digest(_legacy_hash(password), stored)


def needs_rehash(stored):
    """存量的老哈希需要在下一次登录成功后升级"""
    return not (stored or "").startswith(_PBKDF2_PREFIX + "$")


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
