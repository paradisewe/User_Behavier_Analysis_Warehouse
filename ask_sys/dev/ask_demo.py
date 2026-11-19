#!/usr/bin/env python3
"""A3 检索 demo：问题 → 查 meta（指标/字段/表）→ 打印上下文。

    python -m ask_sys.dev.ask_demo            # 跑 10 个固定问题
    python -m ask_sys.dev.ask_demo "复购率怎么算"
"""
import sys

from ask_sys.common.db import q
from ask_sys.common.text import score, tokens

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


def main():
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

        grain_map = {t: grain for t, _c, grain, _s in tables}
        if hits:
            print(f"  表:   {hits[0][4]}  ({grain_map.get(hits[0][4], '')})")


if __name__ == "__main__":
    main()
