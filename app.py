#!/usr/bin/env python3
# ====================== 智慧校园 AI 智能体 — 主入口 ======================
"""
基于 LangChain + 智谱 GLM-4 的全功能校园 AI 助手
Gradio 6 Web 界面 + MySQL 持久化 + 多用户系统

模块架构：
  config.py     - 环境变量 & 数据库配置
  utils.py      - 工具函数（密码哈希、用户状态解析等）
  security.py   - 安全层（SQL校验、SSRF拦截、频率限制）
  database.py   - 数据库层（连接池 + 建表 + CRUD）
  llm_config.py - LLM 配置（ChatOpenAI、Agent 创建）
  tools.py      - 6 个 LangChain 工具
  services.py   - 视觉识别 + 语音交互
  chat.py       - 聊天引擎 + 会话管理
  auth.py       - 登录/注册/密码修改/管理员
  app.py        - 本文件：Gradio UI 组装 + 启动
"""
import sys
import gradio as gr
from html import escape as html_escape

import config
import utils
import database
import llm_config
import services
import chat
import auth


# ====================== DeepSeek风格侧边栏 CSS ======================
SIDEBAR_CSS = """
/* ---- 侧边栏会话列表(仿DeepSeek) ---- */
.ds-group { font-size: 12px; opacity: .55; padding: 8px 6px 2px; user-select: none; }
.ds-item { gap: 2px !important; border-radius: 10px; padding: 1px 4px; align-items: center; }
.ds-item:hover { background: rgba(0, 0, 0, .06); }
.dark .ds-item:hover { background: rgba(255, 255, 255, .08); }
.ds-item.active { background: rgba(77, 107, 254, .12); }
.ds-title-btn, .ds-title-btn:hover {
    background: none !important; border: none !important; box-shadow: none !important;
    justify-content: flex-start !important; text-align: left !important;
    font-size: 14px !important; font-weight: 400 !important;
    padding: 7px 6px !important; min-width: 0 !important;
    white-space: nowrap !important; overflow: hidden !important; text-overflow: ellipsis !important;
}
.ds-item.active .ds-title-btn { color: #4d6bfe !important; font-weight: 600 !important; }
.ds-op-btn {
    display: none !important; background: none !important; border: none !important;
    box-shadow: none !important; min-width: 28px !important; max-width: 28px !important;
    padding: 5px 2px !important; font-size: 13px !important;
}
.ds-item:hover .ds-op-btn { display: flex !important; }
.ds-op-btn:hover { background: rgba(0, 0, 0, .1) !important; border-radius: 6px !important; }
.dark .ds-op-btn:hover { background: rgba(255, 255, 255, .15) !important; }
.ds-del-confirm { min-width: 72px !important; font-size: 12px !important; padding: 5px 4px !important; }

/* ---- 登录页面样式 ---- */
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
#login-col .gr-textbox input {
    border-radius: 10px !important; padding: 12px 16px !important;
    font-size: 15px !important; border: 1.5px solid #e0e0e0 !important;
    transition: border-color .2s !important;
}
#login-col .gr-textbox input:focus {
    border-color: #4d6bfe !important; box-shadow: 0 0 0 3px rgba(77,107,254,.1) !important;
}
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


# ====================== Gradio 界面 ======================
with gr.Blocks(title="智慧校园系统") as demo:
    # ====================== 全局状态 ======================
    session_state = gr.State("default")
    pending_state = gr.State(None)
    selected_msg = gr.State(None)
    user_state = gr.State(None)
    delete_pending = gr.State(None)

    # ====================== 登录页面 ======================
    with gr.Column(visible=True, elem_id="login-col") as login_col:
        gr.HTML("""<div style="text-align:center">
            <div id="login-avatar">🏫</div>
            <div id="login-title">智慧校园系统</div>
            <div id="login-subtitle">AI 智能校园助手 · 请登录</div>
        </div>""")
        with gr.Row():
            with gr.Column(scale=1):
                pass
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
                pass

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

            # ---- 修改密码 ----
            with gr.Accordion("🔒 修改密码", open=False):
                pwd_old = gr.Textbox(label="原密码", type="password", placeholder="输入原密码")
                pwd_new = gr.Textbox(label="新密码", type="password", placeholder="至少4个字符")
                pwd_btn = gr.Button("✅ 确认修改", variant="secondary", size="sm")
                pwd_msg = gr.Markdown("", visible=True)

            # ---- 管理员面板 ----
            with gr.Accordion("🛡️ 管理员面板", open=False, visible=True) as admin_panel:
                gr.Markdown("仅管理员可操作")
                admin_user_input = gr.Textbox(label="目标用户名", placeholder="输入要操作的用户名")
                with gr.Row():
                    admin_list_btn = gr.Button("📋 查看用户列表", size="sm")
                    admin_delete_btn = gr.Button("🗑️ 删除用户", size="sm", variant="stop")
                    admin_promote_btn = gr.Button("⬆️ 提升为管理员", size="sm")
                admin_result = gr.Markdown("")

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

        # ====================== 事件绑定 ======================

        # 页面加载
        demo.load(
            fn=chat.init_ui,
            outputs=[session_dropdown, chatbot, session_state, user_state]
        )

        # 新会话
        new_session_btn.click(
            fn=chat.on_new_session,
            inputs=[user_state],
            outputs=[session_dropdown, chatbot, session_state, delete_pending, delete_btn]
        )

        # 下拉框选择会话
        session_dropdown.change(
            fn=chat.on_dropdown_select,
            inputs=[session_dropdown],
            outputs=[chatbot, session_state, delete_pending, delete_btn]
        )

        # 重命名
        rename_btn.click(fn=chat.start_rename, outputs=[rename_box, delete_pending, delete_btn])
        rename_box.submit(
            fn=chat.do_rename_session,
            inputs=[rename_box, session_state, user_state],
            outputs=[rename_box, session_dropdown]
        )

        # 删除会话
        delete_btn.click(
            fn=chat.on_delete_click,
            inputs=[session_state, user_state, delete_pending],
            outputs=[chatbot, session_state, session_dropdown, delete_btn, delete_pending]
        )

        # 发送消息（两步事件链）
        user_input.submit(
            fn=chat.add_user_message,
            inputs=[user_input, image_upload, camera_img, chatbot],
            outputs=[user_input, image_upload, camera_img, chatbot, pending_state]
        ).then(
            fn=chat.bot_respond,
            inputs=[pending_state, chatbot, session_state, user_state],
            outputs=[chatbot, session_state, session_dropdown, pending_state]
        ).then(
            fn=services.tts_reply,
            inputs=[tts_enable, chatbot],
            outputs=[tts_player]
        )

        send_btn.click(
            fn=chat.add_user_message,
            inputs=[user_input, image_upload, camera_img, chatbot],
            outputs=[user_input, image_upload, camera_img, chatbot, pending_state]
        ).then(
            fn=chat.bot_respond,
            inputs=[pending_state, chatbot, session_state, user_state],
            outputs=[chatbot, session_state, session_dropdown, pending_state]
        ).then(
            fn=services.tts_reply,
            inputs=[tts_enable, chatbot],
            outputs=[tts_player]
        )

        # 语音提问
        mic_audio.stop_recording(
            fn=services.on_voice_input,
            inputs=[mic_audio],
            outputs=[user_input, mic_audio]
        )

        # 对话记录删除
        chatbot.select(fn=chat.on_msg_select, outputs=[selected_msg])
        delete_msg_btn.click(
            fn=chat.delete_selected_round,
            inputs=[selected_msg, chatbot, session_state],
            outputs=[chatbot, selected_msg]
        )

        # 拍照
        capture_btn.click(fn=services.take_photo, outputs=[camera_img])

        # 清空聊天
        clear_btn.click(
            fn=chat.clear_history,
            inputs=[session_state],
            outputs=[chatbot, session_state]
        )

        # 退出登录
        logout_btn.click(
            fn=auth.do_logout,
            inputs=[user_state],
            outputs=[login_col, main_col, user_state, session_dropdown, chatbot, session_state,
                     login_username, login_password, login_error]
        )

        # 修改密码
        pwd_btn.click(
            fn=auth.change_password,
            inputs=[user_state, pwd_old, pwd_new],
            outputs=[pwd_msg]
        )

        # 管理员操作
        admin_list_btn.click(
            fn=auth.admin_list_users,
            inputs=[user_state],
            outputs=[admin_result]
        )
        admin_delete_btn.click(
            fn=auth.admin_delete_user,
            inputs=[user_state, admin_user_input],
            outputs=[admin_result]
        )
        admin_promote_btn.click(
            fn=auth.admin_promote_user,
            inputs=[user_state, admin_user_input],
            outputs=[admin_result]
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
    login_username.submit(
        fn=auth.do_login,
        inputs=[login_username, login_password],
        outputs=[login_col, main_col, user_state, session_dropdown, chatbot, session_state,
                 login_error]
    )
    login_password.submit(
        fn=auth.do_login,
        inputs=[login_username, login_password],
        outputs=[login_col, main_col, user_state, session_dropdown, chatbot, session_state,
                 login_error]
    )
    login_btn.click(
        fn=auth.do_login,
        inputs=[login_username, login_password],
        outputs=[login_col, main_col, user_state, session_dropdown, chatbot, session_state,
                 login_error]
    )
    register_btn.click(
        fn=auth.do_register,
        inputs=[login_username, login_password],
        outputs=[login_col, main_col, user_state, session_dropdown, chatbot, session_state,
                 login_error]
    )


# ====================== 启动入口 ======================
if __name__ == "__main__":
    print("\n" + "=" * 55)
    print("  🏫 智慧校园系统 — 启动检查")
    print("=" * 55)

    errors, warnings = utils.check_config()

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
        print(f"   配置文件路径: {config.BASE_DIR}/.env")
        sys.exit(1)

    if warnings:
        print("\n  ⚠️  部分可选功能不可用（不影响核心对话）：")
        for w in warnings:
            print(f"     {w}")
        print()

    local_ip = utils.get_local_ip()

    # 初始化数据库表 + 默认用户
    database.init_chat_tables()
    database.init_users_table()
    database.seed_default_users()

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
        if "Address already in use" in str(e) or "Cannot find empty port" in str(e):
            for port in range(7861, 7881):
                try:
                    print(f"\n⚠️ 端口已被占用，尝试使用 {port} 端口...")
                    demo.launch(
                        server_name="127.0.0.1",
                        server_port=port,
                        share=False,
                        debug=False,
                        theme=gr.themes.Soft(),
                        css=SIDEBAR_CSS
                    )
                    break
                except OSError:
                    continue
            else:
                raise RuntimeError("无法找到可用端口（已尝试 7860-7880），请手动释放端口后重试。")
        else:
            raise e
