# 🏫 智慧校园 AI 智能体

[![tests](https://github.com/zh-killer/Intelligent-school-assistant/actions/workflows/tests.yml/badge.svg)](https://github.com/zh-killer/Intelligent-school-assistant/actions/workflows/tests.yml)

> 基于 **LangChain + 智谱 GLM-4** 的校园 AI 助手：Gradio 6 界面、MySQL 持久化、6 个工具、GLM-4V + YOLO 双引擎视觉、Whisper 语音交互。

📄 相关文档：[项目全景图](项目全景图.md)（按 STAR 写的项目介绍，可直接用于简历）· [从零到上线：服务器部署全流程](从零到上线-部署全流程.md)

---

## ✨ 功能

### 🤖 AI 对话
- **流式输出**：打字机效果，消息发出后立刻可见
- **上下文记忆**：LangGraph 检查点 + 多会话隔离
- **智能路由**：关键词快速通道 + Agent 工具调用双引擎（天气、成绩这类问题不必绕一圈大模型）

### 🛠 六大工具

| 优先级 | 工具 | 说明 |
|--------|------|------|
| 🥇 | **天气查询** | 高德地图 API 实时天气 + LLM 美化播报 + 出行建议 |
| 🥈 | **数据库操作** | 自然语言 → SQL，查询 / 维护学生信息与成绩（带白名单校验，见「安全设计」） |
| 🥉 | **邮件发送** | QQ 邮箱 SMTP 自动发送 |
| 4 | **百科查询** | Bing 搜索抓取百科摘要 |
| 5 | **联网搜索** | 智谱 Web Search API（支持时效性过滤）+ Bing 降级 |
| 6 | **网页抓取** | URL 正文抓取 + LLM 自动总结（带 SSRF 拦截） |

### 🖼️ 视觉识别（双引擎）
- **GLM-4V**：图像描述、名人识别、场景理解
- **YOLO**：目标检测 + 带框图片输出
- 支持**上传图片**或**摄像头拍照**

### 🎤 语音交互
- **语音输入**：faster-whisper 本地识别 → 繁简转换 → 填入输入框确认后发送
- **语音输出**：Windows TTS 朗读 AI 回复

### 💬 会话管理（仿 DeepSeek 风格）
- 时间分组（今天 / 昨天 / 更早）
- 首轮对话后 LLM 自动生成标题
- 悬停重命名 / 删除，二次确认防误删
- MySQL 持久化，重启不丢

### 👥 多用户系统
- 自定义登录 / 注册页，密码 PBKDF2 哈希存储
- 每个用户独立会话隔离
- 管理员可查看用户、删除用户、提升权限

---

## 🏗️ 技术架构

| 层 | 选型 |
|---|---|
| 界面 | Gradio 6（自定义 CSS，非默认主题） |
| Agent | LangChain 1.3 `create_agent` + LangGraph `InMemorySaver` 检查点 |
| 模型 | 智谱 GLM-4（文本）、GLM-4V（多模态）、智谱 Web Search |
| 数据 | MySQL 8 + PyMySQL（会话 / 消息 / 用户 / 学生 / 成绩） |
| 视觉 | Ultralytics YOLO（可选）、OpenCV、Pillow |
| 语音 | faster-whisper（可选）、Windows SAPI TTS |

```
Gradio UI (app.py)
   │  事件回调
   ├── auth.py        登录 / 注册 / 改密 / 管理员操作
   ├── chat.py        聊天引擎：路由 → Agent → 流式输出 → 落库
   │      ├── llm_config.py   GLM-4 客户端 + 系统提示 + 检查点
   │      └── tools.py        6 个 LangChain 工具
   │             └── security.py   SQL 白名单 / SSRF / 登录限速
   ├── services.py    视觉识别 + 语音
   └── database.py    MySQL 连接池、建表、CRUD
```

---

## 📁 目录结构

```
Intelligent-school-assistant/
├── app.py               # 唯一入口：Gradio 界面组装 + 启动
├── chat.py              # 聊天引擎、会话切换、流式输出
├── tools.py             # 6 个 LangChain 工具
├── llm_config.py        # GLM-4 客户端、系统提示、检查点
├── services.py          # 视觉识别（GLM-4V / YOLO）、语音（Whisper / TTS）
├── database.py          # MySQL 连接池、建表、会话/消息/用户/成绩 CRUD
├── security.py          # SQL 白名单校验、SSRF 拦截、登录限速
├── auth.py              # 登录 / 注册 / 改密 / 管理员回调
├── utils.py             # 密码哈希（PBKDF2）、配置自检、图片工具
├── config.py            # 环境变量加载与常量
├── requirements.txt
├── pytest.ini
├── tests/               # 单元测试：SQL 白名单 / SSRF / 密码哈希
├── 项目全景图.md
└── 从零到上线-部署全流程.md
```

> 早期版本是一个 1800 行的单文件 `智能校园助手ai.py`，已在本仓库中移除；需要看历史实现可以 `git log --oneline -- 智能校园助手ai.py`。

---

## 🚀 快速开始

**环境要求**：Python 3.10+（3.11 验证）、MySQL 8（登录与历史记录需要）

### 1. 安装依赖

```bash
pip install -r requirements.txt

# 可选：语音识别 / YOLO 目标检测
pip install faster-whisper zhconv
pip install ultralytics torch torchvision
```

### 2. 配置密钥

```bash
cp .env.example .env      # Windows: copy .env.example .env
```

至少填写 `zhipuai_api_key`（[申请地址](https://open.bigmodel.cn/)），其余按需：

| 变量 | 必填 | 说明 |
|---|---|---|
| `zhipuai_api_key` | ✅ | 智谱 API Key，AI 对话 / 联网搜索 / 图像识别都依赖它 |
| `zhipuai_base_url` | 否 | 默认 `https://open.bigmodel.cn/api/paas/v4` |
| `AMAP_API_KEY` | 推荐 | 高德 Web 服务 Key，天气查询需要 |
| `MYSQL_HOST` / `MYSQL_USER` / `MYSQL_PASSWORD` | 推荐 | 默认 `localhost` / `root` / 空 |
| `MAIL_USER` / `MAIL_PASS` | 否 | QQ 邮箱地址 + SMTP 授权码（不是 QQ 密码） |

> `.env` 会在启动时自动加载（python-dotenv），不用手动设系统环境变量。
> 缺 Key 不会让程序崩掉，启动日志会明确说哪个功能不可用。

### 3. 准备数据库

```sql
CREATE DATABASE IF NOT EXISTS db_demo DEFAULT CHARSET utf8mb4;
```

表结构（`users` / `chat_sessions` / `chat_messages` / `students` / `scores`）由应用启动时自动创建。

### 4. 启动

```bash
python app.py
```

浏览器打开 **http://127.0.0.1:7860**（端口被占用会自动尝试 7861–7880）。内置演示账号：

| 账号 | 密码 | 角色 |
|---|---|---|
| `admin` | `admin123` | 管理员 |
| `zhangsan` | `123456` | 普通用户 |
| `lisi` | `123456` | 普通用户 |

---

## 🔒 安全设计

模型输出不能直接进执行链路，所以关键位置都加了确定性校验：

- **SQL 白名单**：自然语言转 SQL 后，只允许操作 `students` / `scores` 两张表；`UPDATE` / `DELETE` 必须带 `WHERE`；禁止多语句和 DDL（`DROP` / `ALTER` / `TRUNCATE`…）。
- **SSRF 拦截**：网页抓取只接受 http/https，且拒绝回环、内网、链路本地地址。
- **密码哈希**：PBKDF2-HMAC-SHA256（20 万次迭代 + 每用户随机盐），旧的固定盐 SHA256 哈希在用户下次登录成功后自动升级。
- **登录限速**：同一账号 5 分钟内最多 10 次尝试；错误提示统一为「用户名或密码错误」，避免用户名枚举。

---

## 🧪 测试

```bash
python -m pytest        # 16 个用例，约 0.5 秒
```

覆盖 SQL 白名单的放行 / 拒绝边界、SSRF 网段判断、密码哈希的新旧格式兼容。

---

## ⚠️ 已知限制

- **单进程**：会话检查点存在进程内存里，重启后只保留 MySQL 里的聊天记录；也不支持多 worker 部署。
- **演示账号**：仓库自带 3 个演示账号，正式部署前请改密码或删掉。
- **YOLO**：未内置权重，首次调用会下载 `yolo11n.pt`；语音识别需要额外安装 faster-whisper。
- **NL2SQL 校验是规则兜底**，不是完整的 SQL 解析器：字符串字面量里出现关键字会被误判，宁可误杀。

---

## ❓ 常见问题

| 问题 | 解决方法 |
|---|---|
| 启动提示「未检测到 zhipuai_api_key」 | 复制 `.env.example` → `.env`，填入真实 Key |
| 天气查询不工作 | 去[高德开放平台](https://console.amap.com/)申请免费 Key，填入 `AMAP_API_KEY` |
| 登录 / 注册 / 历史记录不工作 | 检查 MySQL 是否启动、`MYSQL_PASSWORD` 是否正确 |
| 邮件发送失败 | QQ 邮箱需开启 SMTP 服务并获取**授权码**（不是 QQ 密码），填入 `MAIL_PASS` |
| 语音识别不可用 | `pip install faster-whisper zhconv` |
| 端口 7860 被占用 | 应用会自动尝试 7861–7880 |

---

## 📄 License

MIT
