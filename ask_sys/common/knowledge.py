#!/usr/bin/env python3
"""知识分支的语料与检索（两套编排共用）。

语料 = README + docs/项目介绍 + docs/learning/*.md + meta.metric_info（一条一块）。
检索 = bge-small-zh 向量（pgvector HNSW）+ 指标块强制保留。

为什么选纯向量而不是混合 RRF（2026-11-19 实测，32 题标注集）：
  词法 Recall@8=43.75% / 向量 59.38% / 混合 53.12%——词法路在别名题上全是高分噪声，
  经 RRF 加成后把向量路的正确块挤下去，混合反而低于纯向量。
  向量在精确术语上的退化（如「DAU 和 UV」）由 _forced_metric_chunks 兜住。
  对照评测与标注集：ask_sys/verify/knowledge.py、knowledge_cases.py。
"""
import glob
import os
from pathlib import Path

import numpy as np

from .db import q
from .env import REPO
from .llm import call_llm
from .text import score, tokens

CHUNK = 800
OVERLAP = 100
TOP_K = 8
MAX_PER_SOURCE = 3
EMBED_MODEL = "BAAI/bge-small-zh-v1.5"
EMBED_DIM = 512

PROMPT = """你是这个数据仓库项目的知识助手。只依据给出的资料回答，用中文，最多 250 字。

要求：
1. 资料里有的才说，用 [编号] 标注来源；
2. 资料没覆盖就明确说「资料里没有这个说明」，并建议改用查数或补充文档；
3. 不要编造数字或口径；
4. 不要输出 SQL 结果。

【资料】
{docs}

【问题】{question}"""


def _split(path, heading, body):
    body = body.strip()
    if not body:
        return []
    rel = path.relative_to(REPO).as_posix()
    if len(body) <= CHUNK:
        return [{"source": rel, "heading": heading, "text": body}]
    chunks, start = [], 0
    while start < len(body):
        chunks.append({"source": rel, "heading": heading,
                       "text": body[start:start + CHUNK]})
        start += CHUNK - OVERLAP
    return chunks


def _snip(text):
    """引用摘要：跳过表格/代码行，避免 markdown 残渣进界面。"""
    lines = [ln.strip() for ln in text.splitlines()
             if ln.strip() and not ln.strip().startswith(("|", "---", "```", "!["))]
    return " ".join(lines)[:160]


def chunks():
    files = [REPO / "README.md", REPO / "docs/项目介绍.md"]
    files += [Path(p) for p in sorted(glob.glob(str(REPO / "docs/learning/*.md")))]
    out = []
    for path in files:
        if not path.exists():
            continue
        heading = path.stem
        buf = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("#"):
                if buf:
                    out.extend(_split(path, heading, "\n".join(buf)))
                    buf = []
                heading = line.lstrip("#").strip() or heading
            else:
                buf.append(line)
        if buf:
            out.extend(_split(path, heading, "\n".join(buf)))

    # 指标口径是「问怎么算」的第一手资料（config/metrics.json → meta.metric_info）
    try:
        for mid, name, definition, formula, grain, src, vcol, window, caveat in q(
                "SELECT metric_id, metric_name, definition, formula, grain, source_table, "
                "value_column, time_window, caveat FROM meta.metric_info ORDER BY 1"):
            out.append({
                "source": "meta.metric_info",
                "heading": f"指标口径：{name}（{mid}）",
                "text": (f"{name}（{mid}）：{definition}。公式：{formula}。粒度：{grain}；"
                         f"时间窗口：{window}。来源：{src}.{vcol}。口径陷阱：{caveat}"),
            })
    except Exception:  # 元数据不可用时退回纯文档
        pass
    return out


def _forced_metric_chunks(question, ranked):
    """问题命中指标时，强制保留该指标块——并列分情况下它常被挤出 top-k。"""
    from .nl2sql import recall          # 局部导入，避免模块级循环依赖
    try:
        mhits, *_ = recall(question)
    except Exception:
        return []
    ids = {m["id"] for m in mhits}
    if not ids:
        return []
    return [c for c in ranked
            if c.get("source") == "meta.metric_info"
            and any(f"（{mid}）" in c.get("heading", "") for mid in ids)]


_CONTENT_CACHE = {}


def _content(src, head):
    """按 (source, heading) 找回块正文；向量化表里存了一份，优先用它。"""
    key = (src, head)
    if key in _CONTENT_CACHE:
        return _CONTENT_CACHE[key]
    for c in chunks():
        _CONTENT_CACHE[(c["source"], c["heading"])] = c["text"]
    return _CONTENT_CACHE.get(key, "")


_MODEL_CACHE = {}


def _query_vector(question):
    """问题 → 归一化向量。模型进程内缓存（加载 0.2s、推理 0.01s，重载才是延迟元凶）。"""
    if "model" in _MODEL_CACHE:
        model = _MODEL_CACHE["model"]
    else:
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        from fastembed import TextEmbedding
        model = TextEmbedding(EMBED_MODEL, cache_dir=os.environ.get("FASTEMBED_CACHE_PATH"))
        _MODEL_CACHE["model"] = model
    v = list(model.query_embed([question]))[0]
    v = np.asarray(v, dtype=np.float32)
    norm = float(np.linalg.norm(v))
    if norm == 0:
        raise RuntimeError("查询向量为零向量")
    return v / norm


def search(question, top_k=TOP_K):
    """向量检索 top-k + 指标块强制保留。表缺失/未建索引时退回词法。"""
    try:
        v = _query_vector(question)
    except Exception:
        return _lexical_search(question, top_k)   # 模型不可用：降级不报错

    lit = "[" + ",".join(f"{x:.7f}" for x in v) + "]"
    try:
        rows = q(f"""SELECT source, heading, 1 - (embedding <=> '{lit}') AS score
                     FROM meta.doc_embedding
                     ORDER BY embedding <=> '{lit}' LIMIT {top_k * 3}""")
    except Exception:
        return _lexical_search(question, top_k)   # 索引未建：降级不报错

    ranked = [{"source": src, "heading": head, "text": _content(src, head), "score": float(s)}
              for src, head, s in rows]
    ranked.sort(key=lambda c: -c["score"])

    forced = _forced_metric_chunks(question, ranked)
    picked, per_source, seen = [], {}, set()
    for c in forced:
        key = (c["source"], c["heading"])
        if key in seen:
            continue
        seen.add(key)
        picked.append(c)
        per_source[c["source"]] = per_source.get(c["source"], 0) + 1

    for c in ranked:
        if len(picked) >= top_k:
            break
        key = (c["source"], c["heading"])
        if key in seen or per_source.get(c["source"], 0) >= MAX_PER_SOURCE:
            continue
        per_source[c["source"]] = per_source.get(c["source"], 0) + 1
        seen.add(key)
        picked.append(c)
    return picked[:max(top_k, len(forced))]


def _lexical_search(question, top_k):
    """2-gram 词法检索：向量层的降级路径（模型/索引不可用时）。"""
    qt = tokens(question)
    ranked = sorted(
        ({"score": score(qt, f"{c['heading']} {c['text']}"), **c} for c in chunks()),
        key=lambda x: -x["score"])
    forced = _forced_metric_chunks(question, ranked)
    picked, per_source, seen = [], {}, set()
    for c in forced:
        key = (c["source"], c["heading"], c["text"])
        if key in seen:
            continue
        seen.add(key)
        picked.append(c)
        per_source[c["source"]] = per_source.get(c["source"], 0) + 1
    for c in ranked:
        if len(picked) >= top_k or c["score"] <= 0:
            break
        key = (c["source"], c["heading"], c["text"])
        if key in seen or per_source.get(c["source"], 0) >= MAX_PER_SOURCE:
            continue
        per_source[c["source"]] = per_source.get(c["source"], 0) + 1
        seen.add(key)
        picked.append(c)
    return picked[:max(top_k, len(forced))]


def answer(question):
    picked = search(question)
    if not picked:
        return {"answer": "知识库里没有找到相关内容。可以换个问法，或直接问一个可查的指标。",
                "citations": [],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0}}

    docs = "\n\n".join(
        f"[{i}] 来源：{c['source']} / 章节：{c['heading']}\n{c['text']}"
        for i, c in enumerate(picked, 1))
    try:
        reply, usage = call_llm(PROMPT.format(docs=docs, question=question),
                                max_tokens=800, timeout=60)
        text = reply.strip()
    except Exception:  # 回答模型超时就用检索片段兜底
        text = "回答模型暂时不可用。检索到的相关资料：" + \
               "；".join(c["text"][:120] for c in picked[:2])
        usage = {"prompt_tokens": 0, "completion_tokens": 0}

    return {
        "answer": text,
        "citations": [{"id": i, "source": c["source"], "heading": c["heading"],
                       "snippet": _snip(c["text"]), "score": c["score"]}
                      for i, c in enumerate(picked, 1)],
        "usage": usage,
    }
