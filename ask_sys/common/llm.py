#!/usr/bin/env python3
"""LLM 调用（OpenAI 兼容协议）与 JSON 解析工具。"""
import json
import re

import requests

from .env import require


def call_llm(prompt, max_tokens=None, timeout=60):
    """返回 (content, usage)。配置缺失时抛 SystemExit，超时/HTTP 错误抛异常。"""
    base = require("LLM_BASE_URL").rstrip("/")
    payload = {"model": require("LLM_MODEL"), "temperature": 0,
               "messages": [{"role": "user", "content": prompt}]}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    r = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {require('LLM_API_KEY')}",
                 "Content-Type": "application/json"},
        json=payload, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"模型接口 {r.status_code}: {r.text[:300]}")
    data = r.json()
    return data["choices"][0]["message"]["content"], data.get("usage", {})


def json_of(text):
    """从回复里尽力取出 JSON 对象（兼容代码围栏/前后废话）。"""
    text = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None
