#!/usr/bin/env python3
"""A3 最小闭环：问题 → 查 meta（指标/字段/表）→ 打印上下文。

不引入向量、不改 schema、不做验收脚本：先看「元数据写得够不够让检索认出来」。
用法：
    /d/pythonS/anaconda/python.exe scripts/ask_demo.py            # 跑 10 个固定问题
    /d/pythonS/anaconda/python.exe scripts/ask_demo.py "复购率怎么算"
"""
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

QUESTIONS = [
    "12-02 的 DAU 是多少",
    "最近一周每天有多少人购买",
    "复购率怎么算",
    "哪些类目卖得最好",
    "用户次日留存怎么样",
    "加购到购买的转化率",
    "哪些类目经常一起买",
    "RFM 里重要价值客户有多少",
    "用户一般在哪个时段最活跃",
    "核心窗口的路径购买转化率",
]


def load_env():
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    if not os.environ.get("PG_RO_PASSWORD"):
        sys.exit("缺少 PG_RO_PASSWORD（.env）")


def q(sql):
    """以只读账号走 TCP 查 meta。"""
    r = subprocess.run(
        ["docker", "exec", "-e", f"PGPASSWORD={os.environ['PG_RO_PASSWORD']}",
         "pg16", "psql", "-h", "127.0.0.1", "-U", "dw_ro", "-d", "dw",
         "-t", "-A", "-F", "|", "-c", sql],
        capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        sys.exit(r.stderr.strip())
    return [line.split("|") for line in r.stdout.strip().splitlines() if line]


STOP = {"最近", "一周", "今天", "昨天", "本月", "上月", "这个", "那个"}


def tokens(text):
    """英文/数字整词 + 中文按 2 字切。足够跑通 demo，不做分词。"""
    out = set()
    for run in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", text):
        if re.match(r"[A-Za-z0-9_]", run):
            out.add(run.lower())
        else:
            out.update(run[i:i + 2] for i in range(len(run) - 1))
    return out - STOP


def score(qtokens, corpus):
    corpus = corpus.lower()
    return sum(len(t) for t in qtokens if t in corpus)


def main():
    load_env()
    metrics = q("SELECT metric_id, metric_name, aliases, definition, formula, "
                "source_table, value_column, time_window FROM meta.metric_info")
    columns = q("SELECT table_name, column_name, comment FROM meta.column_info")
    tables = q("SELECT table_name, table_comment, grain, served_for FROM meta.table_info")

    questions = [sys.argv[1]] if len(sys.argv) > 1 else QUESTIONS
    for question in questions:
        qt = tokens(question)
        print(f"\nQ: {question}")

        hits = []
        for mid, name, aliases, definition, formula, src, vcol, window in metrics:
            s = 3 * score(qt, f"{name} {aliases}") + score(qt, definition)
            if s:
                hits.append((s, mid, name, formula, src, vcol, window))
        hits.sort(reverse=True)

        if not hits:
            print("  （没有匹配的指标——考虑给 aliases 补同义词）")
        for s, mid, name, formula, src, vcol, window in hits[:2]:
            print(f"  指标: {mid} | {name}   (score={s})")
            print(f"        表: {src} / 值列: {vcol}")
            print(f"        公式: {formula}")
            print(f"        口径: {window}")

        chits = []
        for t, c, comment in columns:
            s = score(qt, f"{c} {comment}")
            if s:
                chits.append((s, t, c, comment))
        chits.sort(reverse=True)
        for s, t, c, comment in chits[:3]:
            print(f"  字段: {t}.{c}  ({comment})")

        thits = []
        for t, comment, grain, served in tables:
            s = score(qt, f"{comment} {grain} {served}")
            if s:
                thits.append((s, t, grain))
        thits.sort(reverse=True)
        grain_map = {t: grain for t, _c, grain, _s in tables}
        if hits:
            print(f"  表:   {hits[0][4]}  ({grain_map.get(hits[0][4], '')})")
        elif thits:
            print(f"  表:   {thits[0][1]}  ({thits[0][2]})")


if __name__ == "__main__":
    main()
