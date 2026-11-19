#!/usr/bin/env python3
"""agent 可调用的三个工具。

纪律：工具永不抛异常——失败也以 {"error": "..."} 作为观察返回；
取数只走 nl2sql.ask（召回→guard→dw_ro→超时），这里不拼任何 SQL。
"""
import json
import time

from langchain_core.tools import tool

from ask_sys.common import knowledge, nl2sql
from ask_sys.common.db import q

MAX_SCHEMA_CHARS = 1500
MAX_KNOWLEDGE_CHARS = 2000
MAX_ROWS = 10


def _dump(obj):
    return json.dumps(obj, ensure_ascii=False)


@tool
def get_schema(table: str = "", metric: str = "") -> str:
    """查询数仓表结构或指标口径。

    参数：
    - table：表名，留空表示不按表查；必须存在于 meta.table_info。
    - metric：指标名或别名（如「DAU」「复购率」），留空表示不按指标查。
    两个都留空时返回全部表名清单。返回紧凑文本，最多 1500 字。
    """
    try:
        if table:
            # 元数据规模很小，一次取全再在 Python 里精确匹配：不把用户输入拼进 SQL
            found = [r for r in q("SELECT table_name, table_comment, grain, served_for "
                                  "FROM meta.table_info ORDER BY 1")
                     if r[0] == table]
            if not found:
                return f"未知表：{table}。请只使用 meta.table_info 里登记过的表。"
            name, comment, grain, served = found[0]
            lines = [f"{name}：{comment}", f"粒度：{grain}；用途：{served}", "字段："]
            for c, dtype, cmt in q(
                    "SELECT column_name, data_type, comment FROM meta.column_info "
                    f"WHERE table_name = '{name.replace(chr(39), chr(39) * 2)}' "
                    "ORDER BY ordinal"):
                lines.append(f"  {c} {dtype} -- {cmt}")
            return "\n".join(lines)[:MAX_SCHEMA_CHARS]

        if metric:
            key = metric.strip().lower()
            rows = [r for r in q(
                "SELECT metric_id, metric_name, aliases, definition, formula, "
                "source_table, value_column, time_window, caveat FROM meta.metric_info ORDER BY 1")
                if key in (r[1] or "").lower() or key in (r[2] or "").lower()]
            if not rows:
                return f"未知指标：{metric}。请用 meta.metric_info 里登记过的指标。"
            lines = []
            for mid, name, aliases, definition, formula, src, vcol, window, caveat in rows:
                lines.append(f"{name}（{mid}，别名：{aliases}）")
                lines.append(f"  定义：{definition}")
                lines.append(f"  公式：{formula}")
                lines.append(f"  来源：{src}.{vcol}；时间窗口：{window}")
                lines.append(f"  口径陷阱：{caveat}")
            return "\n".join(lines)[:MAX_SCHEMA_CHARS]

        rows = q("SELECT table_name, table_comment FROM meta.table_info ORDER BY 1")
        lines = ["可用表（需要字段时再按表名查）："]
        lines += [f"- {t} | {c}" for t, c in rows]
        return "\n".join(lines)[:MAX_SCHEMA_CHARS]
    except Exception as e:  # 元数据不可用也是一种观察
        return f"查询元数据失败：{e}"


@tool
def search_knowledge(query: str) -> str:
    """检索项目文档与指标口径说明；参数 query 是自然语言查询。最多返回 4 个片段。"""
    try:
        hits = knowledge.search(query, top_k=4)
        if not hits:
            return "知识库没有匹配内容。"
        blocks = []
        for i, h in enumerate(hits, 1):
            blocks.append(f"[{i}] {h['source']} / {h['heading']}\n{h['text']}")
        text = "\n\n".join(blocks)
        return text[:MAX_KNOWLEDGE_CHARS]
    except Exception as e:
        return f"检索知识库失败：{e}"


@tool
def query_data(question: str) -> str:
    """把一个「能用单条 SELECT 取数」的问题交给数仓执行，返回 JSON 字符串。

    question 必须写清指标与日期，例如「2017-12-01 的日购买用户数是多少」。
    返回：{sql, columns, rows(最多 10 行), row_count, error}。
    """
    t0 = time.perf_counter()
    met = {}
    try:
        sql, reason, text = nl2sql.ask(question, compact=True, header=True, metrics=met)
        lines = text.splitlines() if text else []
        rows = [ln.split("|") for ln in lines[1:]]
        return _dump({"question": question, "sql": sql, "reason": reason,
                      "columns": lines[0].split("|") if lines else [],
                      "rows": rows[:MAX_ROWS], "row_count": len(rows),
                      "error": None, "seconds": met.get("seconds")})
    except nl2sql.Refusal as e:
        return _dump({"question": question, "sql": None, "reason": None,
                      "columns": [], "rows": [], "row_count": 0,
                      "error": f"拒答：{e}", "seconds": round(time.perf_counter() - t0, 2)})
    except Exception as e:
        return _dump({"question": question, "sql": None, "reason": None,
                      "columns": [], "rows": [], "row_count": 0,
                      "error": str(e), "seconds": round(time.perf_counter() - t0, 2)})


# 名称索引：tools 节点按 tool_call.name 查表，未知名字不抛异常
TOOLS = [get_schema, search_knowledge, query_data]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}
