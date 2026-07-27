# ====================== 6 个 LangChain 工具 ======================
import json
import os
import re
import requests
import smtplib
from email.mime.text import MIMEText
from bs4 import BeautifulSoup
from langchain_core.tools import tool
import llm_config
import security
import database


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
    instruction = "任务：查询指定城市的天气情况，给出友好的天气播报和出行建议。"
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
        response = llm_config.client.chat.completions.create(
            model="glm-4",
            messages=[
                {"role": "system", "content": "你是一个友好的天气播报助手，要根据天气情况给出合理的出行建议。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )
        return response.choices[0].message.content
    except Exception:
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
        response = llm_config.llm.invoke([
            ("system", sql_prompt),
            ("user", query)
        ])

        sql = response.content.strip()
        sql = sql.replace("```sql", "").replace("```", "").strip()

        sql_match = re.search(r"(SELECT|INSERT|UPDATE|DELETE).*", sql, re.I | re.DOTALL)
        if sql_match:
            sql = sql_match.group(0).strip()

        if not sql or not sql.upper().startswith(("SELECT", "INSERT", "UPDATE", "DELETE")):
            return "错误：无法生成合法的SQL语句，请重新描述您的问题。"

        # 安全检查：禁止危险操作
        is_safe, reason = security._validate_sql(sql)
        if not is_safe:
            print(f"[SQL安全拦截] {reason}: {sql}")
            return f"❌ 操作被安全策略拦截：{reason}"

        print(f"[生成SQL] {sql}")
        result = database.query_db(sql)

        if sql.strip().lower().startswith("select"):
            return f"📊 查询结果：\n{result}"
        else:
            return f"✅ {result}"

    except Exception as e:
        return f"❌ 数据库操作失败：{str(e)}"


# ====================== 工具5：联网搜索（智谱Web Search API为主，Bing降级） ======================
@tool
def web_search(query: str, max_results: int = 5) -> str:
    """当用户询问实时新闻、最新资讯、今日热点、赛事结果、某个话题的最新动态等需要联网搜索的内容时，使用此工具进行互联网搜索。
    注意：查询天气请使用 get_weather 工具，不要使用本工具。
    参数：query - 搜索关键词，max_results - 返回结果数量（默认5条）"""
    recency_words = ["今天", "今日", "昨天", "最新", "最近", "近日", "近期",
                     "刚刚", "实时", "这两天", "这几天", "本周", "这周", "这个月",
                     "现在", "结果", "战况", "今年", "2025", "2026"]
    is_recent = any(w in query for w in recency_words)

    # 主通道：智谱 Web Search API
    for recency in (["oneWeek", None] if is_recent else [None]):
        try:
            kwargs = dict(search_engine="search_std", search_query=query, count=max_results,
                          search_intent=True)
            if recency:
                kwargs["search_recency_filter"] = recency
            resp = llm_config.client.web_search.web_search(**kwargs)
            results = []
            for r in (resp.search_result or [])[:max_results]:
                date = getattr(r, 'publish_date', '') or ''
                content = (getattr(r, 'content', '') or '')[:200]
                link = getattr(r, 'link', '') or ''
                results.append(f"{len(results)+1}. **{r.title}**（{date}）\n   🔗 {link}\n   📝 {content}")
            if results:
                scope = "最近一周" if recency else "全部时间"
                return f"🔍 关于「{query}」的搜索结果（{scope}）：\n\n" + "\n\n".join(results)
        except Exception as e:
            print(f"[web_search] 智谱搜索失败（recency={recency}）: {e}")
            break

    # 降级通道：Bing 网页抓取
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Accept': 'text/html,application/xhtml+xml',
        }
        encoded_query = requests.utils.quote(query)
        search_url = (f"https://www.bing.com/search?q={encoded_query}"
                      f"&setlang=zh-cn&count={max_results}&filters=ex1%3a%22ez2%22")
        resp = requests.get(search_url, headers=headers, timeout=15)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')

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
    # SSRF 防护
    if not security._is_safe_url(url):
        return "❌ 安全限制：不允许访问内网地址或不支持的协议，仅支持公网 HTTP/HTTPS 链接。"
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        response = requests.get(url, headers=headers, timeout=15)
        response.encoding = response.apparent_encoding

        soup = BeautifulSoup(response.text, 'html.parser')

        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
            tag.decompose()

        text = soup.get_text(separator='\n', strip=True)
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        text = '\n'.join(lines)

        if len(text) > 4000:
            text = text[:4000] + "..."

        summary_prompt = f"请总结以下网页内容的关键信息，用简洁的中文列出要点：\n\n{text}"
        ai_response = llm_config.client.chat.completions.create(
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
