#!/usr/bin/env python3
"""路径与配置：repo root 与 .env 只在这里计算/加载一次。"""
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def load_env():
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def require(name):
    """取必需配置；缺失时给出明确错误，而不是 KeyError。"""
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"缺少 {name}（检查 {REPO / '.env'}）")
    return value


load_env()
