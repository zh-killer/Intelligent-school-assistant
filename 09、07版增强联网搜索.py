import json
import os
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

"""
1.应用langchain开发可以使用的工具(天气、百科、邮件、数据库查询)
2.修复原有bug，完善功能
3.兼容Gradio 6.0版本
4.修复启动和网络访问问题
5.添加会话记忆功能，支持多会话隔离
6.添加数据库查询功能，支持中英文专业名称映射
7.添加DuckDuckGo联网搜索功能
"""

# 智谱GLM4 兼容OpenAI接口配置
llm = ChatOpenAI(
    model="glm-4",
    api_key=os.getenv("zhipuai_api_key"),
    base_url=os.getenv("zhipuai_base_url"),
    temperature=0.1,
    timeout=30,
)

client = ZhipuAI()

# 创建全局检查点保存器
checkpointer = InMemorySaver()

# 会话存储
sessions = {}

# ============ 模拟数据库 ============
MOCK_STUDENTS = [
    {"id": 1, "name": "张三", "major": "Artificial Intelligence", "grade": 2022, "email": "zhangsan@example.com"},
    {"id": 2, "name": "李四", "major": "Computer Science", "grade": 2021, "email": "lisi@example.com"},
    {"id": 3, "name": "王五", "major": "Artificial Intelligence", "grade": 2023, "email": "wangwu@example.com"},
    {"id": 4, "name": "赵六", "major": "Software Engineering", "grade": 2022, "email": "zhaoliu@example.com"},
    {"id": 5, "name": "孙七", "major": "Data Science", "grade": 2021, "email": "sunqi@example.com"},
    {"id": 6, "name": "周八", "major": "Artificial Intelligence", "grade": 2023, "email": "zhouba@example.com"},
    {"id": 7, "name": "吴九", "major": "Cyber Security", "grade": 2022, "email": "wujiu@example.com"},
    {"id": 8, "name": "郑十", "major": "Computer Science and Technology", "grade": 2021,
     "email": "zhengshi@example.com"},
]


# ============ 工具函数 ============

# 工具1：天气查询
@tool
def get_weather(location: str, date: str = None) -> str:
    """当用户询问某个城市的天气时，使用此工具获取实时天气信息。"""
    try:
        weather_url = "https://restapi.amap.com/v3/weather/weatherInfo"
        key = "f2509cfd6a2cbab7d0de3b18d5362188"
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


# 工具2：百科查询
@tool
def search_baike(query: str) -> str:
    """当用户询问某个名词、人物、地点的百科信息时，使用此工具搜索百科。"""
    try:
        url = "https://baike.baidu.com/api/lemma"
        params = {
            "lemma": query,
            "format": "json"
        }
        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        if "abstract" in data and data["abstract"]:
            abstract = data["abstract"]
        elif "lemmaAbstract" in data and data["lemmaAbstract"]:
            abstract = data["lemmaAbstract"]
        else:
            search_url = "https://baike.baidu.com/api/lemmalist"
            search_params = {"search": query}
            search_response = requests.get(search_url, params=search_params, timeout=10)
            search_data = search_response.json()
            if search_data.get("lemmaList"):
                first_result = search_data["lemmaList"][0]
                abstract = first_result.get("abstract", f"找到关于{query}的信息，但无法获取详细内容。")
            else:
                return f"未找到关于'{query}'的百科信息。"

        return beautify_baike_output(query, abstract)
    except Exception as e:
        return f"搜索百科信息时出错：{str(e)}"


def beautify_baike_output(query, content):
    """美化百科输出"""
    prompt = f"""
        请将以下关于"{query}"的百科内容整理成易读的格式：

        原始内容：{content}

        请按照以下格式输出：
        📚 关于【{query}】的百科信息：
        【简要介绍】
        【关键信息】
        【相关特点】

        要求：简洁明了，突出关键信息。
    """

    try:
        response = client.chat.completions.create(
            model="glm-4",
            messages=[
                {"role": "system", "content": "你是一个信息整理助手，负责将百科内容整理成易读的格式。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )
        return response.choices[0].message.content
    except:
        return f"📚 关于【{query}】的百科信息：\n{content[:500]}..."


import json
import os
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

"""
1.应用langchain开发可以使用的工具(天气、百科、邮件、数据库查询)
2.修复原有bug，完善功能
3.兼容Gradio 6.0版本
4.修复启动和网络访问问题
5.添加会话记忆功能，支持多会话隔离
6.添加数据库查询功能，支持中英文专业名称映射
7.添加DuckDuckGo联网搜索功能
"""

# 智谱GLM4 兼容OpenAI接口配置
llm = ChatOpenAI(
    model="glm-4",
    api_key=os.getenv("zhipuai_api_key"),
    base_url=os.getenv("zhipuai_base_url"),
    temperature=0.1,
    timeout=30,
)

client = ZhipuAI()

# 创建全局检查点保存器
checkpointer = InMemorySaver()

# 会话存储
sessions = {}

# ============ 模拟数据库 ============
MOCK_STUDENTS = [
    {"id": 1, "name": "张三", "major": "Artificial Intelligence", "grade": 2022, "email": "zhangsan@example.com"},
    {"id": 2, "name": "李四", "major": "Computer Science", "grade": 2021, "email": "lisi@example.com"},
    {"id": 3, "name": "王五", "major": "Artificial Intelligence", "grade": 2023, "email": "wangwu@example.com"},
    {"id": 4, "name": "赵六", "major": "Software Engineering", "grade": 2022, "email": "zhaoliu@example.com"},
    {"id": 5, "name": "孙七", "major": "Data Science", "grade": 2021, "email": "sunqi@example.com"},
    {"id": 6, "name": "周八", "major": "Artificial Intelligence", "grade": 2023, "email": "zhouba@example.com"},
    {"id": 7, "name": "吴九", "major": "Cyber Security", "grade": 2022, "email": "wujiu@example.com"},
    {"id": 8, "name": "郑十", "major": "Computer Science and Technology", "grade": 2021,
     "email": "zhengshi@example.com"},
]


# ============ 工具函数 ============

# 工具1：天气查询
@tool
def get_weather(location: str, date: str = None) -> str:
    """当用户询问某个城市的天气时，使用此工具获取实时天气信息。"""
    try:
        weather_url = "https://restapi.amap.com/v3/weather/weatherInfo"
        key = "f2509cfd6a2cbab7d0de3b18d5362188"
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


# 工具2：百科查询
@tool
def search_baike(query: str) -> str:
    """当用户询问某个名词、人物、地点的百科信息时，使用此工具搜索百科。"""
    try:
        url = "https://baike.baidu.com/api/lemma"
        params = {
            "lemma": query,
            "format": "json"
        }
        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        if "abstract" in data and data["abstract"]:
            abstract = data["abstract"]
        elif "lemmaAbstract" in data and data["lemmaAbstract"]:
            abstract = data["lemmaAbstract"]
        else:
            search_url = "https://baike.baidu.com/api/lemmalist"
            search_params = {"search": query}
            search_response = requests.get(search_url, params=search_params, timeout=10)
            search_data = search_response.json()
            if search_data.get("lemmaList"):
                first_result = search_data["lemmaList"][0]
                abstract = first_result.get("abstract", f"找到关于{query}的信息，但无法获取详细内容。")
            else:
                return f"未找到关于'{query}'的百科信息。"

        return beautify_baike_output(query, abstract)
    except Exception as e:
        return f"搜索百科信息时出错：{str(e)}"


def beautify_baike_output(query, content):
    """美化百科输出"""
    prompt = f"""
        请将以下关于"{query}"的百科内容整理成易读的格式：

        原始内容：{content}

        请按照以下格式输出：
        📚 关于【{query}】的百科信息：
        【简要介绍】
        【关键信息】
        【相关特点】

        要求：简洁明了，突出关键信息。
    """

    try:
        response = client.chat.completions.create(
            model="glm-4",
            messages=[
                {"role": "system", "content": "你是一个信息整理助手，负责将百科内容整理成易读的格式。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )
        return response.choices[0].message.content
    except:
        return f"📚 关于【{query}】的百科信息：\n{content[:500]}..."


# 工具3：DuckDuckGo 联网搜索（新增）
@tool
def web_search(query: str) -> str:
    """
    使用DuckDuckGo搜索互联网上的最新信息。
    当用户询问实时新闻、最新事件、2026年及以后的信息、不确定的信息时使用。
    例如："2026年有什么大事"、"最近有什么新闻"、"特朗普最新的消息"
    """
    try:
        print(f"\n🔍 [DuckDuckGo] 搜索: {query}")

        # 使用DuckDuckGo Instant Answer API
        url = "https://api.duckduckgo.com/"
        params = {
            "q": query,
            "format": "json",
            "no_html": 1,
            "skip_disambig": 1,
            "t": "智慧校园系统"  # 用户代理标识
        }

        response = requests.get(url, params=params, timeout=15)
        data = response.json()

        print(f"📊 [DuckDuckGo] 响应状态: {response.status_code}")

        result_parts = []

        # 1. 获取摘要信息
        if data.get("Abstract"):
            abstract = data["Abstract"]
            result_parts.append(f"📝 {abstract}")
            if data.get("AbstractURL"):
                result_parts.append(f"🔗 来源：{data['AbstractURL']}")
            result_parts.append("")

        # 2. 获取定义（如果有）
        if data.get("Definition"):
            definition = data["Definition"]
            result_parts.append(f"📖 定义：{definition}")
            if data.get("DefinitionURL"):
                result_parts.append(f"🔗 来源：{data['DefinitionURL']}")
            result_parts.append("")

        # 3. 获取相关主题
        if data.get("RelatedTopics"):
            topics = data["RelatedTopics"][:5]  # 取前5个
            result_parts.append("📌 相关信息：")
            for topic in topics:
                if "Text" in topic:
                    # 清理文本（去除HTML标签）
                    text = topic["Text"]
                    # 简单清理
                    text = text.replace("<b>", "").replace("</b>", "")
                    result_parts.append(f"  • {text[:200]}")
                    if "FirstURL" in topic:
                        result_parts.append(f"    🔗 {topic['FirstURL']}")
                    result_parts.append("")

        # 4. 如果没有结果，使用备用方案
        if not result_parts:
            print("⚠️ [DuckDuckGo] 未找到直接结果，尝试备用搜索...")
            return search_alternative(query)

        final_result = "\n".join(result_parts)
        print(f"✅ [DuckDuckGo] 搜索成功，返回 {len(final_result)} 字符")
        return final_result

    except requests.exceptions.Timeout:
        print("❌ [DuckDuckGo] 请求超时")
        return search_alternative(query)
    except Exception as e:
        print(f"❌ [DuckDuckGo] 搜索失败: {str(e)}")
        return search_alternative(query)


def search_alternative(query):
    """备用搜索方案：使用多个来源"""
    results = []

    # 1. 尝试百度百科
    try:
        url = "https://baike.baidu.com/api/lemma"
        params = {"lemma": query, "format": "json"}
        response = requests.get(url, params=params, timeout=5)
        data = response.json()
        if data.get("abstract"):
            results.append(f"📚 百度百科：{data['abstract'][:200]}...")
            if data.get("lemmaUrl"):
                results.append(f"🔗 详细：{data['lemmaUrl']}")
    except:
        pass

    # 2. 尝试维基百科（中文）
    try:
        url = "https://zh.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json",
            "srlimit": 1
        }
        response = requests.get(url, params=params, timeout=5)
        data = response.json()
        if data.get("query", {}).get("search"):
            result = data["query"]["search"][0]
            results.append(f"📚 维基百科：{result['title']}")
            # 清理HTML标签
            snippet = result.get("snippet", "").replace("<span class='searchmatch'>", "").replace("</span>", "")
            results.append(f"   {snippet[:200]}...")
            results.append(f"   🔗 https://zh.wikipedia.org/wiki/{result['title']}")
    except:
        pass

    # 3. 如果还是没有结果
    if not results:
        return f"""
🔍 搜索 '{query}' 未找到直接结果。

💡 建议：
1. 尝试使用更具体的关键词
2. 访问百度搜索：https://www.baidu.com/s?wd={query}
3. 访问Google搜索：https://www.google.com/search?q={query}

📌 提示：我可以帮您搜索百科知识、天气信息、学生信息等。
"""

    return "\n\n".join(results)


# 工具4：发送邮件
@tool
def send_email_tool(subject: str, content: str) -> str:
    """
    当用户明确要求发送邮件、发邮件、发送一封邮件等指令时，使用此工具发送邮件。
    """
    try:
        mail_host = "smtp.qq.com"
        mail_user = "3773353416@qq.com"
        mail_pass = "qfraslbimnbucgee"

        sender = '3773353416@qq.com'
        receivers = ['2419819951@qq.com']

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


# 工具5：查询学生信息（按专业）- 支持智能匹配
@tool
def query_students_by_major(major: str) -> str:
    """
    根据专业名称查询学生详细信息。支持中文、英文或混合查询，会自动智能匹配。
    """
    print(f"\n🔍 [查询] 专业: '{major}'")

    try:
        search_term = major.strip()

        # 1. 精确匹配
        exact_matches = [s for s in MOCK_STUDENTS if s["major"].lower() == search_term.lower()]
        if exact_matches:
            print(f"✅ 精确匹配到 {len(exact_matches)} 条记录")
            return format_student_results(search_term, exact_matches)

        # 2. 包含匹配
        contains_matches = []
        for student in MOCK_STUDENTS:
            if search_term.lower() in student["major"].lower():
                contains_matches.append(student)

        if contains_matches:
            print(f"✅ 包含匹配到 {len(contains_matches)} 条记录")
            matched_majors = set([s["major"] for s in contains_matches])
            print(f"匹配到的专业: {', '.join(matched_majors)}")
            return format_student_results(search_term, contains_matches)

        # 3. AI 语义匹配
        return handle_ai_matching(search_term)

    except Exception as e:
        return f"❌ 查询出错：{str(e)}"


def format_student_results(search_term, results):
    """格式化查询结果"""
    output = f"📚 查询专业 '{search_term}' 的结果：\n"
    output += "=" * 50 + "\n\n"

    # 统计不同专业
    majors = {}
    for student in results:
        major = student["major"]
        if major not in majors:
            majors[major] = []
        majors[major].append(student)

    # 显示结果
    for major, students in majors.items():
        output += f"**专业：{major}**（共 {len(students)} 人）\n"
        for student in students:
            output += f"  - {student['name']}（{student['grade']}级）\n"
        output += "\n"

    return output


def handle_ai_matching(search_term):
    """使用 AI 判断用户想查什么专业"""
    all_majors = list(set([s["major"] for s in MOCK_STUDENTS]))

    prompt = f"""
用户想查询专业："{search_term}"
数据库中的专业列表：{all_majors}

请判断用户最可能想查询的是哪个专业？
只返回最匹配的专业名称，不要其他解释。
如果都不匹配，返回 "None"。
"""

    try:
        response = client.chat.completions.create(
            model="glm-4",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        matched_major = response.choices[0].message.content.strip()
        print(f"🤖 AI 建议匹配：{matched_major}")

        if matched_major != "None" and matched_major in all_majors:
            results = [s for s in MOCK_STUDENTS if s["major"] == matched_major]
            return format_student_results(f"{search_term} → {matched_major}", results)
        else:
            return f"❌ 未找到专业 '{search_term}'，系统中有的专业：{', '.join(all_majors)}"
    except:
        return f"❌ 未找到专业 '{search_term}'，系统中有的专业：{', '.join(all_majors)}"


# 工具6：查询学生信息（按姓名）
@tool
def query_student_by_name(name: str) -> str:
    """
    根据学生姓名查询学生详细信息。
    """
    print(f"\n🔍 [查询] 姓名: '{name}'")

    try:
        results = [s for s in MOCK_STUDENTS if s["name"] == name]

        if not results:
            return f"❌ 未找到名为 '{name}' 的学生。"

        output = f"📚 **{name} 的学生信息**\n"
        output += "=" * 50 + "\n\n"

        for student in results:
            output += f"  - 姓名：{student['name']}\n"
            output += f"  - 专业：{student['major']}\n"
            output += f"  - 年级：{student['grade']}级\n"
            output += f"  - 邮箱：{student['email']}\n"

        return output

    except Exception as e:
        return f"❌ 查询出错：{str(e)}"


# ============ 系统提示 ============
system_prompt = """你是一个智慧校园系统的AI助手。

## 可用工具：
1. get_weather - 查询天气
2. search_baike - 搜索百度百科（仅限百科词条）
3. web_search - 【重要】联网搜索最新信息（DuckDuckGo）
4. send_email_tool - 发送邮件
5. query_students_by_major - 查询学生信息（按专业）
6. query_student_by_name - 查询学生信息（按姓名）

## 重要规则：

### 1. 联网搜索规则（最重要）
- 当用户询问2026年及以后的事情、实时新闻、最新事件时，**必须使用 web_search 工具**
- 百度百科只能查已有的词条，不能查实时新闻
- web_search 可以搜索互联网上的最新信息
- 优先使用 web_search 获取最新信息

### 2. 学生查询规则
当用户提到查询学生信息时，使用对应的查询工具。

### 3. 邮件发送规则
当用户说"发送邮件"、"发邮件"时，使用 send_email_tool 工具。

### 4. 其他
- 记住对话历史，保持上下文连贯
- 如果用户的问题不明确，请询问更多细节
"""


# ============ Agent 和会话管理 ============

def create_session_agent():
    """创建带有记忆功能的Agent"""
    return create_agent(
        model=llm,
        tools=[
            get_weather,
            search_baike,
            web_search,  # 新增DuckDuckGo搜索
            send_email_tool,
            query_students_by_major,
            query_student_by_name
        ],
        system_prompt=system_prompt,
        checkpointer=checkpointer
    )


agent = create_session_agent()


def get_session_history(session_id):
    if session_id not in sessions:
        sessions[session_id] = []
    return sessions[session_id]


def process_user_input(user_input, chat_history, session_id="default"):
    if not user_input or not user_input.strip():
        return "", chat_history, session_id

    session_history = get_session_history(session_id)

    chat_history.append({"role": "user", "content": user_input})
    session_history.append({"role": "user", "content": user_input})

    try:
        print(f"\n{'=' * 60}")
        print(f"📝 用户输入: {user_input}")
        print(f"🆔 会话ID: {session_id}")
        print(f"{'=' * 60}")

        result = agent.invoke(
            {"messages": [HumanMessage(content=user_input)]},
            config={"configurable": {"thread_id": session_id}}
        )

        response_content = result["messages"][-1].content

        # 检查是否调用了工具
        has_tool_call = False
        if "intermediate_steps" in result:
            for step in result["intermediate_steps"]:
                tool_name = step[0].tool
                if tool_name in ["get_weather", "search_baike", "web_search",
                                 "send_email_tool", "query_students_by_major", "query_student_by_name"]:
                    has_tool_call = True
                    print(f"🔧 调用了工具: {tool_name}")
                    break

        if not has_tool_call:
            print("⚠️ 没有调用工具，使用LLM直接回答")
            # 检查是否是实时信息查询
            if any(keyword in user_input for keyword in ["2026", "最新", "新闻", "现在", "当前", "最近"]):
                retry_prompt = f"用户需要查询实时信息，请使用web_search工具处理：{user_input}"
                result = agent.invoke(
                    {"messages": [HumanMessage(content=retry_prompt)]},
                    config={"configurable": {"thread_id": session_id}}
                )
                response_content = result["messages"][-1].content
            else:
                # 普通对话
                context = "\n".join([f"{msg['role']}: {msg['content']}" for msg in session_history[-5:]])
                chat_response = client.chat.completions.create(
                    model="glm-4",
                    messages=[
                        {"role": "system", "content": "你是一个智慧校园系统的AI助手，友好、专业地回答用户的问题。"},
                        {"role": "user", "content": f"对话历史：\n{context}\n\n当前问题：{user_input}"}
                    ],
                    temperature=0.7
                )
                response_content = chat_response.choices[0].message.content

        chat_history.append({"role": "assistant", "content": response_content})
        session_history.append({"role": "assistant", "content": response_content})

    except Exception as e:
        error_msg = f"处理请求时出错：{str(e)}"
        chat_history.append({"role": "assistant", "content": error_msg})
        session_history.append({"role": "assistant", "content": error_msg})
        print(f"❌ 错误: {e}")

    return "", chat_history, session_id


def clear_history(session_id="default"):
    if session_id in sessions:
        sessions[session_id] = []
    return [], session_id


def switch_session(session_id, chat_history):
    if session_id not in sessions:
        sessions[session_id] = []

    session_history = sessions[session_id]
    formatted_history = []
    for msg in session_history:
        formatted_history.append({"role": msg["role"], "content": msg["content"]})

    return formatted_history, session_id


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"


# ============ Gradio界面 ============

with gr.Blocks(title="智慧校园系统", theme=gr.themes.Soft()) as demo:
    gr.Markdown("""
    # 🏫 智慧校园系统（带联网搜索功能）

    ## 🌐 新功能：联网搜索
    - **实时信息查询**：可以搜索2026年及以后的最新事件
    - **智能搜索引擎**：使用DuckDuckGo，免费且保护隐私
    - **多来源备用**：如果DuckDuckGo无结果，自动尝试百度百科和维基百科

    ## 🔍 测试联网搜索
    - "2026年有什么重要事件？"
    - "最近有什么新闻？"
    - "特朗普最新消息"
    - "人工智能最新发展"

    ## 🗄️ 其他功能
    - 天气查询
    - 百科查询
    - 学生信息查询
    - 邮件发送
    """)

    with gr.Row():
        with gr.Column(scale=1):
            chatbot = gr.Chatbot(height=500, label="对话窗口")

    with gr.Row():
        with gr.Column(scale=2):
            session_input = gr.Textbox(
                label="会话ID",
                value="default",
                placeholder="输入会话ID，不同会话之间记忆隔离"
            )
        with gr.Column(scale=1):
            switch_btn = gr.Button("🔄 切换会话", variant="secondary", size="lg")
            clear_btn = gr.Button("🗑️ 清空当前会话", variant="secondary", size="lg")

    with gr.Row():
        with gr.Column(scale=4):
            user_input = gr.Textbox(
                label="输入您的问题",
                placeholder="例如：2026年有什么大事？",
                lines=2
            )
        with gr.Column(scale=1):
            send_btn = gr.Button("📤 发送", variant="primary", size="lg")

    session_state = gr.State("default")

    user_input.submit(
        fn=process_user_input,
        inputs=[user_input, chatbot, session_state],
        outputs=[user_input, chatbot, session_state]
    )

    send_btn.click(
        fn=process_user_input,
        inputs=[user_input, chatbot, session_state],
        outputs=[user_input, chatbot, session_state]
    )

    clear_btn.click(
        fn=clear_history,
        inputs=[session_state],
        outputs=[chatbot, session_state]
    )

    switch_btn.click(
        fn=switch_session,
        inputs=[session_input, chatbot],
        outputs=[chatbot, session_state]
    )

    gr.Examples(
        examples=[
            ["🌐 2026年有什么重要事件？"],
            ["🌐 最近有什么科技新闻？"],
            ["🌐 特朗普最新消息"],
            ["🗄️ 查询人工智能专业的学生"],
            ["🌤️ 长沙今天的天气怎么样？"],
            ["📚 人工智能是什么？"]
        ],
        inputs=[user_input],
        label="📝 示例问题"
    )

if __name__ == "__main__":
    if not os.getenv("zhipuai_api_key"):
        print("⚠️ 警告：未设置 zhipuai_api_key 环境变量")
        print("请设置：set zhipuai_api_key=your_api_key (Windows)")
        print("或：export zhipuai_api_key='your_api_key' (Linux/Mac)")

    local_ip = get_local_ip()

    print("\n" + "=" * 60)
    print("🚀 智慧校园系统启动中...")
    print("=" * 60)
    print(f"📡 本地访问地址: http://127.0.0.1:7860")
    print(f"📡 局域网访问地址: http://{local_ip}:7860")
    print("=" * 60)
    print("🌐 已启用DuckDuckGo联网搜索")
    print("💡 可以询问2026年及以后的事件")
    print("💡 调试信息会显示在控制台")
    print("=" * 60 + "\n")

    try:
        demo.launch(
            server_name="0.0.0.0",
            server_port=7860,
            share=False,
            debug=False,
            theme=gr.themes.Soft(),
            quiet=False
        )
    except OSError as e:
        if "Address already in use" in str(e):
            print("\n❌ 端口7860已被占用，尝试使用其他端口...")
            demo.launch(
                server_name="127.0.0.1",
                server_port=7861,
                share=False,
                debug=False,
                theme=gr.themes.Soft()
            )
        else:
            raise e

def search_alternative(query):
    """备用搜索方案：使用多个来源"""
    results = []

    # 1. 尝试百度百科
    try:
        url = "https://baike.baidu.com/api/lemma"
        params = {"lemma": query, "format": "json"}
        response = requests.get(url, params=params, timeout=5)
        data = response.json()
        if data.get("abstract"):
            results.append(f"📚 百度百科：{data['abstract'][:200]}...")
            if data.get("lemmaUrl"):
                results.append(f"🔗 详细：{data['lemmaUrl']}")
    except:
        pass

    # 2. 尝试维基百科（中文）
    try:
        url = "https://zh.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json",
            "srlimit": 1
        }
        response = requests.get(url, params=params, timeout=5)
        data = response.json()
        if data.get("query", {}).get("search"):
            result = data["query"]["search"][0]
            results.append(f"📚 维基百科：{result['title']}")
            # 清理HTML标签
            snippet = result.get("snippet", "").replace("<span class='searchmatch'>", "").replace("</span>", "")
            results.append(f"   {snippet[:200]}...")
            results.append(f"   🔗 https://zh.wikipedia.org/wiki/{result['title']}")
    except:
        pass

    # 3. 如果还是没有结果
    if not results:
        return f"""
🔍 搜索 '{query}' 未找到直接结果。

💡 建议：
1. 尝试使用更具体的关键词
2. 访问百度搜索：https://www.baidu.com/s?wd={query}
3. 访问Google搜索：https://www.google.com/search?q={query}

📌 提示：我可以帮您搜索百科知识、天气信息、学生信息等。
"""

    return "\n\n".join(results)


# 工具4：发送邮件
@tool
def send_email_tool(subject: str, content: str) -> str:
    """
    当用户明确要求发送邮件、发邮件、发送一封邮件等指令时，使用此工具发送邮件。
    """
    try:
        mail_host = "smtp.qq.com"
        mail_user = "3773353416@qq.com"
        mail_pass = "qfraslbimnbucgee"

        sender = '3773353416@qq.com'
        receivers = ['2419819951@qq.com']

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


# 工具5：查询学生信息（按专业）- 支持智能匹配
@tool
def query_students_by_major(major: str) -> str:
    """
    根据专业名称查询学生详细信息。支持中文、英文或混合查询，会自动智能匹配。
    """
    print(f"\n🔍 [查询] 专业: '{major}'")

    try:
        search_term = major.strip()

        # 1. 精确匹配
        exact_matches = [s for s in MOCK_STUDENTS if s["major"].lower() == search_term.lower()]
        if exact_matches:
            print(f"✅ 精确匹配到 {len(exact_matches)} 条记录")
            return format_student_results(search_term, exact_matches)

        # 2. 包含匹配
        contains_matches = []
        for student in MOCK_STUDENTS:
            if search_term.lower() in student["major"].lower():
                contains_matches.append(student)

        if contains_matches:
            print(f"✅ 包含匹配到 {len(contains_matches)} 条记录")
            matched_majors = set([s["major"] for s in contains_matches])
            print(f"匹配到的专业: {', '.join(matched_majors)}")
            return format_student_results(search_term, contains_matches)

        # 3. AI 语义匹配
        return handle_ai_matching(search_term)

    except Exception as e:
        return f"❌ 查询出错：{str(e)}"


def format_student_results(search_term, results):
    """格式化查询结果"""
    output = f"📚 查询专业 '{search_term}' 的结果：\n"
    output += "=" * 50 + "\n\n"

    # 统计不同专业
    majors = {}
    for student in results:
        major = student["major"]
        if major not in majors:
            majors[major] = []
        majors[major].append(student)

    # 显示结果
    for major, students in majors.items():
        output += f"**专业：{major}**（共 {len(students)} 人）\n"
        for student in students:
            output += f"  - {student['name']}（{student['grade']}级）\n"
        output += "\n"

    return output


def handle_ai_matching(search_term):
    """使用 AI 判断用户想查什么专业"""
    all_majors = list(set([s["major"] for s in MOCK_STUDENTS]))

    prompt = f"""
用户想查询专业："{search_term}"
数据库中的专业列表：{all_majors}

请判断用户最可能想查询的是哪个专业？
只返回最匹配的专业名称，不要其他解释。
如果都不匹配，返回 "None"。
"""

    try:
        response = client.chat.completions.create(
            model="glm-4",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        matched_major = response.choices[0].message.content.strip()
        print(f"🤖 AI 建议匹配：{matched_major}")

        if matched_major != "None" and matched_major in all_majors:
            results = [s for s in MOCK_STUDENTS if s["major"] == matched_major]
            return format_student_results(f"{search_term} → {matched_major}", results)
        else:
            return f"❌ 未找到专业 '{search_term}'，系统中有的专业：{', '.join(all_majors)}"
    except:
        return f"❌ 未找到专业 '{search_term}'，系统中有的专业：{', '.join(all_majors)}"


# 工具6：查询学生信息（按姓名）
@tool
def query_student_by_name(name: str) -> str:
    """
    根据学生姓名查询学生详细信息。
    """
    print(f"\n🔍 [查询] 姓名: '{name}'")

    try:
        results = [s for s in MOCK_STUDENTS if s["name"] == name]

        if not results:
            return f"❌ 未找到名为 '{name}' 的学生。"

        output = f"📚 **{name} 的学生信息**\n"
        output += "=" * 50 + "\n\n"

        for student in results:
            output += f"  - 姓名：{student['name']}\n"
            output += f"  - 专业：{student['major']}\n"
            output += f"  - 年级：{student['grade']}级\n"
            output += f"  - 邮箱：{student['email']}\n"

        return output

    except Exception as e:
        return f"❌ 查询出错：{str(e)}"


# ============ 系统提示 ============
system_prompt = """你是一个智慧校园系统的AI助手。

## 可用工具：
1. get_weather - 查询天气
2. search_baike - 搜索百度百科（仅限百科词条）
3. web_search - 【重要】联网搜索最新信息（DuckDuckGo）
4. send_email_tool - 发送邮件
5. query_students_by_major - 查询学生信息（按专业）
6. query_student_by_name - 查询学生信息（按姓名）

## 重要规则：

### 1. 联网搜索规则（最重要）
- 当用户询问2026年及以后的事情、实时新闻、最新事件时，**必须使用 web_search 工具**
- 百度百科只能查已有的词条，不能查实时新闻
- web_search 可以搜索互联网上的最新信息
- 优先使用 web_search 获取最新信息

### 2. 学生查询规则
当用户提到查询学生信息时，使用对应的查询工具。

### 3. 邮件发送规则
当用户说"发送邮件"、"发邮件"时，使用 send_email_tool 工具。

### 4. 其他
- 记住对话历史，保持上下文连贯
- 如果用户的问题不明确，请询问更多细节
"""


# ============ Agent 和会话管理 ============

def create_session_agent():
    """创建带有记忆功能的Agent"""
    return create_agent(
        model=llm,
        tools=[
            get_weather,
            search_baike,
            web_search,  # 新增DuckDuckGo搜索
            send_email_tool,
            query_students_by_major,
            query_student_by_name
        ],
        system_prompt=system_prompt,
        checkpointer=checkpointer
    )


agent = create_session_agent()


def get_session_history(session_id):
    if session_id not in sessions:
        sessions[session_id] = []
    return sessions[session_id]


def process_user_input(user_input, chat_history, session_id="default"):
    if not user_input or not user_input.strip():
        return "", chat_history, session_id

    session_history = get_session_history(session_id)

    chat_history.append({"role": "user", "content": user_input})
    session_history.append({"role": "user", "content": user_input})

    try:
        print(f"\n{'=' * 60}")
        print(f"📝 用户输入: {user_input}")
        print(f"🆔 会话ID: {session_id}")
        print(f"{'=' * 60}")

        result = agent.invoke(
            {"messages": [HumanMessage(content=user_input)]},
            config={"configurable": {"thread_id": session_id}}
        )

        response_content = result["messages"][-1].content

        # 检查是否调用了工具
        has_tool_call = False
        if "intermediate_steps" in result:
            for step in result["intermediate_steps"]:
                tool_name = step[0].tool
                if tool_name in ["get_weather", "search_baike", "web_search",
                                 "send_email_tool", "query_students_by_major", "query_student_by_name"]:
                    has_tool_call = True
                    print(f"🔧 调用了工具: {tool_name}")
                    break

        if not has_tool_call:
            print("⚠️ 没有调用工具，使用LLM直接回答")
            # 检查是否是实时信息查询
            if any(keyword in user_input for keyword in ["2026", "最新", "新闻", "现在", "当前", "最近"]):
                retry_prompt = f"用户需要查询实时信息，请使用web_search工具处理：{user_input}"
                result = agent.invoke(
                    {"messages": [HumanMessage(content=retry_prompt)]},
                    config={"configurable": {"thread_id": session_id}}
                )
                response_content = result["messages"][-1].content
            else:
                # 普通对话
                context = "\n".join([f"{msg['role']}: {msg['content']}" for msg in session_history[-5:]])
                chat_response = client.chat.completions.create(
                    model="glm-4",
                    messages=[
                        {"role": "system", "content": "你是一个智慧校园系统的AI助手，友好、专业地回答用户的问题。"},
                        {"role": "user", "content": f"对话历史：\n{context}\n\n当前问题：{user_input}"}
                    ],
                    temperature=0.7
                )
                response_content = chat_response.choices[0].message.content

        chat_history.append({"role": "assistant", "content": response_content})
        session_history.append({"role": "assistant", "content": response_content})

    except Exception as e:
        error_msg = f"处理请求时出错：{str(e)}"
        chat_history.append({"role": "assistant", "content": error_msg})
        session_history.append({"role": "assistant", "content": error_msg})
        print(f"❌ 错误: {e}")

    return "", chat_history, session_id


def clear_history(session_id="default"):
    if session_id in sessions:
        sessions[session_id] = []
    return [], session_id


def switch_session(session_id, chat_history):
    if session_id not in sessions:
        sessions[session_id] = []

    session_history = sessions[session_id]
    formatted_history = []
    for msg in session_history:
        formatted_history.append({"role": msg["role"], "content": msg["content"]})

    return formatted_history, session_id


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"


# ============ Gradio界面 ============

with gr.Blocks(title="智慧校园系统", theme=gr.themes.Soft()) as demo:
    gr.Markdown("""
    # 🏫 智慧校园系统（带联网搜索功能）

    ## 🌐 新功能：联网搜索
    - **实时信息查询**：可以搜索2026年及以后的最新事件
    - **智能搜索引擎**：使用DuckDuckGo，免费且保护隐私
    - **多来源备用**：如果DuckDuckGo无结果，自动尝试百度百科和维基百科

    ## 🔍 测试联网搜索
    - "2026年有什么重要事件？"
    - "最近有什么新闻？"
    - "特朗普最新消息"
    - "人工智能最新发展"

    ## 🗄️ 其他功能
    - 天气查询
    - 百科查询
    - 学生信息查询
    - 邮件发送
    """)

    with gr.Row():
        with gr.Column(scale=1):
            chatbot = gr.Chatbot(height=500, label="对话窗口")

    with gr.Row():
        with gr.Column(scale=2):
            session_input = gr.Textbox(
                label="会话ID",
                value="default",
                placeholder="输入会话ID，不同会话之间记忆隔离"
            )
        with gr.Column(scale=1):
            switch_btn = gr.Button("🔄 切换会话", variant="secondary", size="lg")
            clear_btn = gr.Button("🗑️ 清空当前会话", variant="secondary", size="lg")

    with gr.Row():
        with gr.Column(scale=4):
            user_input = gr.Textbox(
                label="输入您的问题",
                placeholder="例如：2026年有什么大事？",
                lines=2
            )
        with gr.Column(scale=1):
            send_btn = gr.Button("📤 发送", variant="primary", size="lg")

    session_state = gr.State("default")

    user_input.submit(
        fn=process_user_input,
        inputs=[user_input, chatbot, session_state],
        outputs=[user_input, chatbot, session_state]
    )

    send_btn.click(
        fn=process_user_input,
        inputs=[user_input, chatbot, session_state],
        outputs=[user_input, chatbot, session_state]
    )

    clear_btn.click(
        fn=clear_history,
        inputs=[session_state],
        outputs=[chatbot, session_state]
    )

    switch_btn.click(
        fn=switch_session,
        inputs=[session_input, chatbot],
        outputs=[chatbot, session_state]
    )

    gr.Examples(
        examples=[
            ["🌐 2026年有什么重要事件？"],
            ["🌐 最近有什么科技新闻？"],
            ["🌐 特朗普最新消息"],
            ["🗄️ 查询人工智能专业的学生"],
            ["🌤️ 长沙今天的天气怎么样？"],
            ["📚 人工智能是什么？"]
        ],
        inputs=[user_input],
        label="📝 示例问题"
    )

if __name__ == "__main__":
    if not os.getenv("zhipuai_api_key"):
        print("⚠️ 警告：未设置 zhipuai_api_key 环境变量")
        print("请设置：set zhipuai_api_key=your_api_key (Windows)")
        print("或：export zhipuai_api_key='your_api_key' (Linux/Mac)")

    local_ip = get_local_ip()

    print("\n" + "=" * 60)
    print("🚀 智慧校园系统启动中...")
    print("=" * 60)
    print(f"📡 本地访问地址: http://127.0.0.1:7860")
    print(f"📡 局域网访问地址: http://{local_ip}:7860")
    print("=" * 60)
    print("🌐 已启用DuckDuckGo联网搜索")
    print("💡 可以询问2026年及以后的事件")
    print("💡 调试信息会显示在控制台")
    print("=" * 60 + "\n")

    try:
        demo.launch(
            server_name="0.0.0.0",
            server_port=7860,
            share=False,
            debug=False,
            theme=gr.themes.Soft(),
            quiet=False
        )
    except OSError as e:
        if "Address already in use" in str(e):
            print("\n❌ 端口7860已被占用，尝试使用其他端口...")
            demo.launch(
                server_name="127.0.0.1",
                server_port=7861,
                share=False,
                debug=False,
                theme=gr.themes.Soft()
            )
        else:
            raise e