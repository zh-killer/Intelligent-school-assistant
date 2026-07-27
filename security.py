# ====================== 安全层 ======================
import ipaddress
import time as _time
import re
from urllib.parse import urlparse

# ====================== SSRF 防护 ======================
_BLOCKED_CIDRS = [
    ipaddress.ip_network("127.0.0.0/8"),      # loopback
    ipaddress.ip_network("10.0.0.0/8"),       # private A
    ipaddress.ip_network("172.16.0.0/12"),    # private B
    ipaddress.ip_network("192.168.0.0/16"),   # private C
    ipaddress.ip_network("169.254.0.0/16"),   # link-local
    ipaddress.ip_network("0.0.0.0/8"),        # current network
    ipaddress.ip_network("::1/128"),          # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),         # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
]


def _is_safe_url(url: str) -> bool:
    """校验 URL 是否安全（拒绝内网地址，防 SSRF）"""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return False
        # 拒绝裸 IP 内网地址
        try:
            ip = ipaddress.ip_address(hostname)
            for cidr in _BLOCKED_CIDRS:
                if ip in cidr:
                    return False
        except ValueError:
            pass  # 不是IP地址，是域名，允许
        # 只允许 http/https
        if parsed.scheme not in ("http", "https"):
            return False
        return True
    except Exception:
        return False


# ====================== SQL 安全校验 ======================
_ALLOWED_TABLES = {"students", "scores"}
_BLOCKED_SQL_KEYWORDS = [
    "DROP", "ALTER", "TRUNCATE", "CREATE", "RENAME",
    "GRANT", "REVOKE", "EXEC", "EXECUTE", "CALL",
    "LOAD", "INTO OUTFILE", "INTO DUMPFILE", "SHUTDOWN",
]
_ALLOWED_OPERATIONS = ("SELECT", "INSERT", "UPDATE", "DELETE")


def _validate_sql(sql: str) -> tuple:
    """校验 SQL 是否安全，返回 (is_safe: bool, reason: str)"""
    sql_upper = sql.upper().strip()
    # 1. 检查是否以允许的操作开头
    if not any(sql_upper.startswith(op) for op in _ALLOWED_OPERATIONS):
        return False, f"仅允许 SELECT/INSERT/UPDATE/DELETE 操作，收到: {sql_upper.split()[0] if sql_upper else '空'}"
    # 2. 检查是否包含禁止的关键字
    for kw in _BLOCKED_SQL_KEYWORDS:
        if kw in sql_upper:
            return False, f"禁止使用 {kw} 操作"
    return True, ""


# ====================== 登录频率限制（防暴力破解） ======================
_login_attempts = {}  # {ip_or_username: [(timestamp, success), ...]}
_MAX_ATTEMPTS = 10      # 窗口内最多尝试次数
_ATTEMPT_WINDOW = 300   # 时间窗口（秒）


def _check_rate_limit(key):
    """检查登录频率，返回 (是否允许, 剩余秒数)"""
    now = _time.time()
    attempts = _login_attempts.get(key, [])
    # 清理过期的尝试记录
    attempts = [a for a in attempts if now - a[0] < _ATTEMPT_WINDOW]
    _login_attempts[key] = attempts
    if len(attempts) >= _MAX_ATTEMPTS:
        wait = int(_ATTEMPT_WINDOW - (now - attempts[0][0]))
        return False, max(0, wait)
    return True, 0


def _record_attempt(key, success):
    """记录一次登录尝试"""
    now = _time.time()
    attempts = _login_attempts.get(key, [])
    attempts = [a for a in attempts if now - a[0] < _ATTEMPT_WINDOW]
    attempts.append((now, success))
    _login_attempts[key] = attempts
