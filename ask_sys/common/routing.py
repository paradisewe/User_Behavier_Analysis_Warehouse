#!/usr/bin/env python3
"""问题分类器（两套编排共用）：LLM 四分类 + 关键词规则兜底。"""
import re

from .llm import call_llm, json_of

INTENTS = ("knowledge", "sql", "agent", "fallback")

PROMPT = """你是问数系统的问题分类器。把用户问题分到 4 类之一，只输出 JSON：
{"intent":"knowledge|sql|agent|fallback","reason":"不超过 20 字"}

分类定义与边界：
- sql：要一个能直接查库得到的数、清单或趋势。
  例：12-02 的 DAU 是多少 / 哪些类目卖得最好 / 最近几天每天有多少人购买 / 复购率是多少
- knowledge：问口径定义、算法口径、项目背景、方法、架构、为什么这么设计、怎么验证。
  例：复购率的口径怎么定义的 / 为什么用 PostgreSQL / 这个项目的对账是怎么做的 / RFM 怎么分层
- agent：需要多步取数再比较、归因或解释。
  例：为什么 12-02 的 DAU 比前一天高 / 对比两天的购买人数并解释差异 / 分析购买转化下降的原因
- fallback：闲聊、与本数据集无关、无法用上述方式回答。
  例：你好 / 明天天气怎么样 / 帮我写一首诗

判断技巧：
1. 「是多少 / 哪些 / 多少」→ sql；「怎么算 / 怎么定义 / 为什么」→ knowledge；
2. 问题里同时出现两个以上时间点且要求解释差异 → agent；
3. 拿不准时：只要能用一条 SELECT 答就选 sql，否则 knowledge。

只输出 JSON，不要 markdown，不要解释。"""


def rule_intent(question):
    if re.search(r"为什么|原因|归因|对比|相比|分析.*(下降|上涨|变化|差异)", question):
        return "agent"
    if re.search(r"口径|定义|怎么算|怎么定义|是什么|原理|背景|架构|怎么验证|对账|为什么|文档", question):
        return "knowledge"
    if re.search(r"多少|哪些|排名|榜单|趋势|每天|列出|给我|是多少|几个", question):
        return "sql"
    return "fallback"


def classify(question):
    """返回 (intent, reason, usage)。模型失败时退回规则，路由不是单点故障。"""
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    try:
        reply, u = call_llm(f"{PROMPT}\n\n用户问题：{question}",
                            max_tokens=64, timeout=15)
        usage["prompt_tokens"] += u.get("prompt_tokens", 0)
        usage["completion_tokens"] += u.get("completion_tokens", 0)
        obj = json_of(reply) or {}
        intent = (obj.get("intent") or "").strip().lower()
        if intent in INTENTS:
            return intent, obj.get("reason", ""), usage
    except Exception:
        pass
    return rule_intent(question), "规则兜底（分类模型不可用）", usage
