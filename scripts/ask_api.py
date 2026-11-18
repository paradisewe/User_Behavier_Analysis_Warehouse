#!/usr/bin/env python3
"""B3-lite：把 ask_sql 包成一个 HTTP 接口。

    GET  /        浏览器里直接问一句
    POST /ask     {"question": "..."} -> {"sql", "reason", "rows", "row_count"}

启动：
    /d/pythonS/anaconda/python.exe scripts/ask_api.py
    浏览器打开 http://127.0.0.1:5001
"""
import sys
from pathlib import Path

from flask import Flask, Response, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ask_sql  # noqa: E402

app = Flask(__name__)

INDEX = """<!doctype html>
<meta charset="utf-8">
<h3>问一句（淘宝用户行为数仓）</h3>
<input id="q" size="60" placeholder="例如：12-02 的 DAU 是多少">
<button onclick="ask()">问</button>
<pre id="out" style="white-space:pre-wrap"></pre>
<script>
async function ask() {
  const out = document.getElementById('out');
  out.textContent = '查询中…';
  const r = await fetch('/ask', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({question: document.getElementById('q').value})
  });
  out.textContent = JSON.stringify(await r.json(), null, 2);
}
</script>"""


@app.get("/")
def index():
    return Response(INDEX, mimetype="text/html")


@app.post("/ask")
def ask():
    body = request.get_json(silent=True) or {}
    question = (body.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question 不能为空"}), 400
    try:
        sql, reason, text = ask_sql.ask(question, compact=True)
    except ask_sql.Refusal as e:
        return jsonify({"question": question, "sql": None, "reason": str(e),
                        "rows": [], "row_count": 0})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    rows = [line.split("|") for line in text.splitlines()] if text else []
    return jsonify({"question": question, "sql": sql, "reason": reason,
                    "rows": rows, "row_count": len(rows)})


if __name__ == "__main__":
    ask_sql.load_env()
    app.run(host="127.0.0.1", port=5001)
