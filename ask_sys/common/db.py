#!/usr/bin/env python3
"""PostgreSQL 访问：统一走 docker exec psql；默认只读账号 dw_ro。"""
import subprocess

from .env import require

PG_CONTAINER = "pg16"
PG_DB = "dw"
PG_HOST = "127.0.0.1"
SEP = "\x1f"          # 字段分隔符：比 | 更不可能出现在中文注释里


def psql(sql, *, user="dw_ro", timeout=40, extra=()):
    """执行 SQL 返回原始 stdout；失败抛 RuntimeError（绝不吞错）。"""
    password = require("PG_RO_PASSWORD" if user == "dw_ro" else "PG_PASSWORD")
    cmd = ["docker", "exec", "-e", f"PGPASSWORD={password}",
           PG_CONTAINER, "psql", "-h", PG_HOST, "-U", user, "-d", PG_DB,
           *extra, "-c", sql]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"执行失败：{r.stderr.strip()}")
    return r.stdout


def q(sql):
    """查询并返回 [[字段...], ...]（无表头、无脚注）。"""
    out = psql(sql, extra=("-t", "-A", "-F", SEP))
    return [line.split(SEP) for line in out.strip().splitlines() if line]
