import json
import os
import re
import smtplib
from email.mime.text import MIMEText

import pymysql
import requests
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from zhipuai import ZhipuAI
import gradio as gr

"""
案例所涉及到的知识点：
1.利用langchain开发可以使用的工具（百科、天气、邮件、数据库）并且集成到gradio web应用
2.一句提示词能够调用2个工具
"""
llm = ChatOpenAI(
    model="glm-5.2",
    api_key=os.getenv("zhipuai_api_key"),
    base_url=os.getenv("zhipuai_base_url"),
    temperature=0.1,  # 设置回答问题的多样化
    timeout=30
)

# 使用函数调用
client = ZhipuAI()


@tool
def get_weather(location):
    """获取天气信息"""
    weather_url = "https://restapi.amap.com/v3/weather/weatherInfo"
    # 调用天气查询的接口所需要的唯一密钥/标识
    key = "a10f43ff68835b549d62365978215e4f"
    # 拼接完整的天气查询接口
    weather_url = f"{weather_url}?key={key}&city={location}"
    # 请求天气接口
    response = requests.get(weather_url)
    # 解析 JSON 数据
    weather_data = json.loads(response.text)
    print("weather_data:", weather_data)
    info = weather_data["lives"][0]
    # 模拟天气 API 调用

    weather_json_out = {
        "location": location,
        "date": info["reporttime"],
        "weather": info['weather'],
        "temperature": f"{info['temperature']}°C",
        "humidity": f"{info['humidity']}%"
    }
    return beutiful_chat_fn(weather_json_out)


@tool
def send_email(subject: str, content: str, receiver: str):
    """
    发送邮件的工具
    :param subject: 邮箱的主题
    :param content: 邮箱的内容
    :param receiver: 邮箱的接收方
    :return:
    """
    # 第三方 SMTP 服务
    mail_host = "smtp.qq.com"  # 设置服务器
    mail_user = "3773353416@qq.com"  # 用户名 这个写你们自己的，模拟的校教务处的邮箱
    mail_pass = "gmzcngpjjznddcgf"  # 口令 这个写你们自己申请的邮箱的授权码

    sender = '3773353416@qq.com'
    receivers = [receiver]  # 接收邮件，可设置为你的QQ邮箱或者其他邮箱（模拟多位学生接收成绩单的邮箱）

    message = MIMEText(content, 'plain', 'utf-8')
    message['From'] = sender
    message['To'] = receivers[0]

    # subject = 'Python SMTP 邮件测试'
    message['Subject'] = subject

    try:
        smtpObj = smtplib.SMTP_SSL(mail_host, 465)
        smtpObj.login(mail_user, mail_pass)
        smtpObj.sendmail(sender, receivers, message.as_string())
        print("邮件发送成功")
    except smtplib.SMTPException as e:
        print("Error: 无法发送邮件", e)
    finally:
        return "邮件发送成功！"


"""
使用智能体，我们希望通过对话，后台能够自动查询数据，完成信息的整合
对话问题：查询所有人工智能的学生信息 -> 返回所有的学生信息

0.通过大模型，理解对话，决定调用哪个工具（数据处理工具）
1.通过大模型，理解对话问题，生成查询语句   select * from student where major = '人工智能'  / 删除谢芳同学 delete from student where student_name = "谢芳" 
2.执行sql语句，结果返回
"""
# ====================== 数据库操作层 ======================
# 数据库配置
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "1234",
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
    cursor = conn.cursor()  # 获取游标

    try:
        cursor.execute(sql, params or ())

        if sql.strip().lower().startswith("select"):
            # 查询操作：返回格式化结果
            rows = cursor.fetchall()
            cols = [desc[0] for desc in cursor.description]
            res = [" | ".join(cols), "-" * 50]
            for row in rows:
                res.append(" | ".join(str(x) for x in row))
            return "\n".join(res)
        else:
            # 非查询操作：提交事务并返回影响行数
            conn.commit()
            return f"操作成功，影响 {cursor.rowcount} 行"
    finally:
        cursor.close()
        conn.close()


# 2. 定义唯一的SQL查询工具
@tool
def execute_sql_query(query: str) -> str:
    """
    执行学生和成绩相关的数据库操作
    支持查询、添加、修改、删除学生信息和成绩
    参数：query - 用户的自然语言问题
    """
    print(f"[工具调用] 执行SQL查询: {query}")

    # SQL生成系统提示词
    sql_prompt = f"""
    你是一个专业的MySQL 8.0 SQL生成器，**只返回合法的SQL语句**。

    表结构：
    students: student_id, student_name, student_no, gender, major, class_name, email, phone
    scores: score_id, student_id, course_name, score, semester, exam_time

    规则：
    1. 查询成绩必须使用JOIN关联students和scores表
    2. 优先使用student_name作为查询条件
    3. 支持AVG、COUNT、SUM等聚合函数
    4. 支持INSERT、UPDATE、DELETE、SELECT所有操作
    5. 绝对不返回任何自然语言解释、注释或说明
    6. 禁止返回中文内容
    """

    # 调用大模型生成SQL
    response = llm.invoke([
        ("system", sql_prompt),
        ("user", query)
    ])

    # 妈妈揍我 妈妈打我
    # 大模型每一次给的回复是不同的，但是语义是相似的 rag(加载数据->数据切片->数据向量化->用向量数据库保存->数据检索->响应展示)
    # 提取并清理SQL（多重防护，避免自然语言混入）
    sql = response.content.strip()
    sql = sql.replace("```sql", "").replace("```", "").strip()

    """
    会出现的场景：
    \-\- 这是一段注释
    SELECT id,name FROM user WHERE age>18;

    随便一段文字 abc123 UPDATE student SET score=90
    """
    # 正则提取纯SQL（终极防护）
    # re.I（re.IGNORECASE）：忽略大小写，select / Select / SELECT 全都能匹配
    # re.DOTALL：让 . 可以匹配换行符，支持跨多行 SQL 语句
    sql_match = re.search(r"(SELECT|INSERT|UPDATE|DELETE).*", sql, re.I | re.DOTALL)
    if sql_match:
        sql = sql_match.group(0).strip()

    # 最终校验
    if not sql or not sql.upper().startswith(("SELECT", "INSERT", "UPDATE", "DELETE")):
        return "错误：无法生成合法的SQL语句，请重新描述您的问题。"

    print(f"[生成SQL] {sql}")
    return query_db(sql)


def beutiful_chat_fn(input):
    """
        将json数据转成非科班能看懂的文字信息
        :param input: get_weather工具响应的json数据
        :return:
        """
    # 指令
    instruction = """
        任务：查询指定城市的天气情况
        """
    # 示例（样本）
    example = """
        示例：{'location': '长沙', 'date': '2026-07-14 15:01:46', 'weather': '晴', 'temperature': '37°C', 'humidity': '55%'}
        输出：
            当前城市：湖南省长沙市
            当前时间：2026-07-14 15:01:46
            当前天气：晴天
            当前温度：37°C
            湿度：55%
            出行建议：不建议出行。如必要出行，注意防晒。
        """
    # 输出
    output = """
        按下面格式进行输出：
            当前城市：
            当前时间：
            当前天气：
            当前温度：
            湿度：
            出行建议：
        """

    prompt = f"{instruction}{example}{input}{output}"
    response = client.chat.completions.create(
        model="glm-5.2",
        messages=[
            {
                "role": "system",
                "content": "你是一个有用的AI助手。"
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
    )
    resp = response.choices[0].message.content
    print(resp)
    return resp


agent = create_agent(model=llm,
                     tools=[get_weather, send_email, execute_sql_query],
                     checkpointer=InMemorySaver())


def chat_fn(chat, chatbot):
    """
        在聊天框中生成聊天记录
        :param chat:
        :param chatbot:
        :return:
        """
    chatbot.append(
        {
            "role": "user",
            "content": chat
        }
    )
    # First invocation
    result = agent.invoke(
        {"messages": [HumanMessage(content=chat)]},
        config={"configurable": {"thread_id": "session-1"}}
    )
    # result = agent.invoke({"messages": [{"role": "user", "content": chat}]})
    result = result['messages'][-1].content
    print("result：", result)
    # 智谱AI大模型要给予回复
    try:
        chatbot.append(
            {
                "role": "assistant",
                "content": result
            }
        )
    except Exception as e:
        # print(e)
        chatbot.append(
            {
                "role": "assistant",
                "content": "抱歉，我无法回答您的问题。"
            }
        )

    return "", chatbot


"""搭建gradio网页应用"""
with gr.Blocks(title="智慧校园") as demo:
    with gr.Row():
        gr.Markdown("""
        # 欢迎来到智慧校园系统
        ## 功能如下：
        1.百度百科\n
        2.实时天气查询\n
        3.发送邮件\n
        4.数据库信息查询\n
        ## 案例演示
        1.城市学院怎么样？\n
        2.大理今天的天气怎么样？\n
        3.发送邮件：邮件的标题为湘南城院实习，内容为人工智能视觉方向开发项目是智能分拣项目，LLM方向开发是智慧校园智能体。\n
        4.案例1：查询所有的人工智能专业的学生信息。案例2：查询谢芳同学的所有成绩信息，并且将她的所有成绩发送给她的邮箱。\n
        """)
    with gr.Row():
        chatbot = gr.Chatbot(height=600)  # 聊天记录展示框
    with gr.Row():
        chat = gr.Textbox(label="指令", placeholder="请输入您的问题...")

    # 给指令框添加submit事件
    chat.submit(fn=chat_fn, inputs=[chat, chatbot], outputs=[chat, chatbot])

demo.launch()
