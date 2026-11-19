#!/usr/bin/env python3
"""四分支验收：8 个固定问题，检查「路由标签正确 + 分支产出非空」。

    python -m ask_sys.verify.branches
"""
import sys
import time

from ask_sys.ver_pipeline.router import answer_question

CASES = [
    ("12-02 的 DAU 是多少", "sql"),
    ("哪些类目卖得最好", "sql"),
    ("复购率的口径怎么定义", "knowledge"),
    ("为什么用 PostgreSQL 而不是 MySQL", "knowledge"),
    ("项目的对账是怎么做的", "knowledge"),
    ("为什么 12-02 的 DAU 比前一天高", "agent"),
    ("对比 12-01 和 12-02 的购买人数并解释", "agent"),
    ("你好", "fallback"),
]


def filled(result):
    branch = result.get("branch")
    if branch == "sql":
        return (result.get("row_count") or 0) > 0
    if branch == "knowledge":
        return len(result.get("answer") or "") >= 20 and bool(result.get("citations"))
    if branch == "agent":
        return len(result.get("answer") or "") >= 20 and bool(result.get("steps"))
    if branch == "fallback":
        return len(result.get("answer") or "") >= 10
    return False


def main():
    fails = 0
    for question, expected in CASES:
        t0 = time.perf_counter()
        result = answer_question(question)
        ok = result.get("branch") == expected and filled(result)
        fails += 0 if ok else 1
        detail = ""
        if result.get("branch") == "sql":
            detail = f"rows={result.get('row_count')}"
        elif result.get("branch") == "knowledge":
            detail = f"cites={len(result.get('citations') or [])}"
        elif result.get("branch") == "agent":
            detail = f"steps={len(result.get('steps') or [])}"
        print(f"[{'PASS' if ok else 'FAIL'}] {question} → {result.get('branch')} "
              f"(期望 {expected}, {round(time.perf_counter() - t0, 1)}s, {detail})")
        if not ok:
            print(f"       { (result.get('answer') or result.get('reason') or result.get('error') or '')[:160] }")
    print(f"\n==== 结果：{'全部通过' if fails == 0 else f'{fails} 项失败'} ====")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
