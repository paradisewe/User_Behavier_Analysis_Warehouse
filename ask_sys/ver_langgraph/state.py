#!/usr/bin/env python3
"""图状态与预算常量。

节点只返回「局部字典」，由这里的 reducer 合并；不要把整份状态读出来再整体写回，
否则多个分支会互相覆盖。
"""
import operator
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

# agent 预算：轮次 / tokens / 墙钟。超出任一上限就收口，不再调模型
MAX_TOOL_ROUNDS = 4
MAX_TOKENS = 8000
MAX_SECONDS = 90
MAX_TOOL_CALLS_PER_ROUND = 2   # 同一轮多个工具并发执行的上限

# LangGraph 的递归上限：一圈 agent→tools 是 2 步，16 足够容纳 4 轮 + 收尾
RECURSION_LIMIT = 16


def sum_tokens(left, right):
    """tokens 是字典，默认 reducer 会整份覆盖，这里改成逐项累加。"""
    left = left or {}
    right = right or {}
    return {
        "prompt_tokens": left.get("prompt_tokens", 0) + right.get("prompt_tokens", 0),
        "completion_tokens": left.get("completion_tokens", 0) + right.get("completion_tokens", 0),
    }


class AskState(TypedDict, total=False):
    """一次问答的全部状态（单轮，不做 checkpointer）。"""

    messages: Annotated[list[AnyMessage], add_messages]
    question: str
    started_at: float

    intent: str
    intent_reason: str
    branch: str

    # sql 分支
    sql: str | None
    reason: str | None
    columns: list[str]
    rows: list[list[str]]
    row_count: int
    refusal: bool

    # knowledge / fallback / agent
    answer: str | None
    citations: list[dict]

    # agent 工具循环
    evidence: Annotated[list[dict], operator.add]
    steps: int            # 已执行的 tools 轮次（预算判断用）
    tokens: Annotated[dict, sum_tokens]

    error: str | None
    trace: Annotated[list[dict], operator.add]   # {"node","ms","tool"?,"error"?}
    result: dict          # finalize 产出的统一响应
