# ====================== 数据库层 ======================
import pymysql
import threading
import datetime
import json
import os
from queue import Queue, Empty
import config

# ====================== 简易数据库连接池 ======================
_POOL_SIZE = 5
_pool = Queue(maxsize=_POOL_SIZE)
_pool_lock = threading.Lock()
_pool_created = 0


def _create_conn():
    """创建一条新的数据库连接（自动提交模式）"""
    conn = pymysql.connect(**config.DB_CONFIG)
    conn.autocommit(True)
    return conn


def get_conn():
    """从连接池获取一条数据库连接（池满则新建，用后需调用 return_conn 归还）"""
    global _pool_created
    try:
        return _pool.get(block=False)
    except Empty:
        with _pool_lock:
            if _pool_created < _POOL_SIZE:
                _pool_created += 1
                return _create_conn()
        # 池已满，新建一条临时连接（超出池大小）
        return _create_conn()


def return_conn(conn):
    """归还连接到池（池满则关闭）"""
    if conn is None:
        return
    try:
        _pool.put_nowait(conn)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass


# ====================== 基础数据库操作 ======================
def query_db(sql, params=None):
    """统一执行SQL查询和非查询操作"""
    conn = get_conn()
    cursor = conn.cursor()

    try:
        cursor.execute(sql, params or ())

        if sql.strip().lower().startswith("select"):
            rows = cursor.fetchall()
            if not rows:
                return "查询结果为空"
            cols = [desc[0] for desc in cursor.description]
            res = [" | ".join(cols), "-" * 50]
            for row in rows:
                res.append(" | ".join(str(x) for x in row))
            return "\n".join(res)
        else:
            conn.commit()
            return f"操作成功，影响 {cursor.rowcount} 行"
    except Exception as e:
        return f"数据库操作失败：{str(e)}"
    finally:
        cursor.close()
        return_conn(conn)


def db_exec(sql, params=None):
    """执行写操作，失败只打印不抛异常（数据库不可用时应用仍可运行）"""
    try:
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute(sql, params or ())
        conn.commit()
        cursor.close()
        return_conn(conn)
        return True
    except Exception as e:
        print(f"[聊天记录] 数据库写入失败: {e}")
        return False


def db_fetch(sql, params=None):
    """执行读操作，失败返回空列表"""
    try:
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute(sql, params or ())
        rows = cursor.fetchall()
        cursor.close()
        return_conn(conn)
        return rows
    except Exception as e:
        print(f"[聊天记录] 数据库读取失败: {e}")
        return []


# ====================== 建表 ======================
def init_chat_tables():
    """创建会话/消息表（不存在时自动建），并为老表补充username列"""
    db_exec("""CREATE TABLE IF NOT EXISTS chat_sessions (
        session_id VARCHAR(64) PRIMARY KEY,
        session_name VARCHAR(100) NOT NULL,
        username VARCHAR(64) NOT NULL DEFAULT 'public',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    ) DEFAULT CHARSET=utf8mb4""")
    db_exec("""CREATE TABLE IF NOT EXISTS chat_messages (
        id INT AUTO_INCREMENT PRIMARY KEY,
        session_id VARCHAR(64) NOT NULL,
        role VARCHAR(16) NOT NULL,
        content TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_session (session_id)
    ) DEFAULT CHARSET=utf8mb4""")
    # 老版本建的表没有username列 → 自动补上（多用户会话隔离用）
    has_col = db_fetch(
        "SELECT 1 FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='chat_sessions' AND COLUMN_NAME='username'",
        (config.DB_CONFIG["database"],))
    if not has_col:
        db_exec("ALTER TABLE chat_sessions ADD COLUMN username VARCHAR(64) NOT NULL DEFAULT 'public'")


def init_users_table():
    """创建用户表（不存在时自动建）"""
    db_exec("""CREATE TABLE IF NOT EXISTS users (
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(64) NOT NULL UNIQUE,
        password_hash VARCHAR(128) NOT NULL,
        display_name VARCHAR(64) DEFAULT NULL,
        role VARCHAR(16) NOT NULL DEFAULT 'user',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    ) DEFAULT CHARSET=utf8mb4""")
    # 老版本建的表没有role列 → 自动补上
    has_col = db_fetch(
        "SELECT 1 FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='users' AND COLUMN_NAME='role'",
        (config.DB_CONFIG["database"],))
    if not has_col:
        db_exec("ALTER TABLE users ADD COLUMN role VARCHAR(16) NOT NULL DEFAULT 'user'")


# ====================== 会话 CRUD ======================
def db_list_sessions(username="public"):
    """返回该用户的 [(session_id, session_name, created_at), ...] 按创建时间倒序"""
    return db_fetch(
        "SELECT session_id, session_name, created_at FROM chat_sessions "
        "WHERE username=%s ORDER BY created_at DESC", (username,))


def db_ensure_session(session_id, name=None, username="public"):
    """会话不存在时创建（已存在则忽略）"""
    db_exec("INSERT IGNORE INTO chat_sessions (session_id, session_name, username) "
            "VALUES (%s, %s, %s)",
            (session_id, name or session_id, username))


def db_load_messages(session_id):
    """读取某会话的全部聊天记录，返回Gradio Chatbot格式"""
    rows = db_fetch("SELECT role, content FROM chat_messages WHERE session_id=%s ORDER BY id",
                    (session_id,))
    messages = []
    for r, c in rows:
        content = c
        if isinstance(c, str) and c.startswith('[{'):
            try:
                parsed = json.loads(c)
                content = [p for p in parsed
                           if p.get("type") != "image" or os.path.exists(p.get("path", ""))]
            except Exception:
                pass
        messages.append({"role": r, "content": content})
    return messages


def db_save_message(session_id, role, content):
    """保存一条聊天消息"""
    db_exec("INSERT INTO chat_messages (session_id, role, content) VALUES (%s, %s, %s)",
            (session_id, role, content))


def db_rename_session(session_id, new_name):
    """重命名会话（session_id不变，Agent记忆不断）"""
    db_exec("UPDATE chat_sessions SET session_name=%s WHERE session_id=%s",
            (new_name, session_id))


def db_delete_session(session_id):
    """删除会话及其全部聊天记录"""
    db_exec("DELETE FROM chat_messages WHERE session_id=%s", (session_id,))
    db_exec("DELETE FROM chat_sessions WHERE session_id=%s", (session_id,))


# ====================== 用户 CRUD ======================
def db_create_user(username, password, display_name=None):
    """创建新用户，成功返回True，用户已存在返回False"""
    import utils
    username = username.strip().lower()
    if not username or len(username) < 2:
        return False, "用户名至少2个字符"
    if not password or len(password) < 4:
        return False, "密码至少4个字符"
    try:
        pw_hash = utils.hash_password(password)
        db_exec(
            "INSERT INTO users (username, password_hash, display_name) VALUES (%s, %s, %s)",
            (username, pw_hash, display_name or username)
        )
        return True, "注册成功，请登录"
    except pymysql.IntegrityError:
        return False, "用户名已存在"
    except Exception as e:
        return False, f"注册失败: {e}"


def db_verify_user(username, password):
    """验证用户登录，成功返回 (True, username, role)，失败返回 (False, 错误信息, None)"""
    import utils
    username = username.strip().lower()
    pw_hash = utils.hash_password(password)
    rows = db_fetch(
        "SELECT username, display_name, role FROM users WHERE username=%s AND password_hash=%s",
        (username, pw_hash)
    )
    if rows:
        return True, rows[0][0], rows[0][2]
    # 统一错误消息，不区分"用户不存在"和"密码错误"（防止用户名枚举攻击）
    return False, "用户名或密码错误", None


def db_change_password(username, old_password, new_password):
    """修改用户密码，返回 (success: bool, message: str)"""
    import utils
    username = username.strip().lower() if isinstance(username, str) else username
    pw_hash = utils.hash_password(old_password)
    rows = db_fetch(
        "SELECT 1 FROM users WHERE username=%s AND password_hash=%s",
        (username, pw_hash)
    )
    if not rows:
        return False, "原密码错误"
    if not new_password or len(new_password) < 4:
        return False, "新密码至少4个字符"
    new_hash = utils.hash_password(new_password)
    ok = db_exec(
        "UPDATE users SET password_hash=%s WHERE username=%s",
        (new_hash, username)
    )
    return (True, "密码修改成功") if ok else (False, "数据库操作失败，请稍后重试")


def seed_default_users():
    """初始化默认用户到数据库（用户已存在时跳过）。返回实际创建成功的用户数"""
    import utils
    defaults = [
        ("admin", "admin123", "管理员", "admin"),
        ("zhangsan", "123456", "张三", "user"),
        ("lisi", "123456", "李四", "user"),
    ]
    created = 0
    for uname, pwd, dname, role in defaults:
        rows = db_fetch("SELECT 1 FROM users WHERE username=%s", (uname,))
        if not rows:
            pw_hash = utils.hash_password(pwd)
            ok = db_exec(
                "INSERT INTO users (username, password_hash, display_name, role) VALUES (%s, %s, %s, %s)",
                (uname, pw_hash, dname, role)
            )
            if ok:
                print(f"  ✅ 默认用户已创建: {uname} (角色: {role})")
                created += 1
            else:
                print(f"  ⚠️ 默认用户 {uname} 创建失败（数据库不可用，请检查 MySQL 配置）")
        else:
            db_exec("UPDATE users SET role=%s WHERE username=%s AND role='user' AND %s='admin'",
                    (role, uname, uname))
            print(f"  ⏭️ 用户已存在，跳过: {uname}")
    return created


# ====================== 管理员操作 ======================
def admin_list_users():
    """管理员查看所有用户列表"""
    rows = db_fetch("SELECT id, username, display_name, role, created_at FROM users ORDER BY id")
    if not rows:
        return "暂无用户数据"
    lines = ["| ID | 用户名 | 显示名 | 角色 | 创建时间 |",
             "|----|--------|--------|------|----------|"]
    for r in rows:
        lines.append(f"| {r[0]} | {r[1]} | {r[3] or '-'} | {r[4]} | {str(r[4])[:19]} |")
    return "\n".join(lines)


def admin_delete_user(target_username):
    """管理员删除用户（不能删除自己，不能删除其他admin）"""
    target = target_username.strip().lower()
    if target == "admin":
        return "❌ 不能删除内置 admin 账号"
    rows = db_fetch("SELECT role FROM users WHERE username=%s", (target,))
    if not rows:
        return f"❌ 用户 '{target}' 不存在"
    if rows[0][0] == "admin":
        return "❌ 不能删除其他管理员账号"
    db_exec("DELETE FROM chat_messages WHERE session_id IN "
            "(SELECT session_id FROM chat_sessions WHERE username=%s)", (target,))
    db_exec("DELETE FROM chat_sessions WHERE username=%s", (target,))
    db_exec("DELETE FROM users WHERE username=%s", (target,))
    return f"✅ 用户 '{target}' 及其聊天数据已删除"


def admin_promote_user(target_username):
    """管理员提升用户为管理员"""
    target = target_username.strip().lower()
    ok = db_exec("UPDATE users SET role='admin' WHERE username=%s", (target,))
    return f"✅ 用户 '{target}' 已提升为管理员" if ok else f"❌ 操作失败"
