#!/usr/bin/env python3
"""知识检索评测：词法（现状） vs 向量 vs 混合，同一标注集三行对照。

    python -m ask_sys.verify.knowledge            # 跑三种模式对照
    python -m ask_sys.verify.knowledge --mode lexical|vector|hybrid

指标：Recall@8（命中任一期望块的比例）、MRR、负样本噪声。
先建标注集（verify/knowledge_cases.py）再评——先有答案，再看好不好看。
"""
import argparse
import json
import os
import sys
import time

from ask_sys.common import knowledge
from ask_sys.common.env import load_env, require
from ask_sys.verify.knowledge_cases import CASES, evaluate


def lexical_search(question, top_k=8):
    return knowledge.search(question, top_k=top_k)


def _model():
    load_env()
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    from fastembed import TextEmbedding
    return TextEmbedding("BAAI/bge-small-zh-v1.5",
                         cache_dir=os.environ.get("FASTEMBED_CACHE_PATH"))


def _doc_text(c):
    return f"{c['heading']}。{c['text']}"


def build_vector_store(force=False):
    """全部语料块 → meta.doc_embedding（ON CONFLICT 更新，幂等）。返回块列表。"""
    from ask_sys.common.db import psql, q
    import numpy as np

    chunks = knowledge.chunks()
    try:
        n = q("SELECT count(*) FROM meta.doc_embedding")
    except RuntimeError:
        n = [("0",)]      # 首次构建：表还没建
    if n and int(n[0][0]) == len(chunks) and not force:
        return chunks

    model = _model()
    vecs = list(model.passage_embed([_doc_text(c) for c in chunks]))
    rows = []
    for c, v in zip(chunks, vecs):
        v = np.asarray(v, dtype=np.float32)
        v = v / float(np.linalg.norm(v))
        doc_id = f"{c['source']}#{c['heading']}"
        lit = "[" + ",".join(f"{x:.7f}" for x in v) + "]"
        rows.append((doc_id, c["source"], c["heading"], c["text"], lit))

    # doc_id 去重：同一标题下多个分块时追加序号（README 标题重复是常态）
    seen_ids = {}
    dedup = []
    for doc_id, src, head, text, lit in rows:
        seen_ids[doc_id] = seen_ids.get(doc_id, 0) + 1
        if seen_ids[doc_id] > 1:
            doc_id = f"{doc_id}#{seen_ids[doc_id]}"
        dedup.append((doc_id, src, head, text, lit))
    rows = dedup

    script = ["CREATE EXTENSION IF NOT EXISTS vector;",
              "BEGIN;",
              "DROP TABLE IF EXISTS meta.doc_embedding;",
              "CREATE TABLE meta.doc_embedding (",
              "  doc_id TEXT PRIMARY KEY, source TEXT, heading TEXT, content TEXT,",
              "  embedding vector(512) NOT NULL, model TEXT NOT NULL,",
              "  updated_at TIMESTAMPTZ NOT NULL DEFAULT now());",
              "CREATE INDEX idx_doc_emb ON meta.doc_embedding USING hnsw (embedding vector_cosine_ops);"]
    batch = 40
    for i in range(0, len(rows), batch):
        script.append("INSERT INTO meta.doc_embedding (doc_id, source, heading, content, embedding, model) VALUES")
        vals = []
        for doc_id, src, head, text, lit in rows[i:i + batch]:
            safe = text.replace("'", "''")
            vals.append(f"('{doc_id.replace(chr(39), chr(39) * 2)}','{src.replace(chr(39), chr(39) * 2)}',"
                        f"'{head.replace(chr(39), chr(39) * 2)}','{safe[:1800]}','{lit}','BAAI/bge-small-zh-v1.5')")
        script.append(",".join(vals) + ";")
    script.append("COMMIT;")
    psql("SELECT 1")  # 连通性
    _exec_script("\n".join(script))
    return chunks


def _exec_script(script):
    import subprocess
    from ask_sys.common.env import require
    r = subprocess.run(
        ["docker", "exec", "-i", "-e", f"PGPASSWORD={require('PG_PASSWORD')}",
         "pg16", "psql", "-h", "127.0.0.1", "-U", "dw", "-d", "dw",
         "-q", "-v", "ON_ERROR_STOP=1"],
        input=script, capture_output=True, text=True, encoding="utf-8", timeout=120)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "psql 脚本失败")


def vector_search_factory(model):
    import numpy as np
    from ask_sys.common.db import q

    def search(question, top_k=8):
        v = list(model.query_embed([question]))[0]
        v = np.asarray(v, dtype=np.float32)
        v = v / float(np.linalg.norm(v))
        lit = "[" + ",".join(f"{x:.7f}" for x in v) + "]"
        rows = q(f"""SELECT source, heading, 1 - (embedding <=> '{lit}') AS score
                     FROM meta.doc_embedding ORDER BY embedding <=> '{lit}' LIMIT {top_k * 2}""")
        picked, per = [], {}
        for src, head, score in rows:
            if per.get(src, 0) >= knowledge.MAX_PER_SOURCE:
                continue
            per[src] = per.get(src, 0) + 1
            picked.append({"source": src, "heading": head, "text": "", "score": float(score)})
            if len(picked) >= top_k:
                break
        return picked
    return search


def hybrid_search_factory(model):
    """向量 + 词法 RRF（k=60），指标块强制保留沿用词法版规则。"""
    import numpy as np
    from ask_sys.common.db import q

    def search(question, top_k=8):
        lex = knowledge.search(question, top_k=12)
        v = list(model.query_embed([question]))[0]
        v = np.asarray(v, dtype=np.float32)
        v = v / float(np.linalg.norm(v))
        lit = "[" + ",".join(f"{x:.7f}" for x in v) + "]"
        rows = q(f"""SELECT source, heading, 1 - (embedding <=> '{lit}') AS score
                     FROM meta.doc_embedding ORDER BY embedding <=> '{lit}' LIMIT {top_k * 3}""")

        def key(c):
            return (c["source"], c["heading"])

        scores = {}
        for rank, c in enumerate(lex, 1):
            scores[key(c)] = scores.get(key(c), 0.0) + 1.0 / (60 + rank)
        for rank, (src, head, _s) in enumerate(rows, 1):
            scores[(src, head)] = scores.get((src, head), 0.0) + 1.0 / (60 + rank)
        merged = sorted(scores.items(), key=lambda x: -x[1])

        picked, per_src, seen = [], {}, set()
        # 指标块强制保留：问题命中指标 → 对应口径块无条件进 top
        try:
            mhits, *_ = __import__("ask_sys.common.nl2sql", fromlist=["recall"]).recall(question)
        except Exception:
            mhits = []
        ids = {m["id"] for m in mhits}
        text_map = {key(c): c["text"] for c in knowledge.chunks()}
        for (src, head), score in merged:
            if "指标口径" in head and any(f"（{mid}）" in head for mid in ids):
                picked.append({"source": src, "heading": head, "text": text_map.get((src, head), ""), "score": score})
                per_src[src] = per_src.get(src, 0) + 1
                seen.add((src, head))
        for (src, head), score in merged:
            if len(picked) >= top_k:
                break
            if (src, head) in seen or per_src.get(src, 0) >= knowledge.MAX_PER_SOURCE:
                continue
            per_src[src] = per_src.get(src, 0) + 1
            seen.add((src, head))
            picked.append({"source": src, "heading": head, "text": text_map.get((src, head), ""), "score": score})
        return picked[:top_k] if len(picked) >= top_k else picked
    return search


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["lexical", "vector", "hybrid", "all"], default="all")
    ap.add_argument("--build", action="store_true", help="强制重建向量索引")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    load_env()
    require("PG_PASSWORD")

    modes = []
    if args.mode in ("lexical", "all"):
        modes.append(("lexical", lexical_search))
    if args.mode in ("vector", "hybrid", "all"):
        t0 = time.perf_counter()
        chunks = build_vector_store(force=args.build)
        print(f"[OK] 向量索引 {len(chunks)} 块（{round(time.perf_counter() - t0, 1)}s）")
        model = _model()
        if args.mode in ("vector", "all"):
            modes.append(("vector", vector_search_factory(model)))
        if args.mode in ("hybrid", "all"):
            modes.append(("hybrid", hybrid_search_factory(model)))

    summary = {}
    for name, fn in modes:
        recall, mrr, details = evaluate(fn)
        summary[name] = {"recall@8": round(recall, 4), "mrr": round(mrr, 4)}
        print(f"\n==== {name} ====  Recall@8={recall:.2%}  MRR={mrr:.4f}")
        for question, rank, ok, note in details:
            print(f"[{'PASS' if ok else 'FAIL'}] {question}  ({rank}; {note})")

    if len(summary) > 1:
        print("\n==== 对照 ====")
        for name, m in summary.items():
            print(f"  {name:8s} Recall@8={m['recall@8']:.2%}  MRR={m['mrr']:.4f}")
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
