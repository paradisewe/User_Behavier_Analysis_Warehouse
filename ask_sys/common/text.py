#!/usr/bin/env python3
"""中文/英文轻量分词与打分（2-gram），供召回与知识检索共用。"""
import re

STOP = {"最近", "一周", "今天", "昨天", "本月", "上月", "这个", "那个"}


def tokens(text):
    """英文/数字整词 + 中文按 2 字切。足够跑通召回，不做真正分词。"""
    out = set()
    for run in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", text or ""):
        if re.match(r"[A-Za-z0-9_]", run):
            out.add(run.lower())
        else:
            out.update(run[i:i + 2] for i in range(len(run) - 1))
    return out - STOP


def score(query_tokens, corpus):
    return sum(len(t) for t in query_tokens if t in (corpus or "").lower())
