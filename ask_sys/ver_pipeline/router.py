#!/usr/bin/env python3
"""pipeline 编排：四分支手写状态机（知识 / 查数 / 组合分析 / 兜底）。

对齐原 Dify Chatflow；共享能力都在 ask_sys.common，这里只负责编排与降级。
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor

from ask_sys.common import knowledge, nl2sql, routing
from ask_sys.common.db import q
from ask_sys.common.llm import call_llm, json_of

MAX_STEPS = 2

FALLBACK_TEXT = ("这个问题不在我的能力范围内。我可以帮你：\n"
                 "1. 查数：指标值、榜单、趋势（例如「12-02 的 DAU 是多少」）；\n"
                 "2. 讲口径与方法：例如「复购率的口径怎么定义」「对账是怎么做的」；\n"
                 "3. 多步分析：例如「为什么 12-02 的 DAU 比前一天高」。")


# ---------- 组合分析分支 ----------
PLAN_PROMPT = """你是数据分析师。把用户问题拆成 1~{max_steps} 个「可以用单条 SELECT 取数」的子问题，只输出 JSON：
{{"steps":[{{"question":"子问题（含具体指标与日期）","why":"这一步验证什么"}}]}}

可用指标：
{metrics}

可用表：
{tables}

规则：
1. 子问题必须是取数问题，不要问「为什么」，不要要求解释；
2. 每个子问题都要明确日期（口径窗口 2017-11-25 ~ 2017-12-03）；
3. 优先用 ADS 结果表；不要编造表名或指标；
4. 只输出 JSON。"""

SYNTH_PROMPT = """你是数据分析师。基于下面的取数结果回答用户问题，用中文，最多 350 字。

要求：
1. 先给结论（一句话）；
2. 再列数据依据，必须引用真实数字，不要编造；
3. 如果要做原因推断，明确写「推测」；
4. 数据不足就直说，不要用常理补数；
5. 比较两个值时先算差值并核对方向（增加/减少），写清基准是谁；
6. 纯文本（不要 markdown 表格），可以用 1. 2. 3. 分点。

【用户问题】{question}

【取数结果】
{evidence}"""


def _fallback_plan(question):
    """规划模型超时/不可用时的确定性计划：按日期 + 召回指标拼取数子问题。"""
    try:
        mhits, _ch, _t, _c = nl2sql.recall(question)
    except Exception:
        mhits = []
    dates = re.findall(r"\d{1,2}-\d{1,2}", question)
    if mhits:
        name = mhits[0]["name"]
        if len(dates) >= 2:
            qs = [f"{d} 的{name}是多少" for d in dates[:MAX_STEPS]]
        elif dates:
            qs = [f"最近几天每天的{name}", f"{dates[0]} 的{name}是多少"]
        else:
            qs = [f"最近几天每天的{name}"]
        return [{"question": x, "why": "规则兜底计划（规划模型超时）"} for x in qs[:MAX_STEPS]]
    return [{"question": question, "why": "规则兜底计划（未命中指标）"}]


def _plan(question):
    metrics = "\n".join(f"- {r[0]} | {r[1]}" for r in
                        q("SELECT metric_id, metric_name FROM meta.metric_info ORDER BY 1"))
    tables = "\n".join(f"- {r[0]} | {r[1]}" for r in
                       q("SELECT table_name, table_comment FROM meta.table_info ORDER BY 1"))
    try:
        reply, usage = call_llm(
            PLAN_PROMPT.format(max_steps=MAX_STEPS, metrics=metrics, tables=tables,
                               question=question) + f"\n\n用户问题：{question}",
            max_tokens=300, timeout=18)
    except Exception:  # 不让规划成为单点故障
        return _fallback_plan(question), {"prompt_tokens": 0, "completion_tokens": 0}

    obj = json_of(reply) or {}
    steps = obj.get("steps")
    clean = []
    if isinstance(steps, list):
        for s in steps:
            if isinstance(s, dict) and str(s.get("question", "")).strip():
                clean.append({"question": str(s["question"]).strip(),
                              "why": str(s.get("why", "")).strip()})
    if not clean:
        clean = [{"question": question, "why": "规划失败，退化为单步取数"}]
    return clean[:MAX_STEPS], usage


def _fmt_rows(columns, rows, limit=20):
    lines = [" | ".join(columns)]
    for r in rows[:limit]:
        lines.append(" | ".join(str(v)[:40] for v in r))
    if len(rows) > limit:
        lines.append(f"...（共 {len(rows)} 行，仅展示前 {limit} 行）")
    return "\n".join(lines)


def _run_step(st):
    item = {"question": st["question"], "why": st["why"], "sql": None,
            "columns": [], "rows": [], "row_count": 0, "error": None}
    met = {}
    try:
        sql, reason, text = nl2sql.ask(st["question"], compact=True, header=True,
                                       metrics=met)
        lines = text.splitlines() if text else []
        item.update({"sql": sql, "reason": reason,
                     "columns": lines[0].split("|") if lines else [],
                     "rows": [ln.split("|") for ln in lines[1:]],
                     "row_count": max(0, len(lines) - 1)})
    except nl2sql.Refusal as e:
        item["error"] = f"拒答：{e}"
    except Exception as e:
        item["error"] = str(e)
    item["seconds"] = met.get("seconds")
    return item, met


def _run_steps(steps):
    """并发执行子问题；单步 40s 未返回就记失败，不拖住整体。"""
    ex = ThreadPoolExecutor(max_workers=max(1, len(steps)))
    futs = [ex.submit(_run_step, s) for s in steps]
    out = []
    for s, f in zip(steps, futs):
        try:
            out.append(f.result(timeout=40))
        except Exception as e:
            out.append(({"question": s["question"], "why": s["why"], "sql": None,
                         "columns": [], "rows": [], "row_count": 0,
                         "error": f"单步超时/异常：{e}", "seconds": None}, {}))
    ex.shutdown(wait=False)
    return out


def agent_answer(question):
    steps, plan_usage = _plan(question)
    usage = dict(plan_usage)
    pairs = _run_steps(steps)
    done = [p[0] for p in pairs]
    for _, met in pairs:
        usage["prompt_tokens"] += met.get("prompt_tokens", 0)
        usage["completion_tokens"] += met.get("completion_tokens", 0)

    evidence = []
    for i, d in enumerate(done, 1):
        if d["error"]:
            evidence.append(f"{i}. {d['question']}\n（取数失败：{d['error']}）")
        else:
            evidence.append(f"{i}. {d['question']}\n{_fmt_rows(d['columns'], d['rows'])}")
    try:
        reply, u = call_llm(
            SYNTH_PROMPT.format(question=question, evidence="\n\n".join(evidence)),
            max_tokens=700, timeout=45)
        text = reply.strip()
        usage["prompt_tokens"] += u.get("prompt_tokens", 0)
        usage["completion_tokens"] += u.get("completion_tokens", 0)
    except Exception:  # 综合超时，保留可核对的取数结果
        text = "取数已完成，但结论生成超时。请展开下方各步查看数据依据。"
    return {"answer": text, "steps": done, "usage": usage}


# ---------- 统一入口 ----------
def answer_question(question):
    t0 = time.perf_counter()
    intent, why, usage = routing.classify(question)
    result = {"question": question, "branch": intent, "intent_reason": why}
    try:
        if intent == "sql":
            sql, reason, text = nl2sql.ask(question, compact=True, header=True)
            lines = text.splitlines() if text else []
            result.update({
                "sql": sql, "reason": reason,
                "columns": lines[0].split("|") if lines else [],
                "rows": [ln.split("|") for ln in lines[1:]],
                "row_count": max(0, len(lines) - 1),
                "seconds": nl2sql.LAST.get("seconds"),
                "rounds": nl2sql.LAST.get("rounds"),
            })
            usage["prompt_tokens"] += nl2sql.LAST.get("prompt_tokens", 0)
            usage["completion_tokens"] += nl2sql.LAST.get("completion_tokens", 0)
        elif intent == "knowledge":
            kb = knowledge.answer(question)
            result.update({"answer": kb["answer"], "citations": kb["citations"]})
            usage["prompt_tokens"] += kb.get("usage", {}).get("prompt_tokens", 0)
            usage["completion_tokens"] += kb.get("usage", {}).get("completion_tokens", 0)
        elif intent == "agent":
            ag = agent_answer(question)
            result.update({"answer": ag["answer"], "steps": ag["steps"]})
            usage["prompt_tokens"] += ag.get("usage", {}).get("prompt_tokens", 0)
            usage["completion_tokens"] += ag.get("usage", {}).get("completion_tokens", 0)
        else:
            result.update({"answer": FALLBACK_TEXT})
    except nl2sql.Refusal as e:
        result.update({"branch": intent if intent in ("sql", "knowledge", "agent") else "fallback",
                       "answer": str(e), "refusal": True,
                       "intent_reason": why + "｜执行层拒答"})
    except Exception as e:
        result.update({"branch": "error", "error": str(e)})
    result["tokens"] = usage
    result["elapsed"] = round(time.perf_counter() - t0, 2)
    return result
