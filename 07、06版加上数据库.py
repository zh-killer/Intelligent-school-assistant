import json
import os
import sys
import io
import gradio as gr
import requests
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from zhipuai import ZhipuAI
import datetime
import socket
import smtplib
from email.mime.text import MIMEText
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
import pymysql
import re
from bs4 import BeautifulSoup
from html import escape as html_escape
import base64
import uuid
try:
    import cv2
except ImportError:
    cv2 = None  # OpenCV 是可选依赖（仅拍照+YOLO标注图需要）
from io import BytesIO
from PIL import Image

# ====================== 环境初始化 ======================
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

"""
1.应用langchain开发可以使用的工具(天气、百科、邮件、数据库、联网搜索)
2.修复原有bug，完善功能
3.兼容Gradio 6.0版本
4.修复启动和网络访问问题
5.添加会话记忆功能，支持多会话隔离
6.添加数据库查询功能
7.联网搜索：主用智谱 Web Search API（结果新、带日期），失败时降级 Bing 抓取
8.添加网页抓取功能（BeautifulSoup + LLM总结）
9.修复天气查询被联网搜索劫持的问题（快速通道优先判断天气关键词）
"""

# ====================== 数据库配置 ======================
DB_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", "your_mysql_password"),
    "port": 3306,
    "database": "db_demo",
    "charset": "utf8mb4"
}


def get_conn():
    """获取数据库连接"""
    return pymysql.connect(**DB_CONFIG)


def query_db(sql, params=None):
    """统一执行SQL查询和非查询操作"""
    conn = get_conn()
    cursor = conn.cursor()

    try:
        cursor.execute(sql, params or ())

        if sql.strip().lower().startswith("select"):
            # 查询操作：返回格式化结果
            rows = cursor.fetchall()
            if not rows:
                return "查询结果为空"
            cols = [desc[0] for desc in cursor.description]
            res = [" | ".join(cols), "-" * 50]
            for row in rows:
                res.append(" | ".join(str(x) for x in row))
            return "\n".join(res)
        else:
            # 非查询操作：提交事务并返回影响行数
            conn.commit()
            return f"操作成功，影响 {cursor.rowcount} 行"
    except Exception as e:
        return f"数据库操作失败：{str(e)}"
    finally:
        cursor.close()
        conn.close()


# ====================== 会话历史持久化（MySQL） ======================
def db_exec(sql, params=None):
    """执行写操作，失败只打印不抛异常（数据库不可用时应用仍可运行）"""
    try:
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute(sql, params or ())
        conn.commit()
        cursor.close()
        conn.close()
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
        conn.close()
        return rows
    except Exception as e:
        print(f"[聊天记录] 数据库读取失败: {e}")
        return []


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
        (DB_CONFIG["database"],))
    if not has_col:
        db_exec("ALTER TABLE chat_sessions ADD COLUMN username VARCHAR(64) NOT NULL DEFAULT 'public'")


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
    """读取某会话的全部聊天记录，返回Gradio Chatbot格式（图片消息存的是JSON，解析后恢复显示）"""
    rows = db_fetch("SELECT role, content FROM chat_messages WHERE session_id=%s ORDER BY id",
                    (session_id,))
    messages = []
    for r, c in rows:
        content = c
        if isinstance(c, str) and c.startswith('[{'):
            try:
                parsed = json.loads(c)
                # 图片文件还在时才恢复显示图片，避免文件被删后报错
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


# ====================== 用户账号管理（MySQL持久化） ======================
import hashlib


def init_users_table():
    """创建用户表（不存在时自动建）"""
    db_exec("""CREATE TABLE IF NOT EXISTS users (
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(64) NOT NULL UNIQUE,
        password_hash VARCHAR(128) NOT NULL,
        display_name VARCHAR(64) DEFAULT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    ) DEFAULT CHARSET=utf8mb4""")


def hash_password(password):
    """SHA256 + 固定盐 密码哈希（演示级别安全，生产环境请用 bcrypt）"""
    salt = "smart_campus_2024_salt"
    return hashlib.sha256((password + salt).encode()).hexdigest()


def db_create_user(username, password, display_name=None):
    """创建新用户，成功返回True，用户已存在返回False"""
    username = username.strip().lower()
    if not username or len(username) < 2:
        return False, "用户名至少2个字符"
    if not password or len(password) < 4:
        return False, "密码至少4个字符"
    try:
        pw_hash = hash_password(password)
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
    """验证用户登录，成功返回 (True, username)，失败返回 (False, 错误信息)"""
    username = username.strip().lower()
    pw_hash = hash_password(password)
    rows = db_fetch(
        "SELECT username, display_name FROM users WHERE username=%s AND password_hash=%s",
        (username, pw_hash)
    )
    if rows:
        return True, rows[0][0]
    # 再查一下用户名是否存在（区分"密码错误"和"用户不存在"）
    exists = db_fetch("SELECT 1 FROM users WHERE username=%s", (username,))
    if exists:
        return False, "密码错误"
    return False, "用户不存在"


def seed_default_users():
    """初始化默认用户到数据库（用户已存在时跳过）"""
    defaults = [
        ("admin", "admin123", "管理员"),
        ("zhangsan", "123456", "张三"),
        ("lisi", "123456", "李四"),
    ]
    for uname, pwd, dname in defaults:
        rows = db_fetch("SELECT 1 FROM users WHERE username=%s", (uname,))
        if not rows:
            pw_hash = hash_password(pwd)
            db_exec(
                "INSERT INTO users (username, password_hash, display_name) VALUES (%s, %s, %s)",
                (uname, pw_hash, dname)
            )
            print(f"  ✅ 默认用户已创建: {uname}")
        else:
            print(f"  ⏭️ 用户已存在，跳过: {uname}")


# ====================== 智谱GLM4 配置 ======================
llm = ChatOpenAI(
    model="glm-4",
    api_key=os.getenv("zhipuai_api_key"),
    base_url=os.getenv("zhipuai_base_url"),
    temperature=0.1,
    timeout=30,
)

client = ZhipuAI()

# 创建全局检查点保存器（用于记忆功能）
checkpointer = InMemorySaver()

# 系统提示
system_prompt = """你是一个智慧校园系统的AI助手，拥有以下工具：

**工具列表（按优先级排序）**：
1. **get_weather** - 查询天气（最高优先级，遇到天气相关必须使用）
2. **execute_sql_query** - 执行数据库操作
3. **send_email_tool** - 发送邮件
4. **search_baike** - 搜索百科
5. **web_search** - 联网搜索
6. **web_fetch** - 抓取并总结网页内容

**重要：工具选择规则（严格按优先级）**

1. **天气查询** 🥇：
   - 如果用户提到"天气"、"温度"、"湿度"、"预报"等关键词
   - **必须**使用 get_weather 工具
   - **禁止**使用 web_search 来查询天气，即使问题中包含"今天"、"现在"等时间词
   - 示例："长沙天气" → get_weather；"长沙今天天气怎么样" → get_weather

2. **数据库操作** 🥈：
   - 如果涉及学生信息、成绩、数据增删改查
   - **必须**使用 execute_sql_query 工具

3. **邮件发送** 🥉：
   - 如果用户说要发邮件
   - **必须**使用 send_email_tool 工具

4. **百科查询**：
   - 如果询问名词解释、人物介绍
   - 使用 search_baike 工具

5. **联网搜索**：
   - 当查询实时新闻、最新资讯、热点话题时使用
   - 对于时效性问题（如"今天""最近""2026年"的新闻、事件），不要凭训练数据回答，使用 web_search
   - **你的训练数据有截止日期，早于今天的真实日期**。凡是询问2024年及以后的赛事结果、比赛战况、时事、人物动态、排名等，你的记忆很可能过时或缺失，**必须**调用 web_search 查询，
     **禁止**回答"尚未举行""还有X年才开始"或凭训练数据预测
   - **注意**：天气查询绝对不走联网搜索

6. **网页抓取**：
   - 当用户提供URL并要求阅读总结时使用

**其他规则**：
- 从用户的消息中提取关键信息作为工具参数
- 记住对话历史，以便更好地理解用户的上下文
- 对于无法确定的问题，诚实地表示不知道
"""


# ====================== 工具1：天气查询 ======================
@tool
def get_weather(location: str, date: str = None) -> str:
    """当用户询问某个城市的天气时，使用此工具获取实时天气信息。"""
    try:
        weather_url = "https://restapi.amap.com/v3/weather/weatherInfo"
        key = os.getenv("AMAP_API_KEY", "your_amap_key")
        weather_url = f"{weather_url}?key={key}&city={location}"
        response = requests.get(weather_url, timeout=10)
        weather_data = json.loads(response.text)

        if weather_data.get("status") != "1":
            return f"抱歉，无法获取{location}的天气信息，请检查城市名称是否正确。"

        info = weather_data["lives"][0]
        weather_json_out = {
            "location": location,
            "date": info["reporttime"],
            "weather": info['weather'],
            "temperature": f"{info['temperature']}°C",
            "humidity": f"{info['humidity']}%",
            "wind_direction": info.get('winddirection', '未知'),
            "wind_power": info.get('windpower', '未知')
        }
        return beautify_weather_output(weather_json_out)
    except Exception as e:
        return f"获取天气信息时出错：{str(e)}"


def beautify_weather_output(input_data):
    """将天气JSON数据转成易读的文字信息"""
    instruction = """
        任务：查询指定城市的天气情况，给出友好的天气播报和出行建议。
        """

    example = """
        输入：{'location': '长沙', 'date': '2026-07-14 15:01:46', 'weather': '晴', 'temperature': '37°C', 'humidity': '55%', 'wind_direction': '南风', 'wind_power': '3级'}
        输出：
            🌤️ 当前城市：湖南省长沙市
            📅 当前时间：2026-07-14 15:01:46
            🌡️ 当前天气：晴天
            🌡️ 当前温度：37°C
            💧 湿度：55%
            💨 风向风力：南风 3级
            💡 出行建议：天气炎热，建议减少户外活动。如必要出行，请注意防晒和补水。
        """

    output_template = """
        请按以下格式输出：
            🌤️ 当前城市：
            📅 当前时间：
            🌡️ 当前天气：
            🌡️ 当前温度：
            💧 湿度：
            💨 风向风力：
            💡 出行建议：
        """

    prompt = f"{instruction}{example}输入：{input_data}{output_template}"

    try:
        response = client.chat.completions.create(
            model="glm-4",
            messages=[
                {"role": "system", "content": "你是一个友好的天气播报助手，要根据天气情况给出合理的出行建议。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"""
🌤️ 当前城市：{input_data['location']}
📅 当前时间：{input_data['date']}
🌡️ 当前天气：{input_data['weather']}
🌡️ 当前温度：{input_data['temperature']}
💧 湿度：{input_data['humidity']}
💨 风向风力：{input_data.get('wind_direction', '未知')} {input_data.get('wind_power', '未知')}
        """


# ====================== 工具2：百科查询（Bing后端，国内可用） ======================
@tool
def search_baike(query: str) -> str:
    """当用户询问某个名词、人物、地点的百科信息时，使用此工具搜索百科（Bing搜索引擎）。"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Accept': 'text/html,application/xhtml+xml',
        }

        encoded_query = requests.utils.quote(f"{query} 百科")
        search_url = f"https://www.bing.com/search?q={encoded_query}&setlang=zh-cn"
        resp = requests.get(search_url, headers=headers, timeout=15)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')

        # 提取搜索结果摘要
        results = []
        for item in soup.select('li.b_algo')[:5]:
            title_el = item.select_one('h2 a')
            snippet_el = item.select_one('.b_caption p, .b_lineclamp2, .b_algoSlug')
            if title_el:
                title = title_el.get_text(strip=True)
                snippet = snippet_el.get_text(strip=True) if snippet_el else ''
                results.append(f"📌 **{title}**\n   {snippet}")

        if not results:
            return f"未找到关于「{query}」的百科信息，请尝试更换关键词。"

        return f"🔍 关于「{query}」的搜索结果：\n\n" + "\n\n".join(results)

    except requests.exceptions.Timeout:
        return f"❌ 搜索超时：请稍后重试。"
    except Exception as e:
        return f"❌ 搜索百科信息时出错：{str(e)}"


# ====================== 工具5：联网搜索（智谱Web Search API为主，Bing降级） ======================
@tool
def web_search(query: str, max_results: int = 5) -> str:
    """当用户询问实时新闻、最新资讯、今日热点、赛事结果、某个话题的最新动态等需要联网搜索的内容时，使用此工具进行互联网搜索。
    注意：查询天气请使用 get_weather 工具，不要使用本工具。
    参数：query - 搜索关键词，max_results - 返回结果数量（默认5条）"""
    # 时效性判断：查询包含时间敏感词时，只搜最近一周的结果，
    # 否则会搜到几个月前的旧文章/赛前预测（这是"搜不到最新消息"的根因）
    recency_words = ["今天", "今日", "昨天", "最新", "最近", "近日", "近期",
                     "刚刚", "实时", "这两天", "这几天", "本周", "这周", "这个月",
                     "现在", "结果", "战况", "今年", "2025", "2026"]
    is_recent = any(w in query for w in recency_words)

    # ---- 主通道：智谱 Web Search API（返回真实文章，带发布日期，结果新） ----
    for recency in (["oneWeek", None] if is_recent else [None]):
        try:
            kwargs = dict(search_engine="search_std", search_query=query, count=max_results,
                          search_intent=True)
            if recency:
                kwargs["search_recency_filter"] = recency
            resp = client.web_search.web_search(**kwargs)
            results = []
            for r in (resp.search_result or [])[:max_results]:
                date = getattr(r, 'publish_date', '') or ''
                content = (getattr(r, 'content', '') or '')[:200]
                link = getattr(r, 'link', '') or ''
                results.append(f"{len(results)+1}. **{r.title}**（{date}）\n   🔗 {link}\n   📝 {content}")
            if results:
                scope = "最近一周" if recency else "全部时间"
                return f"🔍 关于「{query}」的搜索结果（{scope}）：\n\n" + "\n\n".join(results)
            # 一周内无结果 → 循环降级为不限时间再搜一次
        except Exception as e:
            print(f"[web_search] 智谱搜索失败（recency={recency}）: {e}")
            break  # API本身出错就不重试了，直接降级Bing

    # ---- 降级通道：Bing 网页抓取（加"最近一周"时间过滤，避免返回陈旧门户页） ----
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Accept': 'text/html,application/xhtml+xml',
        }

        encoded_query = requests.utils.quote(query)
        # filters=ex1:"ez2" 表示只看最近一周的结果
        search_url = (f"https://www.bing.com/search?q={encoded_query}"
                      f"&setlang=zh-cn&count={max_results}&filters=ex1%3a%22ez2%22")
        resp = requests.get(search_url, headers=headers, timeout=15)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')

        # 提取搜索结果
        results = []
        for item in soup.select('li.b_algo')[:max_results]:
            title_el = item.select_one('h2 a')
            snippet_el = item.select_one('.b_caption p, .b_lineclamp2')
            if title_el:
                title = title_el.get_text(strip=True)
                url = title_el.get('href', '')
                snippet = snippet_el.get_text(strip=True) if snippet_el else ''
                results.append(f"{len(results)+1}. **{title}**\n   🔗 {url}\n   📝 {snippet}")

        if not results:
            return f"未找到关于「{query}」的搜索结果，请尝试更换关键词。"

        return f"🔍 关于「{query}」的搜索结果：\n\n" + "\n\n".join(results)

    except requests.exceptions.Timeout:
        return f"❌ 搜索超时：请稍后重试。"
    except Exception as e:
        return f"❌ 联网搜索失败：{str(e)}"


# ====================== 工具6：网页内容抓取 ======================
@tool
def web_fetch(url: str) -> str:
    """当用户提供URL链接并要求阅读、总结、分析网页内容时，使用此工具抓取并总结网页内容。
    参数：url - 要抓取的网页完整URL地址"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=15)
        response.encoding = response.apparent_encoding

        soup = BeautifulSoup(response.text, 'html.parser')

        # 移除无用标签
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
            tag.decompose()

        # 提取文本
        text = soup.get_text(separator='\n', strip=True)

        # 清理多余空行
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        text = '\n'.join(lines)

        # 限制长度
        if len(text) > 4000:
            text = text[:4000] + "..."

        # 使用LLM总结
        summary_prompt = f"请总结以下网页内容的关键信息，用简洁的中文列出要点：\n\n{text}"
        ai_response = client.chat.completions.create(
            model="glm-4",
            messages=[
                {"role": "system", "content": "你是一个信息总结助手，请用简洁的中文总结网页关键内容。"},
                {"role": "user", "content": summary_prompt}
            ],
            temperature=0.3
        )
        summary = ai_response.choices[0].message.content

        return f"📄 **网页内容总结**\n\n{summary}\n\n---\n🔗 原文链接：{url}"
    except requests.exceptions.Timeout:
        return f"❌ 请求超时：无法在15秒内访问 {url}"
    except requests.exceptions.ConnectionError:
        return f"❌ 无法连接到 {url}，请检查URL是否正确"
    except Exception as e:
        return f"❌ 获取网页内容失败：{str(e)}"


# ====================== 工具3：发送邮件 ======================
@tool
def send_email_tool(subject: str, content: str) -> str:
    """
    当用户明确要求发送邮件、发邮件、发送一封邮件等指令时，使用此工具发送邮件。
    例如："发送邮件：主题为XXX，内容为XXX" 或 "帮我发一封邮件，主题是XXX，内容是XXX"
    """
    try:
        mail_host = "smtp.qq.com"
        mail_user = os.getenv("MAIL_USER", "your_email@qq.com")
        mail_pass = os.getenv("MAIL_PASS", "your_smtp_password")

        sender = mail_user
        receivers = [os.getenv("MAIL_RECEIVER", "receiver@qq.com")]

        message = MIMEText(content, 'plain', 'utf-8')
        message['From'] = sender
        message['To'] = receivers[0]
        message['Subject'] = subject

        smtpObj = smtplib.SMTP_SSL(mail_host, 465)
        smtpObj.login(mail_user, mail_pass)
        smtpObj.sendmail(sender, receivers, message.as_string())
        smtpObj.quit()

        return f"✅ 邮件发送成功！\n收件人：{receivers[0]}\n主题：{subject}\n内容：{content}"
    except smtplib.SMTPAuthenticationError:
        return "❌ 邮件发送失败：邮箱认证失败，请检查邮箱账号和授权码是否正确。"
    except smtplib.SMTPException as e:
        return f"❌ 邮件发送失败：{str(e)}"
    except Exception as e:
        return f"❌ 邮件发送失败：{str(e)}"


# ====================== 工具4：数据库操作 ======================
@tool
def execute_sql_query(query: str) -> str:
    """
    执行学生和成绩相关的数据库操作
    支持查询、添加、修改、删除学生信息和成绩
    参数：query - 用户的自然语言问题
    """
    print(f"[工具调用] 执行SQL查询: {query}")

    # SQL生成系统提示词 - 优化版
    sql_prompt = """
    你是一个专业的MySQL 8.0 SQL生成器，**只返回合法的SQL语句**。

    表结构：
    students: student_id, student_name, student_no, gender, major, class_name, email, phone
    scores: score_id, student_id, course_name, score, semester, exam_time

    **重要数据规则**：
    1. major（专业）字段存储的是**中文**，如：'人工智能'、'计算机科学'、'软件工程'、'数据科学'
    2. gender（性别）字段存储的是**中文**，如：'男'、'女'
    3. course_name（课程名）字段存储的是**中文**，如：'高等数学'、'数据结构'、'数据库原理'
    4. student_name（学生姓名）字段存储的是**中文**

    **SQL生成规则**：
    1. 查询成绩必须使用JOIN关联students和scores表
    2. 优先使用student_name作为查询条件
    3. 支持AVG、COUNT、SUM等聚合函数
    4. 支持INSERT、UPDATE、DELETE、SELECT所有操作
    5. **关键规则**：WHERE条件中匹配中文字段值时，必须使用用户输入中的**原始中文**，不要翻译成英文
    6. 绝对不返回任何自然语言解释、注释或说明
    7. 禁止返回中文内容（指注释和说明），但SQL中的字符串值可以是中文
    8. 如果用户要求删除数据，务必生成DELETE语句
    9. 如果用户要求修改数据，务必生成UPDATE语句

    **示例**：
    用户：查询所有人工智能专业的学生
    SQL: SELECT * FROM students WHERE major = '人工智能'

    用户：查询计算机科学专业的男生
    SQL: SELECT * FROM students WHERE major = '计算机科学' AND gender = '男'

    用户：查询张伟的成绩
    SQL: SELECT s.student_name, sc.course_name, sc.score FROM students s JOIN scores sc ON s.student_id = sc.student_id WHERE s.student_name = '张伟'
    """

    try:
        # 调用大模型生成SQL
        response = llm.invoke([
            ("system", sql_prompt),
            ("user", query)
        ])

        # 提取并清理SQL
        sql = response.content.strip()
        sql = sql.replace("```sql", "").replace("```", "").strip()

        # 正则提取纯SQL
        sql_match = re.search(r"(SELECT|INSERT|UPDATE|DELETE).*", sql, re.I | re.DOTALL)
        if sql_match:
            sql = sql_match.group(0).strip()

        # 最终校验
        if not sql or not sql.upper().startswith(("SELECT", "INSERT", "UPDATE", "DELETE")):
            return "错误：无法生成合法的SQL语句，请重新描述您的问题。"

        print(f"[生成SQL] {sql}")
        result = query_db(sql)

        # 如果是查询操作，美化输出
        if sql.strip().lower().startswith("select"):
            return f"📊 查询结果：\n{result}"
        else:
            return f"✅ {result}"

    except Exception as e:
        return f"❌ 数据库操作失败：{str(e)}"


# ====================== 图像识别（GLM-4V + YOLO）与摄像头 ======================
UPLOAD_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "upload_cache")
YOLO_WEIGHTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "day04", "yolo11n.pt")
_yolo_model = None  # YOLO模型缓存：只加载一次，反复推理复用


def image_to_base64(image):
    """PIL图片转base64，用于多模态接口调用"""
    buffer = BytesIO()
    image.save(buffer, format="png")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def pre_recognize_image(image,
                        prompt="详细描述图片中的所有信息。如果图中出现知名公众人物，"
                               "请直接给出其姓名和身份；不太确定时给出最可能的候选并说明不确定。"):
    """调用智谱GLM-4V多模态模型识别图片内容，返回文字描述"""
    if image is None:
        return ""
    try:
        img_64 = image_to_base64(image)
        response = client.chat.completions.create(
            model="glm-4v",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/png;base64,{img_64}"}}
                    ]
                }
            ],
            temperature=0.1
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"图像识别失败：{e}"


def get_img_info_from_pil(pil_img):
    """把PIL图片保存到本地缓存目录，返回文件信息字典"""
    os.makedirs(UPLOAD_CACHE_DIR, exist_ok=True)
    unique_name = f"{uuid.uuid4()}.png"
    full_save_path = os.path.join(UPLOAD_CACHE_DIR, unique_name)
    pil_img.save(full_save_path)
    name_no_ext, ext = os.path.splitext(unique_name)
    return {
        "full_path": full_save_path,
        "file_name": unique_name,
        "name_without_ext": name_no_ext,
        "suffix": ext
    }


def get_yolo_info(img_path):
    """YOLO目标检测：返回 (检测信息文本, 标注框图片路径或None)（模型只加载一次）"""
    global _yolo_model
    try:
        if _yolo_model is None:
            from ultralytics import YOLO
            weights = YOLO_WEIGHTS if os.path.exists(YOLO_WEIGHTS) else "yolo11n.pt"
            print(f"[YOLO] 加载模型: {weights}")
            _yolo_model = YOLO(weights)

        results = _yolo_model([img_path])
        info_list = []
        boxed_path = None
        for res in results:
            boxes = res.boxes
            if boxes is None or len(boxes) == 0:
                continue
            xyxy = boxes.xyxy.cpu().numpy()
            confs = boxes.conf.cpu().numpy()
            cls_ids = boxes.cls.cpu().numpy().astype(int)
            cls_names = res.names
            for i, box in enumerate(xyxy):
                x1, y1, x2, y2 = box
                info_list.append(
                    f"目标{i + 1}：类别={cls_names[cls_ids[i]]}，置信度={confs[i]:.2f}，"
                    f"框坐标[x1:{x1:.0f},y1:{y1:.0f},x2:{x2:.0f},y2:{y2:.0f}]")
            # 生成画了检测框的标注图，作为消息回显到聊天窗口
            try:
                boxed_path = os.path.splitext(img_path)[0] + "_boxed.png"
                cv2.imwrite(boxed_path, res.plot())
            except Exception as e:
                print(f"[YOLO] 标注图保存失败: {e}")
                boxed_path = None
        return ("。".join(info_list) if info_list else "未检测到目标"), boxed_path
    except ImportError:
        return "未安装ultralytics库，已跳过YOLO检测", None
    except Exception as e:
        return f"YOLO检测失败：{e}", None


def take_photo():
    """拍照：OpenCV调用摄像头，捕获一帧画面并返回PIL图片"""
    if cv2 is None:
        raise gr.Error("OpenCV 未安装，无法使用摄像头。请执行: pip install opencv-python")
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        raise gr.Error("无法打开摄像头！请检查摄像头是否被其他应用占用")

    ret, frame = cap.read()
    cap.release()

    if not ret:
        raise gr.Error("拍照失败！未能获取摄像头画面")

    # OpenCV读取的是BGR，转为RGB
    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(frame_rgb)
    print(f"[拍照成功] 图片尺寸: {pil_img.size}")
    return pil_img


# ====================== 语音对话（本地Whisper ASR + Windows TTS，免费离线） ======================
# 智谱语音模型(glm-asr/cogtts)需要付费余额，这里用本地方案：
# ASR = faster-whisper(首次自动从国内镜像下载~145MB模型)，TTS = Windows自带中文语音
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")  # whisper模型走国内镜像
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")               # 镜像不支持xet下载协议
_whisper_model = None  # Whisper模型缓存：只加载一次


def voice_to_text(audio_path):
    """本地faster-whisper语音转文字"""
    global _whisper_model
    if not audio_path:
        return ""
    try:
        if _whisper_model is None:
            from faster_whisper import WhisperModel
            print("[语音] 加载Whisper base模型（首次较慢）...")
            _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
        segs, _ = _whisper_model.transcribe(audio_path, language="zh", beam_size=5)
        text = "".join(s.text for s in segs).strip()
        # whisper base 常输出繁体 → 转成简体（不能用initial_prompt引导，会带偏识别）
        try:
            from zhconv import convert
            text = convert(text, "zh-cn")
        except ImportError:
            pass
        print(f"[语音识别] {text}")
        return text
    except ImportError:
        print("[语音识别] 未安装faster-whisper，请执行: pip install faster-whisper")
        return ""
    except Exception as e:
        print(f"[语音识别] 失败: {e}")
        return ""


def text_to_speech(text):
    """Windows自带中文语音合成，返回wav文件路径（失败返回None）"""
    try:
        # 去掉markdown符号、emoji和链接，只朗读纯文本，限长300字
        clean = re.sub(r"[#*`>\[\]()!【】|]", "", text)
        clean = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️]", "", clean)
        clean = re.sub(r"http\S+", "", clean).strip()[:300]
        if not clean:
            return None
        os.makedirs(UPLOAD_CACHE_DIR, exist_ok=True)
        out_path = os.path.join(UPLOAD_CACHE_DIR, f"tts_{uuid.uuid4().hex[:8]}.wav")
        txt_path = out_path + ".txt"
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(clean)
        ps = ("Add-Type -AssemblyName System.Speech; "
              f"$t = Get-Content -Path '{txt_path}' -Raw -Encoding UTF8; "
              "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              f"$s.SetOutputToWaveFile('{out_path}'); $s.Speak($t); $s.Dispose()")
        import subprocess
        subprocess.run(["powershell", "-Command", ps], timeout=60, capture_output=True)
        os.remove(txt_path)
        return out_path if os.path.exists(out_path) else None
    except Exception as e:
        print(f"[语音合成] 失败: {e}")
        return None


def on_voice_input(audio_path):
    """麦克风录音结束 → 识别成文字填入输入框，由用户确认/修改后自己点发送"""
    if not audio_path:
        return gr.skip(), None
    text = voice_to_text(audio_path)
    if not text:
        # 失败必须让用户看见，不能静默没动作
        gr.Warning("语音识别没有得到内容：请确认运行环境已安装 faster-whisper"
                   "（pip install faster-whisper zhconv），或录音时离麦克风近一点、说长一点")
        return gr.skip(), None
    return text, None  # 识别文字进输入框；清空录音条


def tts_reply(enabled, chat_history):
    """朗读开关开着时，把最新一条AI文字回复合成语音。
    返回 gr.Audio 组件更新（含文件路径+visible），让播放按钮在界面上可见。
    即使浏览器拦截 autoplay，用户也能手动点击播放。"""
    print(f"[TTS] tts_reply 被调用: enabled={enabled}, history_len={len(chat_history) if chat_history else 0}")
    if not enabled or not chat_history:
        return gr.Audio(value=None, visible=False)
    last = chat_history[-1]
    if last.get("role") != "assistant" or not isinstance(last.get("content"), str):
        return gr.Audio(value=None, visible=False)
    audio_path = text_to_speech(last["content"])
    print(f"[TTS] text_to_speech 返回: {audio_path}")
    if audio_path:
        return gr.Audio(value=audio_path, visible=True, autoplay=True)
    return gr.Audio(value=None, visible=False)


# ====================== 多用户登录 ======================
# 教学用：账号写在代码里。正式项目应存数据库并用加盐哈希校验密码
# 用户账号已迁移到 MySQL users 表（db_verify_user / db_create_user）
# 默认用户由 seed_default_users() 在启动时自动创建


# ====================== 创建Agent ======================
def create_session_agent():
    """创建带有记忆功能的Agent"""
    # 注入当前日期：GLM-4的训练数据停在过去，不告诉它今天的日期，
    # 它会认为2026年的事件"还没发生"而拒绝调用web_search
    today = datetime.datetime.now().strftime("%Y年%m月%d日")
    dated_prompt = f"【重要】今天的真实日期是：{today}。\n\n" + system_prompt
    return create_agent(
        model=llm,
        tools=[get_weather, search_baike, send_email_tool, execute_sql_query, web_search, web_fetch],
        system_prompt=dated_prompt,
        checkpointer=checkpointer
    )


# 创建默认Agent
agent = create_session_agent()


# ====================== 处理用户输入（两步事件链） ======================
def add_user_message(user_input, image, camera_image, chat_history):
    """
    第一步（秒回显）：把用户消息立刻显示到聊天窗口并清空输入框，
    耗时的识图和Agent调用放到第二步 bot_respond 里执行
    """
    # 图片优先级：摄像头拍照 > 上传
    image = camera_image if camera_image is not None else image

    if not user_input or not user_input.strip():
        if image is None:
            return "", None, None, chat_history, None
        user_input = "请分析这张图片"  # 只传图不打字时的默认问题

    img_info = get_img_info_from_pil(image) if image is not None else None
    if img_info:
        content = [{"type": "text", "text": user_input},
                   {"type": "image", "path": img_info["full_path"]}]
    else:
        content = user_input
    chat_history.append({"role": "user", "content": content})

    # 把本轮待处理内容暂存给第二步
    pending = {"text": user_input, "img_path": img_info["full_path"] if img_info else None}
    return "", None, None, chat_history, pending


def bot_respond(pending, chat_history, session_id="default", username="public"):
    """
    第二步（耗时，生成器）：识图、路由、流式调用Agent生成回复并持久化。
    每收到一段文字就 yield 一次，实现打字机效果。
    """
    if not pending:
        yield chat_history, session_id, gr.skip(), None
        return

    user_input = pending["text"]
    img_path = pending.get("img_path")

    db_ensure_session(session_id, username=username)
    is_first_exchange = not db_load_messages(session_id)  # 是否是本会话的第一轮对话

    # 持久化用户消息（带图片时存JSON以便重启恢复）
    if img_path:
        db_save_message(session_id, "user",
                        json.dumps(chat_history[-1]["content"], ensure_ascii=False))
    else:
        db_save_message(session_id, "user", user_input)

    # ============ 图片预处理：GLM-4V识别 + YOLO目标检测 ============
    image_desc, yolo_result = "", ""
    if img_path:
        image_desc = pre_recognize_image(Image.open(img_path))
        yolo_result, yolo_boxed = get_yolo_info(img_path)
        print(f"[图像识别] GLM-4V: {image_desc[:60]}... | YOLO: {yolo_result[:80]}")
        if yolo_boxed:
            # 把YOLO画好检测框的标注图先作为一条消息回显（并入库以便重启恢复）
            boxed_content = [{"type": "image", "path": yolo_boxed}]
            chat_history.append({"role": "assistant", "content": boxed_content})
            db_save_message(session_id, "assistant",
                            json.dumps(boxed_content, ensure_ascii=False))
            yield chat_history, gr.skip(), gr.skip(), gr.skip()

    # 占位的AI消息，流式往里填字
    chat_history.append({"role": "assistant", "content": ""})
    response_content = ""

    try:
        # ============ 关键词路由（按优先级：图片 > 天气 > 数据库/邮件/URL > 新闻搜索） ============
        # 注意：天气必须先于新闻判断，否则"今天的天气"会被"今天"劫持到联网搜索
        weather_keywords = ["天气", "气温", "温度", "湿度", "下雨", "降雨", "降雪", "预报", "台风"]
        db_keywords = ["学生", "成绩", "查询", "删除", "修改", "添加", "数据库", "表", "记录"]
        email_keywords = ["发送邮件", "发邮件", "邮件"]
        url_keywords = ["总结", "阅读", "看看", "打开", "这个网页", "这个链接"]
        # 新闻关键词只保留明确表示"要搜新闻/资讯/时效信息"的词。
        # 时间词（2025/2026/最新/今年）可以安全加回：天气和数据库已在前面优先拦截
        news_keywords = [
            "新闻", "资讯", "热点", "头条", "实事", "新消息", "最新消息",
            "最新动态", "最新进展", "最新情况", "最新",
            "最近", "近期", "近日", "发生了什么", "有什么大事", "有什么新",
            "上网查", "联网", "搜索", "帮我搜", "帮我查", "查一下", "搜一下",
            "2025", "2026", "今年", "世界杯", "比赛结果", "四强", "夺冠",
        ]

        is_weather = any(kw in user_input for kw in weather_keywords)
        is_db = any(kw in user_input for kw in db_keywords)
        is_email = any(kw in user_input for kw in email_keywords)
        is_url = "http" in user_input and any(kw in user_input for kw in url_keywords)
        is_news = any(kw in user_input for kw in news_keywords)

        # 统一整理要交给Agent的提示词（新闻快速通道除外）
        agent_prompt = None
        if img_path:
            print(f"[路由] 图像识别: {user_input}")
            agent_prompt = f"""用户上传了一张图片：
GLM-4V对图片的识别结果为：{image_desc}
YOLO对图片的识别结果为：{yolo_result}

用户的问题：{user_input}

请根据上述图片识别出的信息，回答用户的问题。"""
        elif is_weather:
            print(f"[路由] 天气查询: {user_input}")
            agent_prompt = f"请使用get_weather工具处理用户请求（禁止使用web_search）：{user_input}"
        elif is_db or is_email or is_url:
            tool_name = "execute_sql_query" if is_db else ("send_email_tool" if is_email else "web_fetch")
            agent_prompt = f"请使用{tool_name}工具处理用户请求：{user_input}"
        elif not is_news:
            agent_prompt = user_input  # 普通对话

        if agent_prompt is not None:
            # ---- Agent通道：流式输出（stream_mode="messages" 逐token返回） ----
            config = {"configurable": {"thread_id": session_id}}
            try:
                for chunk, meta in agent.stream(
                        {"messages": [HumanMessage(content=agent_prompt)]},
                        config=config, stream_mode="messages"):
                    if type(chunk).__name__ != "AIMessageChunk":
                        continue  # 跳过工具消息等
                    text = chunk.content
                    if isinstance(text, list):  # 兼容分块内容格式
                        text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
                    if not text:
                        continue
                    response_content += text
                    chat_history[-1]["content"] = response_content
                    yield chat_history, gr.skip(), gr.skip(), gr.skip()
            except Exception as e:
                print(f"[流式] 失败，回退为整段输出: {e}")
            if not response_content:
                # 流式没产出（旧版本/异常）→ 回退到一次性invoke
                result = agent.invoke(
                    {"messages": [HumanMessage(content=agent_prompt)]}, config=config)
                response_content = result["messages"][-1].content
        else:
            # ---- 快速通道：直接web_search + 流式总结 ----
            # 清理口语化前缀，提取干净的搜索关键词（避免把"帮我搜一下"当搜索词）
            import re as _re
            clean_query = _re.sub(
                r'^(帮我|请(你)?|麻烦)?(搜一下|搜索一下|搜索|查一下|查一查|查查|查|上网搜一下|上网查一下|联网搜索|联网搜一下)',
                '', user_input).strip()
            if not clean_query:
                clean_query = user_input  # 清理后为空则保留原样
            # 快速通道本质是新闻搜索，给查询加时间前缀确保走 recency 过滤
            recency_words = ["今天", "今日", "昨天", "最新", "最近", "近日", "近期",
                             "刚刚", "实时", "这两天", "这几天", "本周", "这周", "这个月",
                             "现在", "结果", "战况", "今年", "2025", "2026"]
            search_query = clean_query
            if not any(w in search_query for w in recency_words):
                search_query = f"最新 {clean_query}"
            print(f"[快速通道] 联网搜索: {user_input} → 搜索词: {search_query}")
            search_result = web_search.invoke({"query": search_query})
            stream_resp = client.chat.completions.create(
                model="glm-4",
                messages=[
                    {"role": "system", "content": "你是一个信息助手。请基于以下联网搜索结果，用中文简洁回答用户的问题。保留搜索结果中的关键信息和链接。搜索结果中没有链接时不要编造链接。"},
                    {"role": "user", "content": f"用户问题：{user_input}\n\n联网搜索结果：\n{search_result}\n\n请基于以上搜索结果回答用户。"}
                ],
                temperature=0.3,
                stream=True
            )
            for chunk in stream_resp:
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    response_content += delta
                    chat_history[-1]["content"] = response_content
                    yield chat_history, gr.skip(), gr.skip(), gr.skip()

        chat_history[-1]["content"] = response_content
        db_save_message(session_id, "assistant", response_content)

    except Exception as e:
        error_msg = f"处理请求时出错：{str(e)}"
        chat_history[-1]["content"] = error_msg
        db_save_message(session_id, "assistant", error_msg)
        print(f"Error: {e}")

    if is_first_exchange:
        # 第一轮对话结束 → 用LLM总结对话内容生成会话标题（仿DeepSeek）
        title = generate_session_title(user_input, chat_history[-1]["content"])
        db_rename_session(session_id, title)
        print(f"🏷️ 会话标题: {title}")

    dropdown_update = refresh_session_dropdown(username, session_id)
    yield chat_history, session_id, dropdown_update, None


# ====================== 对话记录删除 ======================
def on_msg_select(evt: gr.SelectData):
    """点击聊天窗口中的某条消息 → 记录其序号，供删除按钮使用"""
    idx = evt.index
    if isinstance(idx, (list, tuple)):
        idx = idx[0]
    return idx


def delete_selected_round(selected_idx, chat_history, session_id):
    """删除选中的那一轮对话（用户提问+AI回复），同步删除MySQL记录"""
    if selected_idx is None or not (0 <= selected_idx < len(chat_history)):
        return chat_history, None
    # 定位这一轮的起止（user在前、assistant紧随其后）
    start = selected_idx
    if chat_history[start]["role"] == "assistant" and start > 0 \
            and chat_history[start - 1]["role"] == "user":
        start -= 1
    end = start
    # 一轮回复可能有多条assistant消息（如YOLO标注图+文字回答），全部算进这一轮
    while end + 1 < len(chat_history) and chat_history[end + 1]["role"] == "assistant":
        end += 1
    # 删数据库记录（UI消息顺序与数据库id顺序一致）
    ids = [r[0] for r in db_fetch(
        "SELECT id FROM chat_messages WHERE session_id=%s ORDER BY id", (session_id,))]
    for i in range(start, end + 1):
        if i < len(ids):
            db_exec("DELETE FROM chat_messages WHERE id=%s", (ids[i],))
    del chat_history[start:end + 1]
    print(f"🗑️ 已删除会话 {session_id} 的第{start + 1}~{end + 1}条消息")
    return chat_history, None


# ====================== DeepSeek风格侧边栏 ======================
# 注意：Gradio 6前端(Svelte 5)忽略JS合成事件，不能用"隐藏输入框+dispatchEvent"桥接，
# 必须用官方 @gr.render 动态渲染原生组件（见下方UI部分），CSS只负责外观
SIDEBAR_CSS = """
/* ---- 侧边栏会话列表(仿DeepSeek) ---- */
.ds-group { font-size: 12px; opacity: .55; padding: 8px 6px 2px; user-select: none; }
.ds-item { gap: 2px !important; border-radius: 10px; padding: 1px 4px; align-items: center; }
.ds-item:hover { background: rgba(0, 0, 0, .06); }
.dark .ds-item:hover { background: rgba(255, 255, 255, .08); }
.ds-item.active { background: rgba(77, 107, 254, .12); }
/* 标题按钮：去掉按钮外观，变成一行左对齐文字 */
.ds-title-btn, .ds-title-btn:hover {
    background: none !important; border: none !important; box-shadow: none !important;
    justify-content: flex-start !important; text-align: left !important;
    font-size: 14px !important; font-weight: 400 !important;
    padding: 7px 6px !important; min-width: 0 !important;
    white-space: nowrap !important; overflow: hidden !important; text-overflow: ellipsis !important;
}
.ds-item.active .ds-title-btn { color: #4d6bfe !important; font-weight: 600 !important; }
/* 悬停才出现的操作按钮 */
.ds-op-btn {
    display: none !important; background: none !important; border: none !important;
    box-shadow: none !important; min-width: 28px !important; max-width: 28px !important;
    padding: 5px 2px !important; font-size: 13px !important;
}
.ds-item:hover .ds-op-btn { display: flex !important; }
.ds-op-btn:hover { background: rgba(0, 0, 0, .1) !important; border-radius: 6px !important; }
.dark .ds-op-btn:hover { background: rgba(255, 255, 255, .15) !important; }
/* 删除二次确认按钮（常显，不随悬停消失） */
.ds-del-confirm { min-width: 72px !important; font-size: 12px !important; padding: 5px 4px !important; }

/* ---- 登录页面样式 ---- */
/* 登录卡片：padding-top下推（垂直），Row三列布局（水平居中，不依赖CSS穿透） */
#login-col {
    padding-top: 16vh !important;
}
#login-card {
    background: linear-gradient(135deg, #ffffff 0%, #f8f9fc 100%);
    border-radius: 20px !important; padding: 48px 40px !important;
    box-shadow: 0 8px 40px rgba(0,0,0,.08), 0 2px 8px rgba(0,0,0,.04) !important;
    border: 1px solid rgba(77,107,254,.08) !important;
}
#login-avatar {
    width: 72px; height: 72px; border-radius: 50%;
    background: linear-gradient(135deg, #4d6bfe 0%, #7b5cff 100%);
    margin: 0 auto 20px;
    display: flex; align-items: center; justify-content: center;
    font-size: 36px; color: white;
    box-shadow: 0 4px 16px rgba(77,107,254,.3);
}
#login-title {
    text-align: center; font-size: 26px; font-weight: 700;
    color: #1a1a2e; margin-bottom: 6px;
}
#login-subtitle {
    text-align: center; font-size: 14px; color: #888;
    margin-bottom: 32px;
}
#login-error {
    text-align: center; color: #e74c3c; font-size: 13px;
    margin-top: 8px; min-height: 20px;
}
/* 登录页输入框美化 */
#login-col .gr-textbox input {
    border-radius: 10px !important; padding: 12px 16px !important;
    font-size: 15px !important; border: 1.5px solid #e0e0e0 !important;
    transition: border-color .2s !important;
}
#login-col .gr-textbox input:focus {
    border-color: #4d6bfe !important; box-shadow: 0 0 0 3px rgba(77,107,254,.1) !important;
}
/* 登录/注册按钮 */
#login-btn-row .gr-button-primary {
    background: linear-gradient(135deg, #4d6bfe 0%, #5b4cf0 100%) !important;
    border: none !important; border-radius: 10px !important;
    font-size: 16px !important; font-weight: 600 !important;
    padding: 12px 0 !important; width: 100% !important;
    box-shadow: 0 4px 14px rgba(77,107,254,.35) !important;
    transition: transform .15s, box-shadow .15s !important;
}
#login-btn-row .gr-button-primary:hover {
    transform: translateY(-1px) !important;
    box-shadow: 0 6px 20px rgba(77,107,254,.45) !important;
}
#login-btn-row .gr-button-secondary {
    border: 1.5px solid #d0d0d0 !important; border-radius: 10px !important;
    font-size: 14px !important; padding: 10px 0 !important;
    background: white !important; color: #555 !important;
}
#login-hint {
    text-align: center; font-size: 12px; color: #aaa; margin-top: 24px;
    line-height: 1.8;
}
#login-hint b { color: #666; }
"""


def refresh_session_dropdown(username="public", current_sid=None):
    """刷新会话下拉列表，返回 gr.update() 供回调输出到 session_dropdown"""
    sessions = db_list_sessions(username)
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    choices = []
    for sid, name, created in sessions:
        d = created.date() if isinstance(created, datetime.datetime) else today
        if d == today:
            label = f"今天 {created.strftime('%H:%M')}  {name}"
        elif d == yesterday:
            label = f"昨天 {created.strftime('%H:%M')}  {name}"
        else:
            label = f"{d.strftime('%m-%d')} {created.strftime('%H:%M')}  {name}"
        choices.append((label, sid))
    # 确保 value 是存在的选项
    if current_sid and any(c[1] == current_sid for c in choices):
        value = current_sid
    else:
        value = choices[0][1] if choices else None
    return gr.update(choices=choices, value=value)


# ====================== 会话管理回调 ======================
def init_ui():
    """页面加载时初始化：建表，返回空白状态（用户未登录时等待登录）"""
    init_chat_tables()
    # 未登录时返回空状态，等 do_login 填充
    return gr.update(choices=[]), [], "default", None


def on_new_session(username):
    """开启新对话（标题先叫"新对话"，第一轮对话后由LLM总结生成）"""
    now = datetime.datetime.now()
    sid = f"s{now.strftime('%Y%m%d%H%M%S')}"
    db_ensure_session(sid, "新对话", username)
    print(f"➕ 新建会话: {sid} (用户: {username})")
    dropdown_update = refresh_session_dropdown(username, sid)
    return dropdown_update, [], sid, None, gr.Button(value="🗑️ 删除", variant="secondary")


def generate_session_title(user_msg, ai_reply):
    """用LLM总结第一轮对话，生成简短会话标题（仿DeepSeek）"""
    try:
        resp = client.chat.completions.create(
            model="glm-4",
            messages=[
                {"role": "system",
                 "content": "你是对话标题生成器。根据下面的对话内容，总结生成一个不超过10个字的简短中文标题。只返回标题本身，不要引号、标点、emoji或任何解释。"},
                {"role": "user",
                 "content": f"用户: {user_msg[:200]}\n助手: {ai_reply[:200]}"}
            ],
            temperature=0.3
        )
        title = resp.choices[0].message.content.strip().strip('"\'「」《》')
        return title[:15] if title else user_msg.strip()[:15]
    except Exception as e:
        print(f"[标题生成] 失败，退回用消息前15字: {e}")
        return user_msg.strip()[:15]


def clear_history(session_id="default"):
    """清空当前会话的聊天记录（同时清数据库）"""
    db_exec("DELETE FROM chat_messages WHERE session_id=%s", (session_id,))
    return [], session_id


# ====================== 侧边栏交互回调（Dropdown + 重命名 + 删除） ======================
def on_dropdown_select(sid):
    """下拉框选择会话 → 加载历史记录，同时重置删除按钮"""
    if not sid:
        return gr.skip(), gr.skip(), gr.skip(), gr.skip()
    history = db_load_messages(sid)
    print(f"🔄 切换到会话: {sid}, 历史记录数: {len(history)}")
    return history, sid, None, gr.Button(value="🗑️ 删除", variant="secondary")


def start_rename():
    """显示重命名输入框，同时重置删除按钮"""
    return gr.Textbox(visible=True), None, gr.Button(value="🗑️ 删除", variant="secondary")


def do_rename_session(new_name, sid, username):
    """执行重命名并刷新下拉列表"""
    if not sid:
        return gr.Textbox(visible=False, value=""), gr.skip()
    new_name = (new_name or "").strip()[:50]
    if new_name:
        db_rename_session(sid, new_name)
        print(f"✏️ 会话 {sid} 重命名为: {new_name}")
    dropdown_update = refresh_session_dropdown(username, sid)
    return gr.Textbox(visible=False, value=""), dropdown_update


def on_delete_click(sid, username, pending):
    """删除当前选中的会话（二次确认：第一次改按钮文字，第二次执行删除）"""
    if not sid:
        return gr.skip(), gr.skip(), gr.skip(), gr.skip(), None
    if not pending:
        # 第一次点击 → 要求确认
        return (gr.skip(), gr.skip(), gr.skip(),
                gr.Button(value="⚠️ 确认删除", variant="stop"), sid)
    # 第二次点击 → 执行删除
    db_delete_session(sid)
    print(f"🗑️ 已删除会话: {sid}")
    default_sid = f"default-{username}"
    db_ensure_session(default_sid, "默认会话", username)
    dropdown_update = refresh_session_dropdown(username, default_sid)
    history = db_load_messages(default_sid)
    return (history, default_sid, dropdown_update,
            gr.Button(value="🗑️ 删除", variant="secondary"), None)


# ====================== 获取本机IP ======================
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"


# ====================== 登录/注册/登出回调 ======================
def do_login(username, password):
    """登录校验：查MySQL users表，成功则显示主应用并初始化用户会话"""
    if not username or not username.strip():
        return (gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(),
                "⚠️ 请输入用户名")
    if not password or not password.strip():
        return (gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(),
                "⚠️ 请输入密码")

    success, result = db_verify_user(username, password)
    if not success:
        # result 是错误信息
        return (gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(),
                f"❌ {result}")

    # 登录成功 → 初始化该用户的默认会话
    init_chat_tables()
    uname = result  # db_verify_user 返回的是用户名
    default_sid = f"default-{uname}"
    db_ensure_session(default_sid, "默认会话", uname)
    dropdown_update = refresh_session_dropdown(uname, default_sid)
    history = db_load_messages(default_sid)
    print(f"✅ 用户登录成功: {uname}")

    return (gr.update(visible=False),          # login_col 隐藏
            gr.update(visible=True),           # main_col 显示
            uname,                             # user_state
            dropdown_update,                   # session_dropdown
            history,                           # chatbot
            default_sid,                       # session_state
            "")                                # 清空错误提示


def do_register(username, password):
    """注册新用户：写入MySQL users表，成功则自动登录"""
    if not username or not username.strip():
        return (gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(),
                "⚠️ 请输入用户名（至少2个字符）")
    if not password or not password.strip():
        return (gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(),
                "⚠️ 请输入密码（至少4个字符）")

    success, msg = db_create_user(username, password)
    if not success:
        return (gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(),
                f"❌ {msg}")

    # 注册成功 → 自动登录
    print(f"✅ 新用户注册并登录: {username}")
    init_chat_tables()
    uname = username.strip().lower()
    default_sid = f"default-{uname}"
    db_ensure_session(default_sid, "默认会话", uname)
    dropdown_update = refresh_session_dropdown(uname, default_sid)
    history = db_load_messages(default_sid)

    return (gr.update(visible=False),          # login_col 隐藏
            gr.update(visible=True),           # main_col 显示
            uname,                             # user_state
            dropdown_update,                   # session_dropdown
            history,                           # chatbot
            default_sid,                       # session_state
            "")                                # 清空错误提示


def do_logout(username):
    """退出登录：隐藏主应用，显示登录页，清空状态"""
    print(f"🚪 用户登出: {username}")
    return (gr.update(visible=True),           # login_col 显示
            gr.update(visible=False),          # main_col 隐藏
            None,                              # user_state → None
            gr.update(choices=[]),             # session_dropdown 清空
            [],                                # chatbot 清空
            "default",                         # session_state 重置
            "",                                # login_username 清空
            "",                                # login_password 清空
            "")                                # login_error 清空


# ====================== Gradio界面 ======================
with gr.Blocks(title="智慧校园系统") as demo:
    # ====================== 全局状态 ======================
    session_state = gr.State("default")            # 当前会话ID
    pending_state = gr.State(None)                 # 两步事件链的中转（用户输入+图片路径）
    selected_msg = gr.State(None)                  # 聊天窗口中选中的消息序号
    user_state = gr.State(None)                    # 当前登录用户名（None=未登录，登录后为username）
    delete_pending = gr.State(None)                # 删除二次确认：None=正常，sid=等待确认

    # ====================== 登录页面 ======================
    with gr.Column(visible=True, elem_id="login-col") as login_col:
        # 登录页装饰：头像图标 + 标题（垂直居中由 #login-col CSS padding-top 控制）
        gr.HTML("""<div style="text-align:center">
            <div id="login-avatar">🏫</div>
            <div id="login-title">智慧校园系统</div>
            <div id="login-subtitle">AI 智能校园助手 · 请登录</div>
        </div>""")
        # 用 Row 三列布局居中登录卡片（左占位 : 卡片 : 右占位），不依赖 CSS 穿透
        with gr.Row():
            with gr.Column(scale=1):
                pass  # 左侧占位
            with gr.Column(scale=2, elem_id="login-card"):
                login_username = gr.Textbox(
                    label="👤 用户名", placeholder="请输入用户名",
                    show_label=True, interactive=True, elem_id="login-username"
                )
                login_password = gr.Textbox(
                    label="🔒 密码", placeholder="请输入密码", type="password",
                    show_label=True, interactive=True, elem_id="login-password"
                )
                with gr.Row(elem_id="login-btn-row"):
                    login_btn = gr.Button("🚀 登 录", variant="primary", scale=3)
                    register_btn = gr.Button("📝 注 册", variant="secondary", scale=1)
                login_error = gr.Markdown("", elem_id="login-error", visible=True)
                gr.Markdown(
                    "🔑 **测试账号** &nbsp; `admin` / `admin123` &emsp; `zhangsan` / `123456` &emsp; `lisi` / `123456`",
                    elem_id="login-hint"
                )
            with gr.Column(scale=1):
                pass  # 右侧占位

    # ====================== 主应用（登录后显示） ======================
    with gr.Column(visible=False) as main_col:
        gr.Markdown("""
        # 🏫 智慧校园系统（带数据库+联网功能）

        ## 📌 功能介绍
        - 🌤️ **天气查询**：输入城市名称获取天气信息
        - 📚 **百科查询**：输入名词获取百科介绍
        - 📧 **邮件发送**：输入"发送邮件：主题为XXX，内容为XXX"
        - 💾 **数据库操作**：查询、添加、修改、删除学生和成绩信息
        - 🌐 **联网搜索**：搜索实时新闻、最新资讯、热点话题
        - 📄 **网页抓取**：提供URL链接，自动抓取并总结网页内容
        - 🖼️ **图像识别**：上传图片或摄像头拍照，GLM-4V + YOLO 双引擎识别分析
        - 💬 **智能对话**：支持日常问题解答和记忆功能

        ## 📊 数据库功能示例
        - "查询所有人工智能专业的学生"
        - "查询张三的成绩"
        - "删除学生谢芳"
        - "添加一个新学生，姓名李四，专业计算机"
        - "修改张三的邮箱为zhangsan@qq.com"
        - "统计每个专业的学生人数"
        - "查询所有学生的平均成绩"

        ## 🌐 联网功能示例
        - "搜索今天的最新科技新闻"
        - "帮我查一下ChatGPT最新动态"
        - "最近有什么热点新闻"
        - "总结这个网页 https://example.com"
        """)

        # ---------- 左侧：会话管理栏 ----------
        with gr.Sidebar(label="历史对话", open=True, width=280):
            new_session_btn = gr.Button("➕ 开启新对话", variant="primary")

            # 用 Dropdown 代替 @gr.render 里的按钮列表，规避 Gradio 6 动态组件事件丢失问题
            session_dropdown = gr.Dropdown(
                label="会话列表",
                choices=[],
                value=None,
                interactive=True,
                allow_custom_value=False,
            )

            with gr.Row():
                rename_btn = gr.Button("✏️ 重命名", size="sm", scale=1)
                delete_btn = gr.Button("🗑️ 删除", size="sm", scale=1)

            rename_box = gr.Textbox(
                placeholder="输入新名称，按回车确认",
                visible=False,
                show_label=False,
            )

            clear_btn = gr.Button("🧹 清空当前会话聊天记录", variant="secondary", size="sm")

            logout_btn = gr.Button("🚪 退出登录（切换用户）", variant="stop", size="sm")

        # ---------- 右侧：聊天区 ----------
        chatbot = gr.Chatbot(
            height=500,
            label="对话窗口"
        )
        delete_msg_btn = gr.Button("🗑️ 删除选中的那轮对话（先点击上方聊天记录中的某条消息）",
                                   size="sm", variant="secondary")
        with gr.Row():
            with gr.Column(scale=1, min_width=160):
                image_upload = gr.Image(label="🖼️ 图片上传", type="pil", height=150)
            with gr.Column(scale=1, min_width=160):
                camera_img = gr.Image(label="📷 摄像头", type="pil", height=110)
                capture_btn = gr.Button("📸 拍照", size="sm")
            with gr.Column(scale=4):
                user_input = gr.Textbox(
                    label="输入您的问题",
                    placeholder="请输入您的问题，可配合左侧图片上传/拍照进行图像识别",
                    lines=2
                )
                send_btn = gr.Button("📤 发送", variant="primary", size="lg")
        with gr.Row():
            mic_audio = gr.Audio(sources=["microphone"], type="filepath",
                                 label="🎤 语音提问（录完自动转文字，确认后点发送）", scale=2)
            with gr.Column(scale=1, min_width=200):
                tts_enable = gr.Checkbox(label="🔊 朗读AI回复", value=False)
                tts_player = gr.Audio(label="AI语音（朗读开关打开后自动生成，可手动播放）",
                                      autoplay=True, visible=False)

        # 页面加载：建表 + 为已登录用户加载默认会话历史
        demo.load(
            fn=init_ui,
            outputs=[session_dropdown, chatbot, session_state, user_state]
        )

        new_session_btn.click(
            fn=on_new_session,
            inputs=[user_state],
            outputs=[session_dropdown, chatbot, session_state, delete_pending, delete_btn]
        )

        # 下拉框选择会话 → 加载历史（同时重置删除按钮状态）
        session_dropdown.change(
            fn=on_dropdown_select,
            inputs=[session_dropdown],
            outputs=[chatbot, session_state, delete_pending, delete_btn]
        )

        # 重命名：显示输入框 → 回车确认（同时重置删除按钮状态）
        rename_btn.click(fn=start_rename, outputs=[rename_box, delete_pending, delete_btn])
        rename_box.submit(
            fn=do_rename_session,
            inputs=[rename_box, session_state, user_state],
            outputs=[rename_box, session_dropdown]
        )

        # 删除：二次确认 → 执行删除并切换到默认会话
        delete_btn.click(
            fn=on_delete_click,
            inputs=[session_state, user_state, delete_pending],
            outputs=[chatbot, session_state, session_dropdown, delete_btn, delete_pending]
        )

        # 发送消息：事件链 —— 用户消息秒回显 → 流式生成回复 → (可选)朗读回复
        user_input.submit(
            fn=add_user_message,
            inputs=[user_input, image_upload, camera_img, chatbot],
            outputs=[user_input, image_upload, camera_img, chatbot, pending_state]
        ).then(
            fn=bot_respond,
            inputs=[pending_state, chatbot, session_state, user_state],
            outputs=[chatbot, session_state, session_dropdown, pending_state]
        ).then(
            fn=tts_reply,
            inputs=[tts_enable, chatbot],
            outputs=[tts_player]
        )

        send_btn.click(
            fn=add_user_message,
            inputs=[user_input, image_upload, camera_img, chatbot],
            outputs=[user_input, image_upload, camera_img, chatbot, pending_state]
        ).then(
            fn=bot_respond,
            inputs=[pending_state, chatbot, session_state, user_state],
            outputs=[chatbot, session_state, session_dropdown, pending_state]
        ).then(
            fn=tts_reply,
            inputs=[tts_enable, chatbot],
            outputs=[tts_player]
        )

        # 语音提问：录音结束 → Whisper转文字填入输入框，由用户确认后自己点发送
        mic_audio.stop_recording(
            fn=on_voice_input,
            inputs=[mic_audio],
            outputs=[user_input, mic_audio]
        )

        # 对话记录删除：点击某条消息选中 → 点删除按钮删掉该轮问答（UI+MySQL）
        chatbot.select(fn=on_msg_select, outputs=[selected_msg])
        delete_msg_btn.click(
            fn=delete_selected_round,
            inputs=[selected_msg, chatbot, session_state],
            outputs=[chatbot, selected_msg]
        )

        # 拍照按钮：调用摄像头拍一帧，显示到摄像头框
        capture_btn.click(fn=take_photo, outputs=[camera_img])

        clear_btn.click(
            fn=clear_history,
            inputs=[session_state],
            outputs=[chatbot, session_state]
        )

        # 退出登录 → 隐藏主应用，显示登录页
        logout_btn.click(
            fn=do_logout,
            inputs=[user_state],
            outputs=[login_col, main_col, user_state, session_dropdown, chatbot, session_state,
                     login_username, login_password, login_error]
        )

        gr.Examples(
            examples=[
                ["🌤️ 长沙今天的天气怎么样？"],
                ["📚 人工智能是什么？"],
                ["📧 发送邮件：主题为测试邮件，内容为这是一封测试邮件"],
                ["💾 查询所有人工智能专业的学生"],
                ["💾 查询张三的成绩"],
                ["💾 删除学生谢芳"],
                ["💾 添加一个新学生，姓名王五，学号2024001，专业计算机"],
                ["💾 统计每个专业的学生人数"],
                ["💾 查询所有学生的平均成绩"],
                ["🌐 搜索今天的最新科技新闻"],
                ["🌐 帮我查一下最近有什么热点新闻"],
                ["🧠 我叫张三，是一名大四学生"],
                ["🧠 你还记得我叫什么吗？"]
            ],
            inputs=[user_input],
            label="📝 示例问题"
        )

    # ---- 登录页事件绑定 ----
    # 回车登录：用户名或密码输入框按回车 → 触发登录
    login_username.submit(
        fn=do_login,
        inputs=[login_username, login_password],
        outputs=[login_col, main_col, user_state, session_dropdown, chatbot, session_state,
                 login_error]
    )
    login_password.submit(
        fn=do_login,
        inputs=[login_username, login_password],
        outputs=[login_col, main_col, user_state, session_dropdown, chatbot, session_state,
                 login_error]
    )
    login_btn.click(
        fn=do_login,
        inputs=[login_username, login_password],
        outputs=[login_col, main_col, user_state, session_dropdown, chatbot, session_state,
                 login_error]
    )
    register_btn.click(
        fn=do_register,
        inputs=[login_username, login_password],
        outputs=[login_col, main_col, user_state, session_dropdown, chatbot, session_state,
                 login_error]
    )

# ====================== 启动前配置检查 ======================
def check_config():
    """检查关键配置，返回 (是否可启动, 警告信息列表)"""
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


if __name__ == "__main__":
    print("\n" + "=" * 55)
    print("  🏫 智慧校园系统 — 启动检查")
    print("=" * 55)

    errors, warnings = check_config()

    if errors:
        print("\n" + "=" * 55)
        print("  🚫 缺少必要配置，应用无法启动：")
        for e in errors:
            print(f"     {e}")
        print("=" * 55)
        print("\n💡 解决方法：")
        print("   1. 复制 .env.example 为 .env")
        print("   2. 编辑 .env，至少填入 zhipuai_api_key")
        print("   3. 或者手动设置环境变量（见 README.md）")
        print(f"   配置文件路径: {os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')}")
        sys.exit(1)

    has_warnings = bool(warnings)
    if warnings:
        print("\n  ⚠️  部分可选功能不可用（不影响核心对话）：")
        for w in warnings:
            print(f"     {w}")
        print()

    local_ip = get_local_ip()

    # 初始化聊天记录表 + 用户表 + 默认用户
    init_chat_tables()
    init_users_table()
    seed_default_users()

    print("\n" + "=" * 55)
    print("  🚀 智慧校园系统启动中...")
    print("=" * 55)
    print(f"  📡 本地访问: http://127.0.0.1:7860")
    print(f"  📡 局域网访问: http://{local_ip}:7860")
    print("=" * 55)
    print("  🔐 测试账号（存储在MySQL users表）:")
    print("     admin / admin123")
    print("     zhangsan / 123456")
    print("     lisi / 123456")
    print("     （也可在登录页自行注册新账号）")
    print("=" * 55)

    # 启动时自检语音识别依赖
    try:
        import faster_whisper  # noqa: F401
        print("  🎤 语音识别: faster-whisper 可用")
    except ImportError:
        print("  ⚠️ 语音识别不可用: pip install faster-whisper zhconv")
    print("=" * 55)

    try:
        demo.launch(
            server_name="127.0.0.1",
            server_port=7860,
            share=False,
            debug=False,
            theme=gr.themes.Soft(),
            css=SIDEBAR_CSS,
            quiet=False
        )
    except OSError as e:
        if "Address already in use" in str(e):
            print("\n⚠️ 端口7860已被占用，尝试使用7861端口...")
            demo.launch(
                server_name="127.0.0.1",
                server_port=7861,
                share=False,
                debug=False,
                theme=gr.themes.Soft(),
                css=SIDEBAR_CSS
            )
        else:
            raise e
