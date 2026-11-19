#!/usr/bin/env python3
"""NL2SQL 主链（两套编排共用）：召回 → 拼 prompt → 生成 → 解析/安全闸 → 只读执行 → repair。

这一层是唯一能执行 SQL 的地方；编排层（pipeline / langgraph）只调用它，不复制实现。
"""
import json
import re
import time

from .db import psql, q
from .llm import call_llm
from .text import score, tokens


class Refusal(Exception):
    """问题无法用一条 SELECT 回答（能力/口径类问题）。"""


FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|copy|vacuum)\b", re.I)
HOUR_HINT = re.compile(r"\d{1,2}\s*点")
LAST = {"seconds": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "rounds": 0}


def recall(question):
    qt = tokens(question)
    metrics = q("SELECT metric_id, metric_name, aliases, definition, formula, source_table, "
                "value_column, time_window, caveat FROM meta.metric_info")
    columns = q("SELECT table_name, column_name, data_type, comment FROM meta.column_info")
    tables = q("SELECT table_name, table_comment, grain, served_for FROM meta.table_info")

    mhits = []
    for mid, name, aliases, definition, formula, src, vcol, window, caveat in metrics:
        s = 3 * score(qt, f"{name} {aliases}") + score(qt, definition)
        if mid == "hourly_share" and HOUR_HINT.search(question):
            s += 10
        if s:
            mhits.append({"score": s, "id": mid, "name": name, "table": src,
                          "value": vcol, "formula": formula, "window": window,
                          "caveat": caveat})
    mhits.sort(key=lambda x: -x["score"])
    if mhits:
        floor = max(1, mhits[0]["score"] * 0.4)
        mhits = [m for m in mhits if m["score"] >= floor]
    mhits = mhits[:2]

    chits = []
    for t, c, dtype, comment in columns:
        s = score(qt, f"{c} {comment}")
        if s:
            chits.append({"score": s, "table": t, "col": c, "type": dtype,
                          "comment": comment})
    chits.sort(key=lambda x: -x["score"])

    return mhits, chits[:4], tables, columns


def value_hints(table, all_columns):
    """枚举列给实际取值，日期列给范围——防止模型缩写枚举值导致静默 0 行。"""
    hints = []
    for tt, c, dtype, _comment in all_columns:
        if tt != table:
            continue
        if dtype.startswith("character"):
            vals = [r[0] for r in q(f"SELECT DISTINCT {c}::text FROM {table} "
                                   f"WHERE {c} IS NOT NULL LIMIT 21")]
            if 0 < len(vals) <= 20:
                hints.append(f"  {c} 取值: " + "、".join(vals))
        elif dtype == "date":
            lo, hi, n = q(f"SELECT min({c})::text, max({c})::text, "
                          f"count(DISTINCT {c}) FROM {table}")[0]
            hints.append(f"  {c} 范围: {lo} ~ {hi}（{n} 个日期）")
    return hints


def build_prompt(question, mhits, chits, tables, all_columns):
    lines = ["你是数据仓库的 SQL 生成器。只输出 JSON，不要解释、不要 markdown。", "",
             f"【问题】{question}", ""]

    if mhits:
        lines.append("【候选指标（优先用这些）】")
        for m in mhits:
            lines.append(f"- {m['id']} | {m['name']}")
            lines.append(f"  表: {m['table']} / 值列: {m['value']} / 公式: {m['formula']}")
            lines.append(f"  口径: {m['window']}；注意: {m['caveat']}")
        lines.append("")

    want_tables = []
    for m in mhits:
        if m["table"] not in want_tables:
            want_tables.append(m["table"])
    for c in chits:
        if c["table"] not in want_tables:
            want_tables.append(c["table"])
    grain = {t: g for t, _c, g, _s in tables}
    lines.append("【表结构】")
    for t in want_tables:
        lines.append(f"{t}（{grain.get(t, '')}）")
        for tt, c, dtype, comment in all_columns:
            if tt == t:
                lines.append(f"  {c} {dtype}  -- {comment}")
        lines.extend(value_hints(t, all_columns))
        lines.append("")

    lines += [
        "【规则】",
        "1. 只生成一条 SELECT（可带 WITH），禁止任何写操作。",
        "2. 表名、列名必须来自上面的表结构，不要编造。",
        "3. 日期列是 DATE，写成 'YYYY-MM-DD'；不要用 CURRENT_DATE。",
        "4. 问题没指定日期时，用该指标口径里的时间窗口。",
        "5. 枚举列只能使用【取值】里列出的原值，不要缩写或改写。",
        "6. 相对时间（最近/上周）以口径窗口末日为基准；查询为空时不要编造数据。",
        "7. 比率/均值用 numeric：ROUND(x::numeric, 4)；PG 没有 round(double precision, int)。",
        "8. 不能用给定表的一条 SELECT 回答时，输出 {\"sql\": null, \"reason\": \"缺少什么口径\"}。",
        "9. 输出 JSON：{\"sql\": \"<SQL>\", \"reason\": \"<一句话为什么这么查>\"}",
    ]
    return "\n".join(lines)


def parse_sql(reply):
    text = re.sub(r"^```(?:json)?|```$", "", reply.strip(), flags=re.M).strip()
    try:
        obj = json.loads(text)
        sql = obj.get("sql")
        if not sql:
            raise Refusal(obj.get("reason") or "模型判定无法用一条 SQL 回答")
        return sql.strip(), obj.get("reason", "")
    except Refusal:
        raise
    except Exception:
        m = re.search(r"(select|with)\b.*", text, re.I | re.S)
        if not m:
            raise Refusal(f"模型没有返回 SQL：{reply[:120]}")
        return m.group(0).strip().rstrip(";"), "(未返回 JSON，按文本解析)"


def guard(sql):
    body = sql.strip().rstrip(";")
    if ";" in body:
        raise ValueError("只允许单条语句")
    if not re.match(r"^\s*(select|with)\b", body, re.I):
        raise ValueError("只允许 SELECT")
    hit = FORBIDDEN.search(body)
    if hit:
        raise ValueError(f"SQL 含禁止关键字：{hit.group(0)}")
    if not re.search(r"\blimit\b", body, re.I):
        body += " LIMIT 100"
    return body


def run(sql, compact=False, header=False):
    """执行 SQL。header=True 保留列名（网页表格用）；默认行为不变。"""
    if header:
        extra = ("-A", "-F", "|", "-P", "footer=off")
    elif compact:
        extra = ("-t", "-A")
    else:
        extra = ("-P", "pager=off")
    return psql(sql, extra=extra).strip()


def answer(question):
    mhits, chits, tables, columns = recall(question)
    prompt = build_prompt(question, mhits, chits, tables, columns)
    reply, _usage = call_llm(prompt)
    sql, reason = parse_sql(reply)
    return guard(sql), reason, prompt


def ask(question, repair=1, compact=False, header=False, metrics=None):
    """生成并执行；占位 SQL 或执行失败时把原因回灌模型重写（B2 的 repair）。

    metrics：可选字典，本函数把耗时/轮次/tokens 写回去（并发调用不抢全局 LAST）。
    """
    t0 = time.perf_counter()
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    attempts = 0
    try:
        mhits, chits, tables, columns = recall(question)
        if not mhits:
            raise Refusal("当前元数据里没有匹配的指标，无法生成 SQL（需要先补 metric_info / aliases）")
        prompt = build_prompt(question, mhits, chits, tables, columns)
        last_error = None
        for _ in range(repair + 1):
            attempts += 1
            reply, u = call_llm(prompt)
            usage["prompt_tokens"] += u.get("prompt_tokens", 0)
            usage["completion_tokens"] += u.get("completion_tokens", 0)
            sql, reason = parse_sql(reply)
            sql = guard(sql)
            if re.match(r"(?is)^\s*select\s+null\b", sql):
                last_error = ValueError("模型返回了占位 SQL（SELECT NULL），没有真正计算")
                prompt += "\n\n【上一次返回值是占位 SQL】不要用 SELECT NULL 或常数列，必须基于给定表实际计算。"
                continue
            try:
                return sql, reason, run(sql, compact=compact, header=header)
            except RuntimeError as e:
                last_error = e
                prompt += f"\n\n【上一次 SQL 执行失败】\n{sql}\n错误: {e}\n请修正后重新输出 JSON。"
        raise last_error
    finally:
        LAST["seconds"] = round(time.perf_counter() - t0, 2)
        LAST["rounds"] = attempts
        LAST.update(usage)
        if metrics is not None:
            metrics.update(LAST)
