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

"""
1.应用langchain开发可以使用的工具(天气、百科、邮件)
2.修复原有bug，完善功能
3.兼容Gradio 6.0版本
4.修复启动和网络访问问题
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


# 工具1：天气查询
@tool
def get_weather(location: str, date: str = None) -> str:
    """获取指定城市的天气信息"""
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
    """搜索百度百科获取信息"""
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
    发送邮件工具，用于向指定邮箱发送邮件
    :param subject: 邮件的标题
    :param content: 邮件的内容
    :return: 发送结果
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

        return f"✅ 邮件发送成功！\n收件人：{receivers[0]}\n主题：{subject}\n内容：{content[:100]}..."
    except smtplib.SMTPAuthenticationError:
        return "❌ 邮件发送失败：邮箱认证失败，请检查邮箱账号和授权码是否正确。"
    except smtplib.SMTPException as e:
        return f"❌ 邮件发送失败：{str(e)}"
    except Exception as e:
        return f"❌ 邮件发送失败：{str(e)}"


# 创建Agent
agent = create_agent(
    model=llm,
    tools=[get_weather, search_baike, send_email_tool]
)


def process_user_input(user_input, chat_history):
    """
    处理用户输入并生成回复
    """
    if not user_input or not user_input.strip():
        return "", chat_history

    # 添加用户消息到历史
    chat_history.append({"role": "user", "content": user_input})

    try:
        # 调用agent处理
        result = agent.invoke({
            "messages": [{"role": "user", "content": user_input}]
        })

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
                chat_response = client.chat.completions.create(
                    model="glm-4",
                    messages=[
                        {"role": "system", "content": "你是一个智慧校园系统的AI助手，友好、专业地回答用户的问题。"},
                        {"role": "user", "content": user_input}
                    ],
                    temperature=0.7
                )
                response_content = chat_response.choices[0].message.content
            except:
                pass  # 保持原回复

        chat_history.append({"role": "assistant", "content": response_content})

    except Exception as e:
        error_msg = f"处理请求时出错：{str(e)}"
        chat_history.append({"role": "assistant", "content": error_msg})
        print(f"Error: {e}")

    return "", chat_history


def clear_history():
    """清空对话历史"""
    return []


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


# 创建Gradio界面 - 兼容Gradio 6.0版本
with gr.Blocks(title="智慧校园系统") as demo:
    gr.Markdown("""
    # 🏫 智慧校园系统
    ## 功能介绍
    - 🌤️ **实时天气查询**：输入城市名称即可获取天气信息
    - 📚 **百科知识查询**：输入任何名词即可获取百科介绍
    - 📧 **邮件发送**：输入"发送邮件：主题为XXX，内容为XXX"即可发送邮件
    - 💬 **智能对话**：支持日常问题解答

    ## 使用示例
    - "长沙今天的天气怎么样？"
    - "清华大学怎么样？"
    - "人工智能是什么？"
    - "帮我查一下大理的天气"
    - "发送邮件：主题为湘南学院实习通知，内容为人工智能视觉方向的智能分拣项目将于下周启动"
    - "帮我发一封邮件，主题为会议通知，内容为明天下午3点召开项目会议"
    """)

    with gr.Row():
        with gr.Column(scale=1):
            chatbot = gr.Chatbot(
                height=500,
                label="对话窗口"
            )

    with gr.Row():
        with gr.Column(scale=4):
            user_input = gr.Textbox(
                label="输入您的问题",
                placeholder="请输入您的问题，例如：长沙的天气怎么样？",
                lines=2
            )
        with gr.Column(scale=1):
            send_btn = gr.Button("发送", variant="primary", size="lg")
            clear_btn = gr.Button("清空对话", variant="secondary", size="lg")

    # 绑定事件
    user_input.submit(
        fn=process_user_input,
        inputs=[user_input, chatbot],
        outputs=[user_input, chatbot]
    )

    send_btn.click(
        fn=process_user_input,
        inputs=[user_input, chatbot],
        outputs=[user_input, chatbot]
    )

    clear_btn.click(
        fn=clear_history,
        outputs=[chatbot]
    )

    # 添加示例输入
    gr.Examples(
        examples=[
            ["长沙今天的天气怎么样？"],
            ["北京天气预报"],
            ["人工智能是什么？"],
            ["请介绍一下清华大学"],
            ["大理现在天气如何？"],
            ["什么是区块链？"],
            ["发送邮件：主题为湘南学院实习通知，内容为人工智能视觉方向的智能分拣项目将于下周启动"],
            ["帮我发一封邮件，主题为会议通知，内容为明天下午3点召开项目会议"]
        ],
        inputs=[user_input],
        label="示例问题"
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
    print("💡 如果无法访问，请尝试以下解决方案：")
    print("1. 关闭防火墙或允许端口7860")
    print("2. 使用本地地址: http://127.0.0.1:7860")
    print("3. 检查是否有其他程序占用7860端口")
    print("=" * 50 + "\n")

    try:
        demo.launch(
            server_name="0.0.0.0",  # 允许外部访问
            server_port=7860,
            share=False,  # 不创建公共链接
            debug=False,
            theme=gr.themes.Soft(),
            quiet=False  # 显示详细启动信息
        )
    except OSError as e:
        if "Address already in use" in str(e):
            print("\n❌ 端口7860已被占用，尝试使用其他端口...")
            demo.launch(
                server_name="127.0.0.1",
                server_port=7861,  # 使用不同端口
                share=False,
                debug=False,
                theme=gr.themes.Soft()
            )
        else:
            raise e