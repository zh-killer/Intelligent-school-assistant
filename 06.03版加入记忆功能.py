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
1.应用langchain开发可以使用的工具(天气、百科、邮件)
2.修复原有bug，完善功能
3.兼容Gradio 6.0版本
4.修复启动和网络访问问题
5.添加会话记忆功能，支持多会话隔离
6.修复会话切换时记忆丢失的问题
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

# 创建全局检查点保存器（用于记忆功能）
checkpointer = InMemorySaver()

# 会话存储（管理不同会话的历史记录）
sessions = {}

# 系统提示
system_prompt = """你是一个智慧校园系统的AI助手，拥有以下工具：
1. get_weather - 查询天气
2. search_baike - 搜索百科
3. send_email_tool - 发送邮件

重要规则：
- 当用户说"发送邮件"、"发邮件"、"帮我发一封邮件"等涉及发送邮件的请求时，**必须**使用 send_email_tool 工具
- 从用户的消息中提取邮件的主题和内容作为工具参数
- 如果用户没有明确指定主题或内容，请询问用户补充
- 不要自己生成邮件内容，必须使用工具实际发送邮件
- 其他问题可以直接回答
- 记住对话历史，以便更好地理解用户的上下文
"""


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
        # 如果API调用失败，返回格式化的基本信息
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
            # 如果API失败，使用搜索接口
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


# 工具3：发送邮件
@tool
def send_email_tool(subject: str, content: str) -> str:
    """
    当用户明确要求发送邮件、发邮件、发送一封邮件等指令时，使用此工具发送邮件。
    例如："发送邮件：主题为XXX，内容为XXX" 或 "帮我发一封邮件，主题是XXX，内容是XXX"
    """
    try:
        # 第三方 SMTP 服务
        mail_host = "smtp.qq.com"  # 设置服务器
        mail_user = "3773353416@qq.com"  # 用户名
        mail_pass = "qfraslbimnbucgee"  # 口令

        sender = '3773353416@qq.com'
        receivers = ['2419819951@qq.com']  # 接收邮件

        # 创建邮件内容
        message = MIMEText(content, 'plain', 'utf-8')
        message['From'] = sender
        message['To'] = receivers[0]
        message['Subject'] = subject

        # 发送邮件
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


# 创建Agent（使用全局checkpointer，所有会话共享同一个）
def create_session_agent():
    """创建带有记忆功能的Agent"""
    return create_agent(
        model=llm,
        tools=[get_weather, search_baike, send_email_tool],
        system_prompt=system_prompt,
        checkpointer=checkpointer  # 使用全局checkpointer
    )


# 创建默认Agent
agent = create_session_agent()


def process_user_input(user_input, chat_history, session_id="default"):
    """
    处理用户输入并生成回复（支持多会话记忆）
    """
    if not user_input or not user_input.strip():
        return "", chat_history, session_id

    # 初始化会话历史
    if session_id not in sessions:
        sessions[session_id] = []

    # 添加用户消息到历史
    chat_history.append({"role": "user", "content": user_input})
    sessions[session_id].append({"role": "user", "content": user_input})

    try:
        # 调用agent处理（使用thread_id实现会话隔离和记忆）
        # 注意：这里使用同一个agent实例，通过thread_id来区分不同会话
        result = agent.invoke(
            {"messages": [HumanMessage(content=user_input)]},
            config={"configurable": {"thread_id": session_id}}
        )

        # 提取最终回答
        response_content = result["messages"][-1].content

        # 检查是否调用了工具
        has_tool_call = False
        if "intermediate_steps" in result:
            for step in result["intermediate_steps"]:
                if step[0].tool in ["get_weather", "search_baike", "send_email_tool"]:
                    has_tool_call = True
                    break

        # 如果没有调用工具，使用智谱API增强回复
        if not has_tool_call:
            try:
                # 检查是否包含邮件关键词
                if any(keyword in user_input for keyword in ["发送邮件", "发邮件", "邮件", "email", "Email"]):
                    # 如果用户提到了邮件但没有调用工具，强制让Agent重新处理
                    retry_prompt = f"用户要求发送邮件，请使用send_email_tool工具处理：{user_input}"
                    result = agent.invoke(
                        {"messages": [HumanMessage(content=retry_prompt)]},
                        config={"configurable": {"thread_id": session_id}}
                    )
                    response_content = result["messages"][-1].content
                else:
                    # 构建包含历史上下文的提示
                    context = "\n".join([f"{msg['role']}: {msg['content']}" for msg in sessions[session_id][-5:]])
                    chat_response = client.chat.completions.create(
                        model="glm-4",
                        messages=[
                            {"role": "system",
                             "content": "你是一个智慧校园系统的AI助手，友好、专业地回答用户的问题。请根据对话历史给出连贯的回答。"},
                            {"role": "user", "content": f"对话历史：\n{context}\n\n当前问题：{user_input}"}
                        ],
                        temperature=0.7
                    )
                    response_content = chat_response.choices[0].message.content
            except Exception as e:
                print(f"增强回复失败：{e}")
                pass  # 保持原回复

        chat_history.append({"role": "assistant", "content": response_content})
        sessions[session_id].append({"role": "assistant", "content": response_content})

    except Exception as e:
        error_msg = f"处理请求时出错：{str(e)}"
        chat_history.append({"role": "assistant", "content": error_msg})
        sessions[session_id].append({"role": "assistant", "content": error_msg})
        print(f"Error: {e}")

    return "", chat_history, session_id


def clear_history(session_id="default"):
    """清空对话历史"""
    if session_id in sessions:
        sessions[session_id] = []
    return [], session_id


def switch_session(session_id, chat_history):
    """切换会话 - 加载对应会话的历史记录"""
    if session_id not in sessions:
        sessions[session_id] = []

    # 从sessions中加载该会话的历史记录
    session_history = sessions[session_id]

    # 转换为Gradio格式
    formatted_history = []
    for msg in session_history:
        formatted_history.append({"role": msg["role"], "content": msg["content"]})

    # 打印调试信息
    print(f"切换到会话: {session_id}, 历史记录数: {len(session_history)}")
    if len(session_history) > 0:
        print(f"最后一条记录: {session_history[-1]}")

    return formatted_history, session_id


# 获取本机IP地址
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"


# 创建Gradio界面
with gr.Blocks(title="智慧校园系统", theme=gr.themes.Soft()) as demo:
    gr.Markdown("""
    # 🏫 智慧校园系统（带记忆功能）

    ## 📌 重要说明
    - **记忆功能**：系统会自动记住同一会话ID下的所有对话
    - **会话隔离**：不同会话ID之间的对话完全独立
    - **测试记忆**：可以先说"我叫张三"，再问"我叫什么？"来测试

    ## 功能介绍
    - 🌤️ **实时天气查询**：输入城市名称即可获取天气信息
    - 📚 **百科知识查询**：输入任何名词即可获取百科介绍
    - 📧 **邮件发送**：输入"发送邮件：主题为XXX，内容为XXX"即可实际发送邮件
    - 💬 **智能对话**：支持日常问题解答，**具有完整记忆功能**
    - 🔄 **多会话支持**：不同会话之间相互隔离，各自拥有独立的记忆

    ## 测试记忆功能
    1. 在默认会话中输入："我叫张三，是一名大四学生"
    2. 再输入："你还记得我叫什么吗？" → 应该回答"张三"
    3. 切换会话ID为"test"，再问同样的问题 → 不会记得
    4. 切换回"default"，再问 → 应该还记得"张三"
    """)

    with gr.Row():
        with gr.Column(scale=1):
            chatbot = gr.Chatbot(
                height=500,
                label="对话窗口"
            )

    with gr.Row():
        with gr.Column(scale=2):
            session_input = gr.Textbox(
                label="会话ID",
                value="default",
                placeholder="输入会话ID，不同会话之间记忆隔离",
                info="💡 不同会话ID具有独立的对话历史和记忆"
            )
        with gr.Column(scale=1):
            switch_btn = gr.Button("🔄 切换会话", variant="secondary", size="lg")
            clear_btn = gr.Button("🗑️ 清空当前会话", variant="secondary", size="lg")

    with gr.Row():
        with gr.Column(scale=4):
            user_input = gr.Textbox(
                label="输入您的问题",
                placeholder="请输入您的问题，例如：长沙的天气怎么样？",
                lines=2
            )
        with gr.Column(scale=1):
            send_btn = gr.Button("📤 发送", variant="primary", size="lg")

    # 状态存储
    session_state = gr.State("default")

    # 绑定事件
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

    # 添加示例输入
    gr.Examples(
        examples=[
            ["🌤️ 长沙今天的天气怎么样？"],
            ["📚 人工智能是什么？"],
            ["📧 发送邮件：主题为测试邮件，内容为这是一封测试邮件"],
            ["🧠 我叫张三，是一名大四学生"],
            ["🧠 你还记得我叫什么吗？"],
            ["🧠 我是学计算机的"],
            ["🧠 你还记得我的专业是什么吗？"]
        ],
        inputs=[user_input],
        label="📝 示例问题"
    )

if __name__ == "__main__":
    # 启动时检查环境变量
    if not os.getenv("zhipuai_api_key"):
        print("⚠️ 警告：未设置 zhipuai_api_key 环境变量")
        print("请设置：set zhipuai_api_key=your_api_key (Windows)")
        print("或：export zhipuai_api_key='your_api_key' (Linux/Mac)")

    local_ip = get_local_ip()

    print("\n" + "=" * 50)
    print("🚀 智慧校园系统启动中...")
    print("=" * 50)
    print(f"📡 本地访问地址: http://127.0.0.1:7860")
    print(f"📡 局域网访问地址: http://{local_ip}:7860")
    print("=" * 50)
    print("💡 功能特点：")
    print("1. ✅ 支持多会话隔离（不同会话ID具有独立记忆）")
    print("2. ✅ 每个会话都有完整的对话历史记忆")
    print("3. ✅ 支持天气查询、百科搜索、邮件发送")
    print("4. ✅ 切换会话时自动加载历史记录")
    print("=" * 50)
    print("💡 测试记忆功能：")
    print("1. 输入：'我叫张三'")
    print("2. 输入：'我叫什么？' → 应该回答'张三'")
    print("3. 切换会话ID，再问'我叫什么？' → 不会记得")
    print("4. 切换回原会话，再问'我叫什么？' → 还记得'张三'")
    print("=" * 50 + "\n")

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