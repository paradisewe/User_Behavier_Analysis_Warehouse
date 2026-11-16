#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
跨管道对账：Hive ADS 结果 vs DuckDB 全量基准

输入：
  verify_out/01.tsv 02.tsv 03.tsv 06.tsv 07.tsv 08.tsv   ← scripts/verify.sh 导出
  <BASELINE_DIR>/0X_*.csv                                 ← DuckDB 基准（默认取同级仓库）

用法：
  python scripts/verify_compare.py
  BASELINE_DIR=/path/to/results python scripts/verify_compare.py

规则：
  计数类 |diff| <= 50；比率类 |diff| <= 0.001
  04/05 不比：Hive 北京时间 vs DuckDB UTC，日/小时粒度必然错位（已声明口径差异）

退出码：0 全过 / 1 有失败
"""

import os
from pathlib import Path
import csv
import sys

# ---------- 配置区 ----------
REPO_DIR = Path(__file__).resolve().parent.parent
HIVE_DIR = REPO_DIR / "verify_out"
BASELINE_DIR = Path(os.environ.get(
    "BASELINE_DIR",
    REPO_DIR.parent / "User_behavier_analysis" / "sql" / "results",
))

COUNT_TOL = 50        # 计数容差：去重 49 行的传导
RATIO_TOL = 0.001     # 比率容差：ADS 统一 round(...,4)

failed = 0
total = 0

# ---------- 读数据 ----------
def load_hive(fname):
    """读 beeline TSV，返回 [[列1, 列2, ...], ...]；文件不存在返回空列表"""
    path = HIVE_DIR / fname
    if not path.exists():
        print(f"[FAIL] 缺少 {path}，请先执行 bash scripts/verify.sh")
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(line.split("\t"))
    return rows


def load_base(fname):
    """读 DuckDB 基准 CSV；utf-8-sig 用于吃掉 BOM"""
    with (BASELINE_DIR / fname).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# ---------- 比对与打印 ----------
def record(name, hive, base, kind):
    """单指标比对：kind = count | ratio"""
    global failed, total
    total += 1
    try:
        h, b = float(hive), float(base)
    except (TypeError, ValueError):
        failed += 1
        print(f"[FAIL] {name:<32} 无法解析：基线={base!r}  Hive={hive!r}")
        return
    diff = abs(h - b)
    tol = COUNT_TOL if kind == "count" else RATIO_TOL
    ok = diff <= tol
    if not ok:
        failed += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name:<32} 基线={b:>13g}  Hive={h:>13g}  diff={diff:.6f}")


# ---------- 各表对账 ----------
def check_01():  # 行为分布：基线 pct 是百分数（89.5809）→ /100
    base = {r["behavior_type"]: r for r in load_base("01_behavior_dist.csv")}
    hive = {r[0]: r for r in load_hive("01.tsv")}
    for bt in ("pv", "fav", "cart", "buy"):
        b, h = base.get(bt), hive.get(bt)
        record(f"01 {bt} 次数", h[1] if h else None, b["cnt"] if b else None, "count")
        record(f"01 {bt} 占比", h[2] if h else None,
               float(b["pct"]) / 100 if b else None, "ratio")


def check_02():  # 用户级漏斗：基线 conversion 已是小数
    base = {r["stage"]: r for r in load_base("02_user_funnel.csv")}
    hive = {r[0]: r for r in load_hive("02.tsv")}
    for st in ("pv", "fav", "cart", "buy"):
        b, h = base.get(st), hive.get(st)
        record(f"02 {st} 用户数", h[1] if h else None, b["users"] if b else None, "count")
        record(f"02 {st} 转化率", h[2] if h else None, b["conversion"] if b else None, "ratio")


def check_03():  # 顺序漏斗：基线是宽表一行，Hive 是长表 4 行 → 需转置对齐
    rows = load_base("03_item_funnel.csv")
    if not rows:
        return
    b = rows[0]
    hive = {r[0]: r for r in load_hive("03.tsv")}
    pairs = [
        ("pv_to_cart", "pv_to_cart_pairs", "pv_to_cart_rate"),
        ("pv_to_buy", "pv_to_buy_pairs", "pv_to_buy_rate"),
        ("cart_to_buy", "cart_to_buy_pairs", "cart_to_buy_rate"),
    ]
    for tr, cnt_col, rate_col in pairs:
        h = hive.get(tr)
        record(f"03 {tr} 对数", h[1] if h else None, b[cnt_col], "count")
        record(f"03 {tr} 转化率", h[2] if h else None, b[rate_col], "ratio")


def check_06():  # 类目 TOP10：按 category_id 对齐，买卖两个口径都验
    base = load_base("06_category_top.csv")
    hive = {r[1]: r for r in load_hive("06.tsv")}   # 第 0 列是 rn，第 1 列是 category_id
    for br in base:
        cid = br["category_id"]
        h = hive.get(cid)
        record(f"06 {cid} buy", h[2] if h else None, br["buy_count"], "count")
        record(f"06 {cid} buyer", h[3] if h else None, br["buyer_count"], "count")


def check_07():  # 复购：单行快照
    base = load_base("07_repurchase.csv")
    hive = load_hive("07.tsv")
    if not base or not hive:
        return
    b, h = base[0], hive[0]
    record("07 购买用户数", h[0], b["total_buyers"], "count")
    record("07 复购用户数", h[1], b["repurchase_users"], "count")
    record("07 复购率", h[2], b["repurchase_rate"], "ratio")


def check_08():  # 分层：基线 pct 是百分数（68.06）→ /100
    base = {r["behavior_depth"]: r for r in load_base("08_user_segmentation.csv")}
    hive = {r[0]: r for r in load_hive("08.tsv")}
    for seg in ("已购买", "加购未购买", "仅收藏", "仅浏览"):
        b, h = base.get(seg), hive.get(seg)
        record(f"08 {seg} 人数", h[1] if h else None, b["users"] if b else None, "count")
        record(f"08 {seg} 占比", h[2] if h else None,
               float(b["pct"]) / 100 if b else None, "ratio")


def main():
    print(f"Hive 导出目录：{HIVE_DIR}")
    print(f"基准目录    ：{BASELINE_DIR}\n")
    check_01()
    check_02()
    check_03()
    check_06()
    check_07()
    check_08()
    print(f"\n==== 共 {total} 项，失败 {failed} 项 ====")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()