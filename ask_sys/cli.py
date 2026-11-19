#!/usr/bin/env python3
"""统一 CLI。

    python -m ask_sys.cli "12-02 的 DAU 是多少"          # 四分支，输出 JSON
    python -m ask_sys.cli --engine graph "12-02 的 DAU 是多少"  # LangGraph 编排
    python -m ask_sys.cli --sql "12-02 的 DAU 是多少"    # 只走 NL2SQL
    python -m ask_sys.cli --sql --dry-run "复购率是多少" # 只看 prompt
    python -m ask_sys.cli --sql --batch                  # 跑固定 10 题
    python -m ask_sys.cli --sql --file eval/questions.txt
"""
import json
import sys

from ask_sys.common import nl2sql
from ask_sys.common.env import load_env, require
from ask_sys.ver_pipeline.router import answer_question

LLM_KEYS = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")


def _need_llm():
    load_env()
    for key in LLM_KEYS:
        require(key)


def _pctl(values, p):
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, round((len(values) - 1) * p))]


def load_questions(path):
    """读评测文件：# 开头是分组名，其余每行一个问题。"""
    groups = []
    for line in open(path, encoding="utf-8"):
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


def batch(path=None):
    from ask_sys.dev.ask_demo import QUESTIONS
    groups = load_questions(path) if path else [("固定问题", QUESTIONS)]
    stats = []
    n = 0
    for title, questions in groups:
        print(f"\n=== {title} ===")
        for question in questions:
            n += 1
            outcome = "answered"
            try:
                sql, _reason, result = nl2sql.ask(question, compact=True)
                print(f"{n}. {question}\n   SQL: {sql}\n   结果: {result or '(空)'}")
            except nl2sql.Refusal as e:
                outcome = "refused"
                print(f"{n}. {question}\n   无此口径: {e}")
            except Exception as e:
                outcome = "failed"
                print(f"{n}. {question}\n   FAIL: {e}")
            stats.append((outcome, nl2sql.LAST["seconds"], nl2sql.LAST["prompt_tokens"],
                          nl2sql.LAST["completion_tokens"], nl2sql.LAST["rounds"]))

    secs = [x[1] for x in stats]
    answered = sum(1 for o, *_ in stats if o == "answered")
    refused = sum(1 for o, *_ in stats if o == "refused")
    failed = sum(1 for o, *_ in stats if o == "failed")
    print("\n--- 汇总 ---")
    print(f"题数 {len(stats)} | 给数 {answered} | 拒答 {refused} | 失败 {failed}")
    print(f"耗时 P50 {_pctl(secs, 0.5):.1f}s / P95 {_pctl(secs, 0.95):.1f}s")
    print(f"tokens {sum(x[2] + x[3] for x in stats)}"
          f"（prompt {sum(x[2] for x in stats)} / completion {sum(x[3] for x in stats)}）")


def main():
    argv = sys.argv[1:]
    dry = "--dry-run" in argv
    use_sql = "--sql" in argv
    is_batch = "--batch" in argv
    path = None
    if "--file" in argv:
        i = argv.index("--file")
        path = argv[i + 1]
        del argv[i:i + 2]

    # 引擎：缺省 pipeline，graph 走 LangGraph；两种写法都支持
    engine = "pipeline"
    if "--engine" in argv:
        i = argv.index("--engine")
        if i + 1 >= len(argv):
            sys.exit("--engine 需要取值：pipeline|graph")
        engine = argv[i + 1]
        del argv[i:i + 2]
    for a in list(argv):
        if a.startswith("--engine="):
            engine = a.split("=", 1)[1]
            argv.remove(a)
    if engine not in ("pipeline", "graph"):
        sys.exit(f"未知引擎：{engine}（可选 pipeline|graph）")
    if engine == "graph" and use_sql:
        sys.exit("--engine graph 不能与 --sql 同用（graph 走四分支）")

    args = [a for a in argv if not a.startswith("--")]

    _need_llm()

    if use_sql and (is_batch or path):
        batch(path)
        return

    if not args:
        sys.exit('用法: python -m ask_sys.cli [--sql|--dry-run] "问题"')

    question = args[0]
    if use_sql:
        if dry:
            mhits, chits, tables, columns = nl2sql.recall(question)
            print(nl2sql.build_prompt(question, mhits, chits, tables, columns))
            return
        try:
            sql, reason, result = nl2sql.ask(question)
        except nl2sql.Refusal as e:
            sys.exit(f"无此口径：{e}")
        except Exception as e:
            sys.exit(f"[FAIL] {e}")
        print(f"问题: {question}\nSQL : {sql}\n理由: {reason}\n\n结果:\n{result or '(空)'}")
        return

    if engine == "graph":
        # 延迟导入：默认 pipeline 路径不加载 langgraph 依赖
        from ask_sys.ver_langgraph import answer as graph_answer
        print(json.dumps(graph_answer(question), ensure_ascii=False, indent=1))
        return

    print(json.dumps(answer_question(question), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
