#!/usr/bin/env python3
"""LangGraph 编排：编译一张图，四分支同图。

拓扑：
    START → classify ─┬→ nl2sql   ──→ finalize → END
                      ├→ knowledge ─→ finalize
                      ├→ fallback  ─→ finalize
                      └→ agent ⇄ tools → finalize
"""
import time

from langgraph.graph import END, START, StateGraph

from .nodes import (agent, classify, fallback, finalize, knowledge_node, route,
                    should_continue, sql_node, tools)
from .state import RECURSION_LIMIT, AskState


def build_graph():
    g = StateGraph(AskState)
    g.add_node("classify", classify)
    g.add_node("nl2sql", sql_node)
    g.add_node("knowledge", knowledge_node)
    g.add_node("fallback", fallback)
    g.add_node("agent", agent)
    g.add_node("tools", tools)
    g.add_node("finalize", finalize)

    g.add_edge(START, "classify")
    g.add_conditional_edges("classify", route, {
        "sql": "nl2sql",
        "knowledge": "knowledge",
        "fallback": "fallback",
        "agent": "agent",
    })
    for name in ("nl2sql", "knowledge", "fallback"):
        g.add_edge(name, "finalize")
    # 唯一的环：agent 判断 → 调工具 → 回 agent 再判断
    g.add_conditional_edges("agent", should_continue,
                            {"tools": "tools", "finalize": "finalize"})
    g.add_edge("tools", "agent")
    g.add_edge("finalize", END)
    return g.compile()


# 编译一次复用：编译有开销，且每次请求重建没有意义
GRAPH = build_graph()


def _error_result(question, started_at, exc):
    """图本身异常时的兜底响应，保证调用方拿到的仍是 4.3 的结构。"""
    return {
        "question": question, "branch": "error", "intent_reason": "",
        "answer": None, "sql": None, "reason": None,
        "columns": [], "rows": [], "row_count": 0, "citations": [],
        "steps": [], "refusal": False, "error": str(exc),
        "elapsed": round(time.perf_counter() - started_at, 2),
        "tokens": {"prompt_tokens": 0, "completion_tokens": 0},
        "trace": [],
    }


def answer(question):
    """单题入口：返回统一响应字典（与 pipeline 同构）。"""
    started_at = time.perf_counter()
    try:
        state = GRAPH.invoke(
            {"question": question, "started_at": started_at},
            config={"recursion_limit": RECURSION_LIMIT},
        )
        return state["result"]
    except Exception as e:      # 节点内的异常已各自降级，这里只兜图级故障
        return _error_result(question, started_at, e)


def stream(question):
    """T3 用：暴露编译后的图，由 API 层映射 SSE 事件。"""
    return GRAPH, {"question": question, "started_at": time.perf_counter()}
