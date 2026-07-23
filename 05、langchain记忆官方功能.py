import os

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

"""
langchain针对于多个会话，有对应的管理机制的，并且同一个会话是具备记忆功能的
1.隔离性
2.记忆功能
"""
llm = ChatOpenAI(
    model="glm-4",
    api_key=os.getenv("zhipuai_api_key"),
    base_url=os.getenv("zhipuai_base_url"),
    temperature=0.1,
    timeout=30,
)

agent = create_agent(
    llm,
    tools=[],
    checkpointer=InMemorySaver()
)

# First invocation
response1=agent.invoke(
    {"messages": [HumanMessage(content="我是张三，我是一名大四学生")]},
    config={"configurable": {"thread_id": "session-1"}}

)
print(response1["messages"][-1].content)

# Second invocation: the first message is persisted (Sydney location), so the model returns GMT+10 time
response2=agent.invoke(
    {"messages": [HumanMessage(content="我是李四,我手一名医生")]},
    config={"configurable": {"thread_id": "session-1"}}
)
print(response2["messages"][-1].content)