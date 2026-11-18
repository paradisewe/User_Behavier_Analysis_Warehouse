#!/usr/bin/env python3
"""B-lite：问题 → 召回 meta → LLM 生成 SQL → 只读执行 → 打印结果。

模型走 OpenAI 兼容协议，配置全在 .env：
    LLM_BASE_URL=https://api.deepseek.com/v1
    LLM_API_KEY=...
    LLM_MODEL=deepseek-chat

用法：
    python scripts/ask_sql.py "12-02 的 DAU 是多少"
    python scripts/ask_sql.py --dry-run "复购率怎么算"     # 只打印 prompt，不调模型
    python scripts/ask_sql.py --batch                      # 跑 10 个固定问题，看整体可用性
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ask_demo import QUESTIONS, load_env, q, tokens, score  # noqa: E402

class Refusal(Exception):
    """问题无法用一条 SELECT 回答（能力/口径类问题）。"""


FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|copy|vacuum)\b", re.I)
HOUR_HINT = re.compile(r"\d{1,2}\s*点")
LAST = {"seconds": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "rounds": 0}  # 上一次 ask 的耗时/用量/轮次


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
                          "value": vcol, "formula": formula, "window": window, "caveat": caveat})
    mhits.sort(key=lambda x: -x["score"])
    if mhits:
        floor = max(1, mhits[0]["score"] * 0.4)
        mhits = [m for m in mhits if m["score"] >= floor]
    mhits = mhits[:2]

    chits = []
    for t, c, dtype, comment in columns:
        s = score(qt, f"{c} {comment}")
        if s:
            chits.append({"score": s, "table": t, "col": c, "type": dtype, "comment": comment})
    chits.sort(key=lambda x: -x["score"])

    return mhits, chits[:4], tables, columns


def value_hints(table, all_columns):
    """枚举列给实际取值，日期列给范围——防止模型把 '重要价值客户' 缩写成 '重要价值' 导致静默 0 行。"""
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
        "5. 枚举列只能使用【取值】里列出的原值，不要缩写或改写（例如 '重要价值客户' 不能写成 '重要价值'）。",
        "6. 相对时间（最近/上周）以口径窗口末日为基准；查询为空时不要编造数据。",
        "7. 比率/均值用 numeric：ROUND(x::numeric, 4)；PG 没有 round(double precision, int)。",
        "8. 不能用给定表的一条 SELECT 回答时（能力/解释类问题、缺少所需口径），输出 {\"sql\": null, \"reason\": \"缺少什么口径\"}。",
        "9. 输出 JSON：{\"sql\": \"<SQL>\", \"reason\": \"<一句话为什么这么查>\"}",
    ]
    return "\n".join(lines)


def call_llm(prompt):
    base = os.environ["LLM_BASE_URL"].rstrip("/")
    r = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['LLM_API_KEY']}",
                 "Content-Type": "application/json"},
        json={"model": os.environ["LLM_MODEL"], "temperature": 0,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"模型接口 {r.status_code}: {r.text[:300]}")
    data = r.json()
    return data["choices"][0]["message"]["content"], data.get("usage", {})


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


def run(sql, compact=False):
    extra = ["-t", "-A"] if compact else ["-P", "pager=off"]
    r = subprocess.run(
        ["docker", "exec", "-e", f"PGPASSWORD={os.environ['PG_RO_PASSWORD']}",
         "pg16", "psql", "-h", "127.0.0.1", "-U", "dw_ro", "-d", "dw"] + extra + ["-c", sql],
        capture_output=True, text=True, encoding="utf-8", timeout=40)
    if r.returncode != 0:
        raise RuntimeError(f"执行失败：{r.stderr.strip()}")
    return r.stdout.strip()


def answer(question):
    mhits, chits, tables, columns = recall(question)
    prompt = build_prompt(question, mhits, chits, tables, columns)
    reply, _usage = call_llm(prompt)
    sql, reason = parse_sql(reply)
    return guard(sql), reason, prompt


def ask(question, repair=1, compact=False):
    """生成并执行；占位 SQL 或执行失败时把原因回灌给模型重写一次（B2 的 repair 步骤）。"""
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
                return sql, reason, run(sql, compact=compact)
            except RuntimeError as e:
                last_error = e
                prompt += f"\n\n【上一次 SQL 执行失败】\n{sql}\n错误: {e}\n请修正后重新输出 JSON。"
        raise last_error
    finally:
        LAST["seconds"] = round(time.perf_counter() - t0, 2)
        LAST["rounds"] = attempts
        LAST.update(usage)


def load_questions(path):
    """读评测文件：# 开头是分组名，其余每行一个问题。"""
    groups = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            groups.append((line.lstrip("# ").strip(), []))
        else:
            if not groups:
                groups.append(("未分组", []))
            groups[-1][1].append(line)
    return groups


def main():
    argv = sys.argv[1:]
    dry = "--dry-run" in argv
    batch = "--batch" in argv
    path = None
    if "--file" in argv:
        i = argv.index("--file")
        path = argv[i + 1]
        del argv[i:i + 2]
    args = [a for a in argv if not a.startswith("--")]

    load_env()

    if batch or path:
        for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
            if not os.environ.get(key):
                sys.exit(f"缺少 {key}（写进 .env）")
        groups = load_questions(path) if path else [("固定问题", QUESTIONS)]
        stats, n = [], 0
        for title, questions in groups:
            print(f"\n=== {title} ===")
            for question in questions:
                n += 1
                outcome = "answered"
                try:
                    sql, _reason, result = ask(question, compact=True)
                    print(f"{n}. {question}\n   SQL: {sql}\n   结果: {result or '(空 — 可能该日期无数据或不在口径窗口)'}")
                except Refusal as e:
                    outcome = "refused"
                    print(f"{n}. {question}\n   无此口径: {e}")
                except Exception as e:
                    outcome = "failed"
                    print(f"{n}. {question}\n   FAIL: {e}")
                stats.append((outcome, LAST["seconds"], LAST["prompt_tokens"],
                              LAST["completion_tokens"], LAST["rounds"]))

        def pctl(values, p):
            if not values:
                return 0.0
            values = sorted(values)
            return values[min(len(values) - 1, round((len(values) - 1) * p))]

        secs = [x[1] for x in stats]
        answered = sum(1 for o, *_ in stats if o == "answered")
        refused = sum(1 for o, *_ in stats if o == "refused")
        failed = sum(1 for o, *_ in stats if o == "failed")
        avg_rounds = sum(x[4] for x in stats) / len(stats) if stats else 0
        print("\n--- 汇总 ---")
        print(f"题数 {len(stats)} | 给数 {answered} | 拒答 {refused} | 失败 {failed}")
        print(f"耗时 P50 {pctl(secs, 0.5):.1f}s / P95 {pctl(secs, 0.95):.1f}s | 平均轮次 {avg_rounds:.2f}")
        print(f"tokens {sum(x[2] + x[3] for x in stats)}"
              f"（prompt {sum(x[2] for x in stats)} / completion {sum(x[3] for x in stats)}）")
        return

    if not args:
        sys.exit('用法: python scripts/ask_sql.py "问题"')
    question = args[0]

    if dry:
        mhits, chits, tables, columns = recall(question)
        print(build_prompt(question, mhits, chits, tables, columns))
        return

    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        if not os.environ.get(key):
            sys.exit(f"缺少 {key}（写进 .env）")
    try:
        sql, reason, result = ask(question)
    except Refusal as e:
        sys.exit(f"无此口径：{e}")
    except Exception as e:
        sys.exit(f"[FAIL] {e}")

    print(f"问题: {question}")
    print(f"SQL : {sql}")
    print(f"理由: {reason}")
    print("\n结果:")
    print(result or "(空 — 可能该日期无数据或不在口径窗口)")


if __name__ == "__main__":
    main()
