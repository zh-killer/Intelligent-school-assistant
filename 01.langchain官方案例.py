import os
from langchain.agents import create_agent
# 正确tool导入
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

# 智谱GLM4 兼容OpenAI接口配置
llm = ChatOpenAI(
    model="glm-4",
    api_key=os.getenv("zhipuai_api_key"),
    base_url=os.getenv("zhipuai_base_url"),
    temperature=0.1,
    timeout=30,
)

# 工具1：正确加@tool、返回str
@tool
def search(query: str) -> str:
    """Search for information online.
    Args:
        query: the keyword you want to search
    """
    return f"Search Results for [{query}]"

# 工具2：缺少@tool是最大bug，补上；返回类型修正为str
@tool
def get_weather(location: str) -> str:
    """Get real-time weather of target city.
    Args:
        location: city name to query weather
    """
    return f"{location} 今日天气晴朗，气温28℃"

# 创建Agent，开启verbose打印完整执行链路
agent = create_agent(
    model=llm,
    tools=[search, get_weather]
)

# 调用：messages格式保持不变
result = agent.invoke({
    "messages": [{"role": "user", "content": "长沙天气怎么样？"}]
})

# 打印最终回答
print("\n===== 最终输出 =====")
print(result["messages"][-1].content)