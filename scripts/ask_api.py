#!/usr/bin/env python3
"""HTTP 入口（薄壳）：应用主体在 ask_sys，这里只做 Flask 路由与页面服务。

    GET  /         聊天界面（web/index.html，阶段二再搬进 ask_sys）
    GET  /health   引擎探活（网页状态灯）
    POST /ask      {"question": "...", "engine": "pipeline|graph"} -> 四分支统一响应
    POST /ask/stream {"question": "..."} -> SSE（仅 graph，事件：node/tool/tool_end/result）

启动：
    /d/pythonS/anaconda/python.exe scripts/ask_api.py
    浏览器打开 http://127.0.0.1:5001
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flask import Flask, Response, jsonify, request, stream_with_context  # noqa: E402

from ask_sys.common import env, nl2sql  # noqa: E402
from ask_sys.ver_pipeline.router import answer_question  # noqa: E402

INDEX = env.REPO / "web" / "index.html"
ENGINES = ("pipeline", "graph")

app = Flask(__name__)


def _graph():
    """延迟导入：默认引擎不加载 langgraph 依赖，也不拖慢启动。"""
    from ask_sys.ver_langgraph.graph import GRAPH
    from ask_sys.ver_langgraph.state import RECURSION_LIMIT
    return GRAPH, RECURSION_LIMIT


def _sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/")
def index():
    if not INDEX.exists():
        return Response("缺少 web/index.html", status=500, mimetype="text/plain")
    return Response(INDEX.read_text(encoding="utf-8"), mimetype="text/html")


@app.get("/health")
def health():
    """状态灯用：能连上镜像库就算在线；失败也返回 200，由 ok 字段表达。"""
    try:
        nl2sql.run("SELECT 1", compact=True)
        return jsonify({"ok": True, "db": "pg16", "window": "2017-11-25 ~ 2017-12-03",
                        "engine": "pipeline"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/ask")
def ask():
    body = request.get_json(silent=True) or {}
    question = (body.get("question") or "").strip()
    if not question:
        return jsonify({"branch": "error", "error": "question 不能为空"}), 400
    engine = (body.get("engine") or "pipeline").strip()
    if engine not in ENGINES:
        return jsonify({"branch": "error",
                        "error": f"未知引擎：{engine}（可选 {'|'.join(ENGINES)}）"}), 400

    if engine == "graph":
        from ask_sys.ver_langgraph import answer as graph_answer
        result = graph_answer(question)
    else:
        result = answer_question(question)
    status = 500 if result.get("branch") == "error" else 200
    return jsonify(result), status


@app.post("/ask/stream")
def ask_stream():
    """SSE 进度流：固定 graph 引擎，只做单次问答，不做断线重连。"""
    body = request.get_json(silent=True) or {}
    question = (body.get("question") or "").strip()
    if not question:
        return jsonify({"branch": "error", "error": "question 不能为空"}), 400

    graph, recursion_limit = _graph()

    def generate():
        started = time.perf_counter()
        result = None
        try:
            for chunk in graph.stream(
                    {"question": question, "started_at": started},
                    config={"recursion_limit": recursion_limit},
                    stream_mode="updates"):
                for node, update in (chunk or {}).items():
                    yield _sse("node", {"name": node})
                    update = update or {}
                    if node == "agent":
                        # 模型这一轮想调什么工具，先告诉前端（还没执行）
                        for call in _tool_calls(update):
                            yield _sse("tool", {"name": call.get("name"),
                                                "args": call.get("args") or {}})
                    elif node == "tools":
                        for ev in update.get("evidence") or []:
                            yield _sse("tool_end", {"name": ev.get("tool"),
                                                    "rows": ev.get("row_count") or 0,
                                                    "error": ev.get("error")})
                    elif node == "finalize":
                        result = update.get("result")
        except Exception as e:
            yield _sse("error", {"error": str(e)})
            return

        if result is None:      # 没走到 finalize（图级异常），仍给一个可解析的收尾
            result = {"question": question, "branch": "error",
                      "error": "图未产出结果",
                      "elapsed": round(time.perf_counter() - started, 2),
                      "tokens": {"prompt_tokens": 0, "completion_tokens": 0},
                      "trace": []}
        yield _sse("result", result)

    return Response(stream_with_context(generate()),
                    mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


def _tool_calls(update):
    """从 agent 节点返回的 AIMessage 里取出本轮 tool_calls。"""
    for msg in update.get("messages") or []:
        calls = getattr(msg, "tool_calls", None)
        if calls:
            return calls
    return []


if __name__ == "__main__":
    print("问数界面：http://127.0.0.1:5001")
    app.run(host="127.0.0.1", port=5001, threaded=True)
