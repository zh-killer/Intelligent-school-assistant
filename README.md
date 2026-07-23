# 🏫 智慧校园 AI 智能体

> 基于 **LangChain + 智谱 GLM-4** 的全功能校园 AI 助手，Gradio 6 Web 界面 + MySQL 持久化。

## ✨ 核心功能

### 🤖 AI 对话
- **流式输出**：打字机效果实时渲染，消息发送后立即可见
- **上下文记忆**：LangGraph 检查点 + 多会话隔离
- **智能路由**：关键词快速通道 + Agent 工具调用双引擎

### 🛠 六大工具

| 优先级 | 工具 | 说明 |
|--------|------|------|
| 🥇 | **天气查询** | 高德地图 API 实时天气 + LLM 美化播报 + 出行建议 |
| 🥈 | **数据库操作** | MySQL 学生成绩/信息的增删改查、统计聚合 |
| 🥉 | **邮件发送** | QQ邮箱 SMTP 自动发送 |
| 4 | **百科查询** | Bing 搜索引擎抓取百科摘要 |
| 5 | **联网搜索** | 智谱 Web Search API（支持时效性过滤）+ Bing 降级 |
| 6 | **网页抓取** | URL 内容抓取 + LLM 自动总结 |

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
- MySQL 持久化，重启不丢失

### 👥 多用户系统
- 自定义登录/注册页面（密码 SHA256 哈希存储）
- 每个用户独立会话隔离
- 内置测试账号：admin / zhangsan / lisi

---

## 🚀 快速启动

### 1. 环境准备
```bash
# Python 3.10+
pip install gradio langchain langchain-openai zhipuai pymysql requests beautifulsoup4 opencv-python pillow

# 语音识别（可选）
pip install faster-whisper zhconv

# 目标检测（可选）
pip install ultralytics
```

### 2. 配置环境变量
```bash
# 复制配置模板
cp .env.example .env

# 编辑 .env 填入真实 API Key
# 至少需要填写：zhipuai_api_key, AMAP_API_KEY, MYSQL_PASSWORD
```

**Windows (CMD):**
```cmd
set zhipuai_api_key=你的智谱API密钥
set zhipuai_base_url=https://open.bigmodel.cn/api/paas/v4
set AMAP_API_KEY=你的高德地图Key
set MYSQL_PASSWORD=你的MySQL密码
```

**Windows (PowerShell):**
```powershell
$env:zhipuai_api_key="你的智谱API密钥"
$env:zhipuai_base_url="https://open.bigmodel.cn/api/paas/v4"
$env:AMAP_API_KEY="你的高德地图Key"
$env:MYSQL_PASSWORD="你的MySQL密码"
```

### 3. 准备数据库
```sql
CREATE DATABASE IF NOT EXISTS db_demo DEFAULT CHARSET utf8mb4;
```
（表结构由应用自动创建，无需手动建表）

### 4. 启动
```bash
python "07、06版加上数据库.py"
```
浏览器访问 `http://localhost:7860`

---

## 🏗️ 技术架构

```
┌────────────────────────────────────────┐
│           Gradio 6 Web UI              │
│  登录页 → 侧边栏 + 聊天窗 + 输入区      │
└────────────────┬───────────────────────┘
                 │
┌────────────────▼───────────────────────┐
│         LangChain Agent 层             │
│  关键词快速通道 → 工具调用 → LLM 回复   │
└────────────────┬───────────────────────┘
                 │
┌────────────────▼───────────────────────┐
│            能力层                       │
│  智谱GLM-4 │ 高德天气 │ MySQL │ Whisper │
│  GLM-4V   │ YOLO    │ SMTP  │ Win TTS │
└────────────────────────────────────────┘
```

---

## 📁 文件说明

| 文件 | 说明 |
|------|------|
| `07、06版加上数据库.py` | 主程序（全部功能集成在这一个文件） |
| `.env.example` | 环境变量配置模板 |
| `01-09` 系列文件 | 开发过程中的迭代版本 |

---

## 📄 License

MIT — 仅供学习交流使用。
