# ====================== 认证 & 用户管理回调 ======================
import gradio as gr
import database
import security
import utils
import chat


# ====================== 登录/注册/登出回调 ======================
def do_login(username, password):
    """登录校验：查MySQL users表，成功则显示主应用并初始化用户会话"""
    if not username or not username.strip():
        return (gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(),
                "⚠️ 请输入用户名")
    if not password or not password.strip():
        return (gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(),
                "⚠️ 请输入密码")

    # 频率限制检查
    key = username.strip().lower()
    allowed, wait = security._check_rate_limit(key)
    if not allowed:
        return (gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(),
                f"⏳ 登录尝试过于频繁，请 {wait} 秒后再试")

    success, result, role = database.db_verify_user(username, password)
    security._record_attempt(key, success)
    if not success:
        return (gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(),
                f"❌ {result}")

    # 登录成功 → 初始化该用户的默认会话
    database.init_chat_tables()
    uname = result
    default_sid = f"default-{uname}"
    database.db_ensure_session(default_sid, "默认会话", uname)
    dropdown_update = chat.refresh_session_dropdown((uname, role), default_sid)
    history = database.db_load_messages(default_sid)
    print(f"✅ 用户登录成功: {uname} (角色: {role})")

    return (gr.update(visible=False),
            gr.update(visible=True),
            (uname, role),
            dropdown_update,
            history,
            default_sid,
            "")


def do_register(username, password):
    """注册新用户：写入MySQL users表，成功则自动登录"""
    if not username or not username.strip():
        return (gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(),
                "⚠️ 请输入用户名（至少2个字符）")
    if not password or not password.strip():
        return (gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(),
                "⚠️ 请输入密码（至少4个字符）")

    success, msg = database.db_create_user(username, password)
    if not success:
        return (gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(),
                f"❌ {msg}")

    # 注册成功 → 自动登录
    print(f"✅ 新用户注册并登录: {username}")
    database.init_chat_tables()
    uname = username.strip().lower()
    default_sid = f"default-{uname}"
    database.db_ensure_session(default_sid, "默认会话", uname)
    dropdown_update = chat.refresh_session_dropdown((uname, "user"), default_sid)
    history = database.db_load_messages(default_sid)

    return (gr.update(visible=False),
            gr.update(visible=True),
            (uname, "user"),
            dropdown_update,
            history,
            default_sid,
            "")


def do_logout(user_state):
    """退出登录：隐藏主应用，显示登录页，清空状态"""
    username = utils._uname(user_state) or "unknown"
    print(f"🚪 用户登出: {username}")
    return (gr.update(visible=True),
            gr.update(visible=False),
            None,
            gr.update(choices=[]),
            [],
            "default",
            "",
            "",
            "")


# ====================== 修改密码回调 ======================
def change_password(user_state_val, old_pwd, new_pwd):
    uname = utils._uname(user_state_val)
    if not uname:
        return "❌ 请先登录"
    if not old_pwd or not new_pwd:
        return "⚠️ 请填写原密码和新密码"
    ok, msg = database.db_change_password(uname, old_pwd, new_pwd)
    return f"✅ {msg}" if ok else f"❌ {msg}"


# ====================== 管理员回调 ======================
def admin_list_users(user_state_val):
    if utils._role(user_state_val) != "admin":
        return "❌ 权限不足：仅管理员可执行此操作"
    return database.admin_list_users()


def admin_delete_user(user_state_val, target):
    if utils._role(user_state_val) != "admin":
        return "❌ 权限不足：仅管理员可执行此操作"
    if not target or not target.strip():
        return "⚠️ 请输入要删除的用户名"
    return database.admin_delete_user(target)


def admin_promote_user(user_state_val, target):
    if utils._role(user_state_val) != "admin":
        return "❌ 权限不足：仅管理员可执行此操作"
    if not target or not target.strip():
        return "⚠️ 请输入要提升的用户名"
    return database.admin_promote_user(target)
