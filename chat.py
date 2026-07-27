# ====================== 聊天引擎 + 会话管理 ======================
import json
import datetime
import re
import gradio as gr
from PIL import Image
from langchain_core.messages import HumanMessage
import database
import utils
import services
import llm_config


# ====================== 处理用户输入（两步事件链） ======================
def add_user_message(user_input, image, camera_image, chat_history):
    """
    第一步（秒回显）：把用户消息立刻显示到聊天窗口并清空输入框，
    耗时的识图和Agent调用放到第二步 bot_respond 里执行
    """
    image = camera_image if camera_image is not None else image

    if not user_input or not user_input.strip():
        if image is None:
            return "", None, None, chat_history, None
        user_input = "请分析这张图片"

    img_info = utils.get_img_info_from_pil(image) if image is not None else None
    if img_info:
        content = [{"type": "text", "text": user_input},
                   {"type": "image", "path": img_info["full_path"]}]
    else:
        content = user_input
    chat_history.append({"role": "user", "content": content})

    pending = {"text": user_input, "img_path": img_info["full_path"] if img_info else None}
    return "", None, None, chat_history, pending


def bot_respond(pending, chat_history, session_id="default", user_state=None):
    """
    第二步（耗时，生成器）：识图、路由、流式调用Agent生成回复并持久化。
    每收到一段文字就 yield 一次，实现打字机效果。
    """
    if not pending:
        yield chat_history, session_id, gr.skip(), None
        return

    username = utils._uname(user_state) or "public"
    user_input = pending["text"]
    img_path = pending.get("img_path")

    database.db_ensure_session(session_id, username=username)
    is_first_exchange = not database.db_load_messages(session_id)

    # 持久化用户消息
    if img_path:
        database.db_save_message(session_id, "user",
                                 json.dumps(chat_history[-1]["content"], ensure_ascii=False))
    else:
        database.db_save_message(session_id, "user", user_input)

    # 图片预处理：GLM-4V识别 + YOLO目标检测
    image_desc, yolo_result = "", ""
    if img_path:
        image_desc = services.pre_recognize_image(Image.open(img_path))
        yolo_result, yolo_boxed = services.get_yolo_info(img_path)
        print(f"[图像识别] GLM-4V: {image_desc[:60]}... | YOLO: {yolo_result[:80]}")
        if yolo_boxed:
            boxed_content = [{"type": "image", "path": yolo_boxed}]
            chat_history.append({"role": "assistant", "content": boxed_content})
            database.db_save_message(session_id, "assistant",
                                     json.dumps(boxed_content, ensure_ascii=False))
            yield chat_history, gr.skip(), gr.skip(), gr.skip()

    chat_history.append({"role": "assistant", "content": ""})
    response_content = ""

    try:
        # 关键词路由
        weather_keywords = ["天气", "气温", "温度", "湿度", "下雨", "降雨", "降雪", "预报", "台风"]
        db_keywords = ["学生", "成绩", "查询", "删除", "修改", "添加", "数据库", "表", "记录"]
        email_keywords = ["发送邮件", "发邮件", "邮件"]
        url_keywords = ["总结", "阅读", "看看", "打开", "这个网页", "这个链接"]
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
            agent_prompt = user_input

        agent = llm_config.create_session_agent()

        if agent_prompt is not None:
            config_dict = {"configurable": {"thread_id": session_id}}
            try:
                for chunk, meta in agent.stream(
                        {"messages": [HumanMessage(content=agent_prompt)]},
                        config=config_dict, stream_mode="messages"):
                    if type(chunk).__name__ != "AIMessageChunk":
                        continue
                    text = chunk.content
                    if isinstance(text, list):
                        text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
                    if not text:
                        continue
                    response_content += text
                    chat_history[-1]["content"] = response_content
                    yield chat_history, gr.skip(), gr.skip(), gr.skip()
            except Exception as e:
                print(f"[流式] 失败，回退为整段输出: {e}")
            if not response_content:
                result = agent.invoke(
                    {"messages": [HumanMessage(content=agent_prompt)]}, config=config_dict)
                response_content = result["messages"][-1].content
        else:
            # 快速通道：直接web_search + 流式总结
            clean_query = re.sub(
                r'^(帮我|请(你)?|麻烦)?(搜一下|搜索一下|搜索|查一下|查一查|查查|查|上网搜一下|上网查一下|联网搜索|联网搜一下)',
                '', user_input).strip()
            if not clean_query:
                clean_query = user_input
            recency_words = ["今天", "今日", "昨天", "最新", "最近", "近日", "近期",
                             "刚刚", "实时", "这两天", "这几天", "本周", "这周", "这个月",
                             "现在", "结果", "战况", "今年", "2025", "2026"]
            search_query = clean_query
            if not any(w in search_query for w in recency_words):
                search_query = f"最新 {clean_query}"
            print(f"[快速通道] 联网搜索: {user_input} → 搜索词: {search_query}")
            from tools import web_search
            search_result = web_search.invoke({"query": search_query})
            stream_resp = llm_config.client.chat.completions.create(
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
        database.db_save_message(session_id, "assistant", response_content)

    except Exception as e:
        error_msg = f"处理请求时出错：{str(e)}"
        chat_history[-1]["content"] = error_msg
        database.db_save_message(session_id, "assistant", error_msg)
        print(f"Error: {e}")

    if is_first_exchange:
        title = generate_session_title(user_input, chat_history[-1]["content"])
        database.db_rename_session(session_id, title)
        print(f"🏷️ 会话标题: {title}")

    dropdown_update = refresh_session_dropdown(user_state, session_id)
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
    start = selected_idx
    if chat_history[start]["role"] == "assistant" and start > 0 \
            and chat_history[start - 1]["role"] == "user":
        start -= 1
    end = start
    while end + 1 < len(chat_history) and chat_history[end + 1]["role"] == "assistant":
        end += 1
    ids = [r[0] for r in database.db_fetch(
        "SELECT id FROM chat_messages WHERE session_id=%s ORDER BY id", (session_id,))]
    for i in range(start, end + 1):
        if i < len(ids):
            database.db_exec("DELETE FROM chat_messages WHERE id=%s", (ids[i],))
    del chat_history[start:end + 1]
    print(f"🗑️ 已删除会话 {session_id} 的第{start + 1}~{end + 1}条消息")
    return chat_history, None


# ====================== 会话管理回调 ======================
def init_ui():
    """页面加载时初始化：建表，返回空白状态"""
    database.init_chat_tables()
    return gr.update(choices=[]), [], "default", None


def on_new_session(user_state):
    """开启新对话"""
    username = utils._uname(user_state) or "public"
    now = datetime.datetime.now()
    sid = f"s{now.strftime('%Y%m%d%H%M%S')}"
    database.db_ensure_session(sid, "新对话", username)
    print(f"➕ 新建会话: {sid} (用户: {username})")
    dropdown_update = refresh_session_dropdown(user_state, sid)
    return dropdown_update, [], sid, None, gr.Button(value="🗑️ 删除", variant="secondary")


def generate_session_title(user_msg, ai_reply):
    """用LLM总结第一轮对话，生成简短会话标题"""
    try:
        resp = llm_config.client.chat.completions.create(
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
    """清空当前会话的聊天记录"""
    database.db_exec("DELETE FROM chat_messages WHERE session_id=%s", (session_id,))
    return [], session_id


def on_dropdown_select(sid):
    """下拉框选择会话 → 加载历史记录"""
    if not sid:
        return gr.skip(), gr.skip(), gr.skip(), gr.skip()
    history = database.db_load_messages(sid)
    print(f"🔄 切换到会话: {sid}, 历史记录数: {len(history)}")
    return history, sid, None, gr.Button(value="🗑️ 删除", variant="secondary")


def start_rename():
    """显示重命名输入框"""
    return gr.Textbox(visible=True), None, gr.Button(value="🗑️ 删除", variant="secondary")


def do_rename_session(new_name, sid, user_state):
    """执行重命名并刷新下拉列表"""
    if not sid:
        return gr.Textbox(visible=False, value=""), gr.skip()
    new_name = (new_name or "").strip()[:50]
    if new_name:
        database.db_rename_session(sid, new_name)
        print(f"✏️ 会话 {sid} 重命名为: {new_name}")
    dropdown_update = refresh_session_dropdown(user_state, sid)
    return gr.Textbox(visible=False, value=""), dropdown_update


def on_delete_click(sid, user_state, pending):
    """删除当前选中的会话（二次确认）"""
    if not sid:
        return gr.skip(), gr.skip(), gr.skip(), gr.skip(), None
    username = utils._uname(user_state) or "public"
    if not pending:
        return (gr.skip(), gr.skip(), gr.skip(),
                gr.Button(value="⚠️ 确认删除", variant="stop"), sid)
    database.db_delete_session(sid)
    print(f"🗑️ 已删除会话: {sid}")
    default_sid = f"default-{username}"
    database.db_ensure_session(default_sid, "默认会话", username)
    dropdown_update = refresh_session_dropdown(user_state, default_sid)
    history = database.db_load_messages(default_sid)
    return (history, default_sid, dropdown_update,
            gr.Button(value="🗑️ 删除", variant="secondary"), None)


def refresh_session_dropdown(user_state=None, current_sid=None):
    """刷新会话下拉列表"""
    username = utils._uname(user_state) or "public"
    sessions = database.db_list_sessions(username)
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
    if current_sid and any(c[1] == current_sid for c in choices):
        value = current_sid
    else:
        value = choices[0][1] if choices else None
    return gr.update(choices=choices, value=value)
