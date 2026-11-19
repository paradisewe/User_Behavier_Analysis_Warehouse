#!/usr/bin/env python3
"""图节点：快路径直接调用 ask_sys.common，不复制其实现。

只有 agent ⇄ tools 是环；其余分支都是单节点，避免与 pipeline 漂移。
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from ask_sys.common import knowledge, nl2sql, routing
from ask_sys.common.env import require
from ask_sys.ver_pipeline.router import FALLBACK_TEXT

from .state import (MAX_SECONDS, MAX_TOKENS, MAX_TOOL_CALLS_PER_ROUND,
                    MAX_TOOL_ROUNDS)
from .tools import TOOLS, TOOLS_BY_NAME

# 每个节点都往 trace 里追加一条，前端/SSE 靠它展示真实进度
def _trace(node, t0, **extra):
    item = {"node": node, "ms": round((time.perf_counter() - t0) * 1000)}
    item.update({k: v for k, v in extra.items() if v is not None})
    return item


# ---------- 快路径 ----------
def classify(state):
    """LLM 四分类 + 规则兜底；usage 进 tokens 累计。"""
    t0 = time.perf_counter()
    intent, why, usage = routing.classify(state["question"])
    return {"intent": intent, "branch": intent, "intent_reason": why,
            "tokens": usage, "trace": [_trace("classify", t0)]}


def sql_node(state):
    """查数分支：nl2sql.ask（召回→guard→dw_ro→30s 角色超时）是唯一取数链路。"""
    t0 = time.perf_counter()
    met = {}
    try:
        sql, reason, text = nl2sql.ask(state["question"], compact=True, header=True,
                                       metrics=met)
        lines = text.splitlines() if text else []
        return {
            "sql": sql, "reason": reason,
            "columns": lines[0].split("|") if lines else [],
            "rows": [ln.split("|") for ln in lines[1:]],
            "row_count": max(0, len(lines) - 1),
            "tokens": met,
            "trace": [_trace("nl2sql", t0)],
        }
    except nl2sql.Refusal as e:
        # 与 pipeline 一致：保留原分支，标 refusal，由 finalize 出答案
        return {"answer": str(e), "refusal": True,
                "intent_reason": (state.get("intent_reason") or "") + "｜执行层拒答",
                "tokens": met, "trace": [_trace("nl2sql", t0, error="拒答")]}
    except Exception as e:
        return {"branch": "error", "error": str(e),
                "tokens": met, "trace": [_trace("nl2sql", t0, error=str(e))]}


def knowledge_node(state):
    """知识分支：检索 + 生成；知识库无命中时 knowledge.answer 自带话术。"""
    t0 = time.perf_counter()
    try:
        kb = knowledge.answer(state["question"])
        return {"answer": kb["answer"], "citations": kb["citations"],
                "tokens": kb.get("usage") or {},
                "trace": [_trace("knowledge", t0)]}
    except Exception as e:
        return {"branch": "error", "error": str(e),
                "trace": [_trace("knowledge", t0, error=str(e))]}


def fallback(state):
    """兜底分支：固定话术，不调模型。"""
    t0 = time.perf_counter()
    return {"answer": FALLBACK_TEXT, "trace": [_trace("fallback", t0)]}


# ---------- 组合分析：真正的工具循环 ----------
SYSTEM_PROMPT = """你是淘宝用户行为数仓的分析助手，可以调用三个工具：
1. get_schema(table, metric)：查表结构与指标口径；
2. search_knowledge(query)：查项目文档、口径与方法说明；
3. query_data(question)：把一个取数子问题交给 SQL 链路执行（只读）。

规则：
1. 事实必须来自工具返回，禁止编造数字、表名、字段或口径；
2. 取数问题直接调用 query_data，不要先查 get_schema 或 search_knowledge；
   只有不确定表名/口径、或工具报错需要换路时才查；
3. 比较/归因类问题：第一轮就把需要的子问题一起提交（最多 2 个 query_data 并行），
   每个子问题写清日期与指标，例如「2017-12-01 的 DAU 是多少」；
4. 没取到数之前不要下结论；
5. 最多 4 轮工具调用；预算内做不完就明确说明缺什么，不要硬凑；
6. 工具报错时换问法或换表，不要重复同一个失败调用；
7. 已有数据足够回答时立即给出最终文本，不要再调工具；
8. 最终回答用中文、纯文本，最多 350 字：先给结论，再列数字依据；
9. 引用数字用原始整数，不加千分位、不加单位（写 970401，不写 970,401）；
10. 比较两个值时写清基准与方向（谁更高/更低、差多少），不要把增加说成减少。
"""

_LLM = None


def _llm():
    """ChatOpenAI 绑定工具；模块内缓存，避免每轮重建客户端。"""
    global _LLM
    if _LLM is None:
        from langchain_openai import ChatOpenAI
        _LLM = ChatOpenAI(
            model=require("LLM_MODEL"),
            base_url=require("LLM_BASE_URL").rstrip("/"),
            api_key=require("LLM_API_KEY"),
            temperature=0,
            timeout=60,
            max_retries=0,
        ).bind_tools(TOOLS)
    return _LLM


def _usage_of(resp):
    """把 langchain 的 usage_metadata 归一到本项目的 tokens 形状。"""
    u = getattr(resp, "usage_metadata", None) or {}
    prompt = u.get("input_tokens") or 0
    completion = u.get("output_tokens") or 0
    if not prompt and not completion:
        tu = (getattr(resp, "response_metadata", None) or {}).get("token_usage") or {}
        prompt = tu.get("prompt_tokens") or 0
        completion = tu.get("completion_tokens") or 0
    return {"prompt_tokens": prompt, "completion_tokens": completion}


def _over_budget(state):
    """三重上限：轮次 / tokens / 墙钟，任一触发就收口。

    started_at 用 perf_counter 记（单调时钟，不受系统时间调整影响），这里必须同源。
    """
    if state.get("steps", 0) >= MAX_TOOL_ROUNDS:
        return True
    tokens = state.get("tokens") or {}
    if tokens.get("prompt_tokens", 0) + tokens.get("completion_tokens", 0) >= MAX_TOKENS:
        return True
    return time.perf_counter() - state.get("started_at", time.perf_counter()) >= MAX_SECONDS


def agent(state):
    """判断下一步：模型要么继续要工具，要么给出最终文本。"""
    t0 = time.perf_counter()
    if _over_budget(state):
        return {"trace": [_trace("agent", t0, error="预算用尽，直接收口")]}

    msgs = [SystemMessage(content=SYSTEM_PROMPT)] + list(state.get("messages") or [])
    if not any(isinstance(m, HumanMessage) for m in msgs):
        msgs.insert(1, HumanMessage(content=state["question"]))
    try:
        resp = _llm().invoke(msgs)
    except Exception as e:
        return {"trace": [_trace("agent", t0, error=str(e))]}

    calls = getattr(resp, "tool_calls", None) or []
    return {
        "messages": [resp],
        "tokens": _usage_of(resp),
        # 模型没有 tool_calls 时，content 就是答案；有就留空，等 finalize 降级
        "answer": None if calls else (resp.content or "").strip() or None,
        "trace": [_trace("agent", t0,
                         tool=",".join(c["name"] for c in calls) or None)],
    }


def _run_tool(call):
    """执行单个工具；未知工具/异常都转成 JSON 观察，绝不抛给图。"""
    name = (call or {}).get("name")
    args = (call or {}).get("args") or {}
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        return json.dumps({"error": f"未知工具：{name}"}, ensure_ascii=False)
    try:
        return tool.invoke(args)
    except Exception as e:
        return json.dumps({"error": f"工具执行失败：{e}"}, ensure_ascii=False)


def _evidence_of(call, raw):
    """一条可核对的证据：query_data 带 SQL/行，其余工具带观察摘要。"""
    name = (call or {}).get("name")
    args = (call or {}).get("args") or {}
    item = {"tool": name,
            "question": args.get("question") or args.get("query")
            or args.get("table") or args.get("metric") or "",
            "why": f"agent 调用 {name}", "sql": None, "columns": [],
            "rows": [], "row_count": 0, "error": None}
    try:
        obj = json.loads(raw)
    except Exception:
        obj = {"error": (raw or "")[:200]}
    if name == "query_data":
        for key in ("sql", "columns", "rows", "row_count", "error", "seconds"):
            item[key] = obj.get(key)
        item["question"] = obj.get("question") or item["question"]
    else:
        item["error"] = obj.get("error")
        item["text"] = (raw or "")[:500]
    return item


def tools(state):
    """执行最后一条 AIMessage 的 tool_calls：单轮并发 ≤2，超出的给明确错误。"""
    t0 = time.perf_counter()
    msgs = state.get("messages") or []
    last = msgs[-1] if msgs else None
    calls = list(getattr(last, "tool_calls", None) or [])
    if not calls:
        return {"trace": [_trace("tools", t0, error="没有 tool_calls")]}

    runnable, overflow = calls[:MAX_TOOL_CALLS_PER_ROUND], calls[MAX_TOOL_CALLS_PER_ROUND:]
    results = []
    if runnable:
        with ThreadPoolExecutor(max_workers=min(MAX_TOOL_CALLS_PER_ROUND, len(runnable))) as ex:
            futs = [ex.submit(_run_tool, c) for c in runnable]
            for c, f in zip(runnable, futs):
                try:
                    results.append((c, f.result(timeout=45)))
                except Exception as e:   # 单工具超时不影响本轮其它结果
                    results.append((c, json.dumps({"error": f"工具超时：{e}"},
                                                  ensure_ascii=False)))
    for c in overflow:
        results.append((c, json.dumps(
            {"error": f"单轮工具调用超过 {MAX_TOOL_CALLS_PER_ROUND} 个，未执行；请下一轮再试"},
            ensure_ascii=False)))

    out_msgs, evidence = [], []
    for call, raw in results:
        out_msgs.append(ToolMessage(content=raw, tool_call_id=call.get("id") or "",
                                    name=call.get("name")))
        evidence.append(_evidence_of(call, raw))
    return {
        "messages": out_msgs,
        "evidence": evidence,
        "steps": state.get("steps", 0) + 1,
        "trace": [_trace("tools", t0,
                         tool=",".join(c.get("name") or "?" for c in calls))],
    }


# ---------- 收口 ----------
def _degrade(evidence):
    """模型没给答案时，用已取到的证据拼一段可核对的话，避免空手而归。"""
    ok = [e for e in evidence if not e.get("error") and e.get("rows")]
    if not ok:
        return ("预算内未能完成取数与结论：没有拿到可用数据。"
                "可以缩小问题范围（指定指标和日期）再问。")
    lines = ["预算内未能完成全部取数与结论，先给出已核实的部分："]
    for e in ok[:4]:
        head = " | ".join(str(c) for c in (e.get("columns") or []))
        rows = [" | ".join(str(v) for v in r) for r in (e.get("rows") or [])[:3]]
        lines.append(f"- {e.get('question')}\n  {head}\n  " + "\n  ".join(rows))
    return "\n".join(lines)


def finalize(state):
    """组装统一响应（plan.md 4.3），不调模型。"""
    t0 = time.perf_counter()
    trace = list(state.get("trace") or [])
    trace.append(_trace("finalize", t0))

    branch = state.get("branch") or state.get("intent") or "fallback"
    evidence = state.get("evidence") or []
    answer = state.get("answer")
    if branch == "agent" and not answer:
        answer = _degrade(evidence)

    result = {
        "question": state["question"],
        "branch": branch,
        "intent_reason": state.get("intent_reason") or "",
        "answer": answer,
        "sql": state.get("sql"),
        "reason": state.get("reason"),
        "columns": state.get("columns") or [],
        "rows": state.get("rows") or [],
        "row_count": state.get("row_count") or 0,
        "citations": state.get("citations") or [],
        # agent 的 steps 就是逐次取数/检索的证据；快路径没有工具循环，留空
        "steps": evidence if branch == "agent" else [],
        "refusal": bool(state.get("refusal")),
        "error": state.get("error"),
        "elapsed": round(time.perf_counter() - state.get("started_at", t0), 2),
        "tokens": state.get("tokens") or {"prompt_tokens": 0, "completion_tokens": 0},
        "trace": trace,
    }
    return {"result": result}


def route(state):
    """classify 之后按 intent 分流。"""
    return state.get("intent") or "fallback"


def should_continue(state):
    """agent 之后：要工具就去 tools，否则收口。"""
    msgs = state.get("messages") or []
    last = msgs[-1] if msgs else None
    if not getattr(last, "tool_calls", None):
        return "finalize"
    if _over_budget(state):
        return "finalize"
    return "tools"
