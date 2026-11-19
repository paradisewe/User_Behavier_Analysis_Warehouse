#!/usr/bin/env python3
"""知识检索标注集：问题 → 期望命中的块标识（source + 标题关键词）。

标注原则（评测先于实现——先有答案，再看好不好看）：
- expect 里每项是 (source 前缀, 标题必须包含的词)；命中任一即算该题召回成功；
- 「别名组」题目考察同义改写（词法召不回、向量应该召回）；
- 「精确组」考察术语/编号命中（词法强项，向量不能退化）；
- 「歧义组」期望多块或断言「不该命中什么」（负样本）。

用法：python -m ask_sys.verify.knowledge
"""
import re

CASES = [
    # ---------- 口径精确组（8）：术语直给，词法应命中 ----------
    ("复购率的口径怎么定义", [("meta.metric_info", "复购率")]),
    ("次日留存率是怎么算的", [("meta.metric_info", "次日留存")]),
    ("RFM 里的重要价值客户怎么划分", [("meta.metric_info", "RFM")]),
    ("类目关联规则的提升度是什么意思", [("meta.metric_info", "提升度")]),
    ("DAU 和 UV 是什么关系", [("meta.metric_info", "DAU")]),
    ("支持度和置信度怎么算", [("meta.metric_info", "支持度"), ("meta.metric_info", "置信度")]),
    ("路径转化率和漏斗转化率有什么区别", [("meta.metric_info", "路径购买转化率"), ("meta.metric_info", "漏斗转化率")]),
    ("有效购物篮是怎么定义的", [("meta.metric_info", "购物篮")]),
    # ---------- 别名/同义组（10）：考察向量相对词法的增量 ----------
    ("用户回头率怎么定义", [("meta.metric_info", "复购率")]),
    ("每天大概有多少人来逛", [("meta.metric_info", "DAU")]),
    ("哪些品类卖得最好", [("meta.metric_info", "类目购买次数"), ("meta.metric_info", "Top10")]),
    ("用户买过几次算回头客", [("meta.metric_info", "复购率")]),
    ("新用户第二天还会再来吗怎么看", [("meta.metric_info", "次日留存")]),
    ("买东西的人一般一个月来几次", [("meta.metric_info", "活跃天数")]),
    ("哪种行为最容易流失", [("docs/", "分层"), ("docs/", "浏览")]),
    ("经常被一起购买的商品组合怎么找", [("meta.metric_info", "提升度"), ("meta.metric_info", "关联")]),
    ("重要客户流失预警应该看哪个指标", [("meta.metric_info", "R 值"), ("meta.metric_info", "recency")]),
    ("客单价相关的指标有哪些", [("meta.metric_info", "购买次数"), ("meta.metric_info", "购买用户")]),
    # ---------- 工程方法组（8）：文档章节 ----------
    ("为什么用 PostgreSQL 不用 MySQL", [("README.md", "PostgreSQL"), ("docs/", "PostgreSQL")]),
    ("这个项目的对账是怎么做的", [("docs/", "对账"), ("README.md", "对账")]),
    ("数据仓库为什么分这么多层", [("docs/", "分层"), ("docs/", "DWS")]),
    ("ODS 层为什么不清洗", [("docs/", "贴源"), ("docs/", "ODS")]),
    ("幂等是什么意思，为什么要保证", [("docs/", "幂等"), ("docs/", "OVERWRITE")]),
    ("SVG 还是 PNG 无所谓，四层之间的数据怎么流转", [("docs/", "ODS"), ("docs/", "分层")]),
    ("只有 9 天数据为什么还要分区", [("docs/", "分区"), ("docs/", "贴源")]),
    ("怎么防止 AI 生成危险 SQL", [("README.md", "安全闸"), ("docs/", "安全")]),
    # ---------- 歧义/负样本组（6）：考察排序与拒答 ----------
    ("转化率是多少", [("meta.metric_info", "漏斗转化率"), ("meta.metric_info", "路径购买转化率")]),
    ("留存率是多少", [("meta.metric_info", "次日留存")]),
    ("复购是按商品还是按类目", [("meta.metric_info", "复购率"), ("docs/", "口径")]),
    ("汇率是多少", []),                      # 负样本：期望 0 命中
    ("今天天气怎么样", []),                   # 负样本
    ("帮我写一首诗", []),                     # 负样本
]


def _hit(chunk, expect):
    src_prefix, needle = expect
    return chunk["source"].startswith(src_prefix) and needle.lower() in chunk["heading"].lower()


def evaluate(search_fn, top_k=8):
    """返回 (recall@k, mrr, 命中明细)。负样本题命中任意块记噪声，不命中记对。"""
    hit_cnt, mrr_sum, details = 0, 0.0, []
    for question, expects in CASES:
        picked = search_fn(question, top_k=top_k)
        if not expects:                          # 负样本
            noise = len(picked)
            ok = noise == 0
            details.append((question, "negative", ok, f"噪声 {noise}"))
            hit_cnt += 1 if ok else 0
            continue
        rank = None
        for i, c in enumerate(picked, 1):
            if any(_hit(c, e) for e in expects):
                rank = i
                break
        ok = rank is not None
        hit_cnt += 1 if ok else 0
        mrr_sum += (1.0 / rank) if rank else 0.0
        details.append((question, f"rank={rank}" if rank else "miss", ok,
                        f"top={picked[0]['source'] + '#' + picked[0]['heading'][:20] if picked else '空'}"))
    n = len(CASES)
    return hit_cnt / n, mrr_sum / n, details


CASE_NAMES = {"exact": "精确", "alias": "别名", "method": "方法", "neg": "负样本"}
