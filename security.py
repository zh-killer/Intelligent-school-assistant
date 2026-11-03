# ====================== 安全层 ======================
"""三道防线：SSRF 拦截（网页抓取）、SQL 白名单（自然语言转 SQL）、登录限速。

这里的校验都是为了兜住「模型生成的东西不能直接执行」：
LLM 输出只是字符串，执行前必须过一遍确定性规则。
"""
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

# FROM / JOIN / INTO / UPDATE 后面跟的表名（允许反引号）
_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE)\s+`?([A-Za-z_][A-Za-z0-9_]*)`?", re.IGNORECASE
)


def _tables_in(sql: str) -> set:
    return {match.group(1).lower() for match in _TABLE_RE.finditer(sql)}


def _validate_sql(sql: str) -> tuple:
    """校验模型生成的 SQL，返回 (is_safe: bool, reason: str)

    规则（任一条不满足就拒绝）：
    1. 单条语句，且以 SELECT / INSERT / UPDATE / DELETE 开头；
    2. 不含 DDL、权限、文件读写这类关键字；
    3. 只操作白名单里的表（students / scores）；
    4. UPDATE / DELETE 必须带 WHERE —— 否则一条语句就能清空整张表。

    这是「确定性规则兜底」，不是完整的 SQL 解析器：字符串字面量里出现关键字
    会被误判（比如 WHERE name = 'drop'），但对校园演示库来说，宁可误杀不可放过。
    """
    text = (sql or "").strip()
    if not text:
        return False, "SQL 为空"

    if ";" in text.rstrip(";"):
        return False, "禁止一次执行多条语句"

    upper = text.upper()
    if not any(upper.startswith(op) for op in _ALLOWED_OPERATIONS):
        head = upper.split()[0] if upper.split() else "空"
        return False, f"仅允许 SELECT/INSERT/UPDATE/DELETE，收到：{head}"

    for keyword in _BLOCKED_SQL_KEYWORDS:
        if keyword in upper:
            return False, f"禁止使用 {keyword} 操作"

    unknown = _tables_in(text) - _ALLOWED_TABLES
    if unknown:
        return False, f"只允许查询 {sorted(_ALLOWED_TABLES)}，收到：{sorted(unknown)}"

    if upper.startswith(("UPDATE", "DELETE")) and not re.search(r"\bWHERE\b", upper):
        return False, "UPDATE / DELETE 必须带 WHERE 条件"

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
