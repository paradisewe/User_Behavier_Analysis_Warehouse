#!/usr/bin/env python3
"""T0 探针：验证 qwen 原生 tool_calls、ToolMessage 回填、最小 StateGraph。

三个探针任一失败即以非 0 退出——说明工具协议不可用，应停下来改方案，
不要在 graph 里先写一套绕过逻辑。

跑法：python -m ask_sys.dev.probe_graph
"""
import json
import sys
import time
from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from ask_sys.common.env import require

# 探针里的工具只做形状验证，真正的工具在 T2 的 tools.py 实现
@tool
def get_schema(table: str = "", metric: str = "") -> str:
    """查询数仓表结构与指标口径；table 为空时返回全部表名。"""
    return ("dw_ads_traffic_rfm_df：stat_date(日期)、dau(日活)、buy_users(购买人数)；"
            "指标 dau=当日去重访客数")


@tool
def query_data(question: str) -> str:
    """把自然语言问题转成 SELECT 并查询数仓（只读）。"""
    return json.dumps({"sql": "SELECT 1 AS ok", "columns": ["ok"],
                       "rows": [["1"]], "row_count": 1, "error": None},
                      ensure_ascii=False)


def make_llm(tools):
    """统一的模型入口：超时必须有，重试关掉以免探针拖长。"""
    return ChatOpenAI(
        model=require("LLM_MODEL"),
        base_url=require("LLM_BASE_URL").rstrip("/"),
        api_key=require("LLM_API_KEY"),
        temperature=0,
        timeout=60,
        max_retries=0,
    ).bind_tools(tools)


def probe_a():
    """A：模型能返回原生 tool_calls，且参数是合法 JSON。"""
    llm = make_llm([get_schema, query_data])
    msgs = [
        SystemMessage(content="你必须先调用工具获取信息，再回答；不要凭空编造字段。"),
        HumanMessage(content="dw_ads_traffic_rfm_df 这张表有哪些字段？"),
    ]
    t0 = time.time()
    resp = llm.invoke(msgs)
    calls = getattr(resp, "tool_calls", None) or []
    print(f"[A] {time.time() - t0:.1f}s tool_calls={len(calls)}")
    if not calls:
        print(f"[A] FAIL：没有 tool_calls，content={ (resp.content or '')[:200] }")
        return None
    for c in calls:
        print(f"[A] name={c['name']} args={json.dumps(c['args'], ensure_ascii=False)}")
        if not isinstance(c.get("args"), dict):
            print("[A] FAIL：tool_calls.args 不是对象")
            return None
    return resp


def probe_b(ai_msg):
    """B：工具结果以 ToolMessage 回填后，模型能给出最终文本。"""
    llm = make_llm([get_schema, query_data])
    call = ai_msg.tool_calls[0]
    observation = get_schema.invoke(call["args"])
    msgs = [
        SystemMessage(content="你必须先调用工具获取信息，再回答；不要凭空编造字段。"),
        HumanMessage(content="dw_ads_traffic_rfm_df 这张表有哪些字段？"),
        ai_msg,
        ToolMessage(content=observation, tool_call_id=call["id"]),
    ]
    t0 = time.time()
    resp = llm.invoke(msgs)
    text = (resp.content or "").strip()
    print(f"[B] {time.time() - t0:.1f}s content={text[:160]}")
    if not text:
        print("[B] FAIL：回填 ToolMessage 后没有最终文本")
        return False
    return True


class MiniState(TypedDict, total=False):
    text: str
    done: bool


def probe_c():
    """C：最小 StateGraph（两节点一条边）能编译并调用。"""
    from langgraph.graph import END, START, StateGraph

    def first(state: MiniState):
        return {"text": state.get("text", "") + "a"}

    def second(state: MiniState):
        return {"text": state.get("text", "") + "b", "done": True}

    g = StateGraph(MiniState)
    g.add_node("first", first)
    g.add_node("second", second)
    g.add_edge(START, "first")
    g.add_edge("first", "second")
    g.add_edge("second", END)
    out = g.compile().invoke({"text": ""})
    print(f"[C] out={out}")
    if out.get("text") != "ab" or not out.get("done"):
        print("[C] FAIL：StateGraph 结果不符合预期")
        return False
    return True


def main():
    ai_msg = probe_a()
    if ai_msg is None:
        print("探针 A 失败：需要改工具协议（结构化 action JSON），先更新 plan.md")
        return 1
    if not probe_b(ai_msg):
        print("探针 B 失败：ToolMessage 回填链路不通，停在 T0")
        return 1
    if not probe_c():
        print("探针 C 失败：LangGraph 基本不可用，停在 T0")
        return 1
    print("T0 全部探针通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
