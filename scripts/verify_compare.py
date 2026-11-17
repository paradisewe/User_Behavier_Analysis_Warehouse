#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
跨管道对账：Hive ADS 结果 vs 基准

两组基准，两种容差：
  A. DuckDB 全量基准  <BASELINE_DIR>/0X_*.csv        —— 同口径，可精确对账（tol 极小）
  B. 上游抽样基准     <REPORTS_DIR>/rfm_segments.csv  —— 5% 抽样，只比占比/比率/均值（tol 放宽）

输入：
  verify_out/01.tsv ... 13.tsv     ← scripts/verify.sh 导出
  <BASELINE_DIR>                   ← DuckDB 全量（默认同级仓库 sql/results）
  <REPORTS_DIR>                    ← 上游抽样（默认同级仓库 python/reports）

用法：
  python scripts/verify_compare.py
  BASELINE_DIR=... REPORTS_DIR=... python scripts/verify_compare.py

规则：
  01/02/03/06/07/08：计数 |diff| <= 50，比率 |diff| <= 0.001（双重去重口径差仅 49 行）
  09/11/12        ：与抽样基线比占比(tol 0.01) / 比率(tol 0.02) / 均值(tol 0.5)，不比计数
  10              ：无基线，校验表内恒等（next_day_rate == retained/cohort）
  13              ：校验 support/confidence/lift 恒等式 + 阈值 + 购物篮倍数（≈抽样 20 倍）
  04/05 不比：Hive 北京时间 vs DuckDB UTC，日/小时粒度必然错位（已声明口径差异）

退出码：0 全过 / 1 有失败
"""

import csv
import os
import sys
from pathlib import Path

# ---------- 配置区 ----------
REPO_DIR = Path(__file__).resolve().parent.parent
HIVE_DIR = REPO_DIR / "verify_out"
BASELINE_DIR = Path(os.environ.get(
    "BASELINE_DIR",
    REPO_DIR.parent / "User_behavier_analysis" / "sql" / "results",
))
REPORTS_DIR = Path(os.environ.get(
    "REPORTS_DIR",
    REPO_DIR.parent / "User_behavier_analysis" / "python" / "reports",
))

TOL = {
    "count": 50,        # 计数：去重口径 49 行的传导
    "ratio": 0.001,     # 同口径比率：ADS 统一 round(...,4)
    "share": 0.01,      # 抽样 vs 全量：占比
    "rate": 0.02,       # 抽样 vs 全量：比率
    "mean": 0.50,       # 抽样 vs 全量：均值
}

failed = 0
total = 0


# ---------- 读数据 ----------
def load_hive(fname):
    """读 beeline TSV，返回 [[列1, 列2, ...], ...]；文件缺失记一次失败"""
    global failed, total
    path = HIVE_DIR / fname
    if not path.exists():
        total += 1
        failed += 1
        print(f"[FAIL] 缺少 {path}，请先执行 bash scripts/verify.sh")
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(line.split("\t"))
    if not rows:
        total += 1
        failed += 1
        print(f"[FAIL] {fname} 为空（导出失败或表无数据）")
    return rows


def load_csv(directory, fname):
    """读基准 CSV；utf-8-sig 吃掉 BOM；缺失只提示一次"""
    path = directory / fname
    if not path.exists():
        print(f"[WARN] 缺少基准 {path}（跳过该组）")
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def to_num(v):
    """'NULL' / '\\N' / '' / 'nan' → None"""
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.upper() in ("NULL", "\\N", "NAN", "NONE"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ---------- 比对与打印 ----------
def record(name, hive, base, kind, tol=None):
    """单指标比对；kind ∈ TOL 的键；tol 可显式覆盖"""
    global failed, total
    total += 1
    h, b = to_num(hive), to_num(base)
    if h is None or b is None:
        failed += 1
        print(f"[FAIL] {name:<34} 无法解析：基线={base!r}  Hive={hive!r}")
        return
    diff = abs(h - b)
    limit = TOL[kind] if tol is None else tol
    ok = diff <= limit
    if not ok:
        failed += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name:<34} 基线={b:>13g}  Hive={h:>13g}  diff={diff:.6f}")


# ---------- A 组：与 DuckDB 全量基准精确对账 ----------
def check_01():  # 行为分布：基线 pct 是百分数（89.5809）→ /100
    base = {r["behavior_type"]: r for r in load_csv(BASELINE_DIR, "01_behavior_dist.csv")}
    hive = {r[0]: r for r in load_hive("01.tsv")}
    if not base or not hive:
        return
    for bt in ("pv", "fav", "cart", "buy"):
        b, h = base.get(bt), hive.get(bt)
        record(f"01 {bt} 次数", h[1] if h else None, b["cnt"] if b else None, "count")
        record(f"01 {bt} 占比", h[2] if h else None,
               float(b["pct"]) / 100 if b else None, "ratio")


def check_02():  # 用户级漏斗：基线 conversion 已是小数
    base = {r["stage"]: r for r in load_csv(BASELINE_DIR, "02_user_funnel.csv")}
    hive = {r[0]: r for r in load_hive("02.tsv")}
    if not base or not hive:
        return
    for st in ("pv", "fav", "cart", "buy"):
        b, h = base.get(st), hive.get(st)
        record(f"02 {st} 用户数", h[1] if h else None, b["users"] if b else None, "count")
        record(f"02 {st} 转化率", h[2] if h else None, b["conversion"] if b else None, "ratio")


def check_03():  # 顺序漏斗：基线宽表一行，Hive 长表 4 行
    rows = load_csv(BASELINE_DIR, "03_item_funnel.csv")
    hive = {r[0]: r for r in load_hive("03.tsv")}
    if not rows or not hive:
        return
    b = rows[0]
    for tr, cnt_col, rate_col in (
        ("pv_to_cart", "pv_to_cart_pairs", "pv_to_cart_rate"),
        ("pv_to_buy", "pv_to_buy_pairs", "pv_to_buy_rate"),
        ("cart_to_buy", "cart_to_buy_pairs", "cart_to_buy_rate"),
    ):
        h = hive.get(tr)
        record(f"03 {tr} 对数", h[1] if h else None, b[cnt_col], "count")
        record(f"03 {tr} 转化率", h[2] if h else None, b[rate_col], "ratio")


def check_06():  # 类目 TOP10
    base = load_csv(BASELINE_DIR, "06_category_top.csv")
    hive = {r[1]: r for r in load_hive("06.tsv")}   # 第 0 列 rn，第 1 列 category_id
    if not base or not hive:
        return
    for br in base:
        cid = br["category_id"]
        h = hive.get(cid)
        record(f"06 {cid} buy", h[2] if h else None, br["buy_count"], "count")
        record(f"06 {cid} buyer", h[3] if h else None, br["buyer_count"], "count")


def check_07():  # 复购单行快照
    base = load_csv(BASELINE_DIR, "07_repurchase.csv")
    hive = load_hive("07.tsv")
    if not base or not hive:
        return
    b, h = base[0], hive[0]
    record("07 购买用户数", h[0], b["total_buyers"], "count")
    record("07 复购用户数", h[1], b["repurchase_users"], "count")
    record("07 复购率", h[2], b["repurchase_rate"], "ratio")


def check_08():  # 行为深度分层：基线 pct 是百分数（68.06）→ /100
    base = {r["behavior_depth"]: r for r in load_csv(BASELINE_DIR, "08_user_segmentation.csv")}
    hive = {r[0]: r for r in load_hive("08.tsv")}
    if not base or not hive:
        return
    for seg in ("已购买", "加购未购买", "仅收藏", "仅浏览"):
        b, h = base.get(seg), hive.get(seg)
        record(f"08 {seg} 人数", h[1] if h else None, b["users"] if b else None, "count")
        record(f"08 {seg} 占比", h[2] if h else None,
               float(b["pct"]) / 100 if b else None, "ratio")


# ---------- B 组：与上游 5% 抽样基准比结构（不比计数） ----------
def check_09():  # RFM 分层：占比 / 人均购买次数 / 平均R天数
    base = {r["segment"]: r for r in load_csv(REPORTS_DIR, "rfm_segments.csv")}
    hive = {r[0]: r for r in load_hive("09.tsv")}   # segment, users, avg_f, avg_r, share
    if not base or not hive:
        return

    for seg in ("重要价值客户", "重要发展客户", "重要保持客户", "重要挽留客户",
                "一般价值客户", "一般挽留客户", "未购买用户"):
        b, h = base.get(seg), hive.get(seg)
        if not b or not h:
            # 缺分段要显式失败，不能静默跳过
            record(f"09 {seg} 占比", None, b["user_share"] if b else None, "share")
            continue

        record(f"09 {seg} 占比", h[4], b["user_share"], "share")

        # 口径差异说明：
        #   上游把未购买用户的 F 记为 NaN（mean 为空）、R 也为空；
        #   本项目 ADS 把未购买用户的 frequency 存为 0、recency_days 存为 NULL。
        #   因此该分段无法与基线比均值，退化为「购买次数必为 0」的自证。
        if seg == "未购买用户":
            record("09 未购买用户 人均购买次数(=0)", h[2], 0, "mean", tol=0)
        elif to_num(b["avg_recency_days"]) is not None:
            record(f"09 {seg} 人均购买次数", h[2], b["avg_frequency"], "mean")
            record(f"09 {seg} 平均R天数", h[3], b["avg_recency_days"], "mean")

def check_10():  # 逐日留存：无基线，验表内恒等
    rows = load_hive("10.tsv")   # stat_date, cohort, retained, rate
    for r in rows:
        cohort, retained, rate = to_num(r[1]), to_num(r[2]), to_num(r[3])
        if cohort in (None, 0) or retained is None or rate is None:
            record(f"10 {r[0]} 留存率恒等", None, None, "ratio")
            continue
        record(f"10 {r[0]} 留存率恒等", f"{rate:.4f}", f"{retained / cohort:.4f}", "ratio")


def check_11():  # 分层留存：占比/留存率/人均可留存天数
    base_rows = load_csv(REPORTS_DIR, "retention_summary.csv")
    hive = {r[0]: r for r in load_hive("11.tsv")}   # layer, users, eligible, retained, rate, avg_days
    if not base_rows or not hive:
        return
    base = {r["layer"]: r for r in base_rows}
    base_total = sum(to_num(r["users"]) or 0 for r in base.values())
    hive_total = sum(to_num(r[1]) or 0 for r in hive.values())
    for layer in ("1天", "2~3天", "4~5天", "6~9天"):
        b, h = base.get(layer), hive.get(layer)
        if not b or not h:
            record(f"11 {layer} 用户占比", None, None, "share")
            continue
        record(f"11 {layer} 用户占比",
               (to_num(h[1]) or 0) / hive_total if hive_total else None,
               (to_num(b["users"]) or 0) / base_total if base_total else None, "share")
        record(f"11 {layer} 次日留存率", h[4], b["next_day_retention"], "rate")
        record(f"11 {layer} 人均可留存天数", h[5], b["avg_eligible_days"], "mean")


def check_12():  # 用户路径：占比与购买率
    base = {r["path"]: r for r in load_csv(REPORTS_DIR, "path_summary.csv")}
    hive = {r[0]: r for r in load_hive("12.tsv")}   # path, pairs, purchased, share, rate
    if not base or not hive:
        return
    for path in ("仅浏览", "直接购买", "先加购", "先收藏"):
        b, h = base.get(path), hive.get(path)
        if not b or not h:
            record(f"12 {path} 占比", None, None, "share")
            continue
        record(f"12 {path} 占比", h[3], b["share_of_browsed"], "share")
        record(f"12 {path} 购买率", h[4], b["purchase_rate"], "rate")


def check_13():  # 关联规则：恒等式 + 阈值 + 购物篮倍数
    rows = load_hive("13.tsv")
    if not rows:
        return
    record("13 规则条数(TopN)", len(rows), 100, "count", tol=0)

    max_sup_err = max_conf_err = max_lift_err = 0.0
    violations = 0
    sort_violations = 0
    prev_lift = None
    total_baskets = None
    for r in rows:
        sup, conf, lift = to_num(r[3]), to_num(r[4]), to_num(r[5])
        both, ante_b, cons_b, nb = to_num(r[6]), to_num(r[7]), to_num(r[8]), to_num(r[9])
        if None in (sup, conf, lift, both, ante_b, cons_b, nb) or ante_b == 0 or nb == 0 or cons_b == 0:
            violations += 1
            continue
        total_baskets = nb
        max_sup_err = max(max_sup_err, abs(sup - both / nb))
        max_conf_err = max(max_conf_err, abs(conf - both / ante_b))
        max_lift_err = max(max_lift_err, abs(lift - (both / ante_b) / (cons_b / nb)))
        if sup < 0.0005 or conf < 0.10 or lift <= 1.0:
            violations += 1
        if prev_lift is not None and lift > prev_lift + 1e-9:
            sort_violations += 1
        prev_lift = lift

    record("13 support 恒等式最大误差", f"{max_sup_err:.6f}", 0.0, "ratio")
    record("13 confidence 恒等式最大误差", f"{max_conf_err:.6f}", 0.0, "ratio")
    record("13 lift 恒等式最大误差", f"{max_lift_err:.6f}", 0.0, "ratio", tol=0.02)
    record("13 阈值违例条数", violations, 0, "count", tol=0)
    record("13 lift 降序违例条数", sort_violations, 0, "count", tol=0)

    base_rows = load_csv(REPORTS_DIR, "association_rules.csv")
    if base_rows and total_baskets:
        base_baskets = to_num(base_rows[0]["total_baskets"])
        if base_baskets:
            record("13 购物篮倍数(全量/抽样)", total_baskets / base_baskets, 20.0, "count", tol=10)


# ---------- 主流程 ----------
def main():
    print(f"Hive 导出目录：{HIVE_DIR}")
    print(f"DuckDB 基准 ：{BASELINE_DIR}")
    print(f"抽样基准    ：{REPORTS_DIR}\n")

    print("-- A 组：DuckDB 全量基准（精确对账） --")
    check_01()
    check_02()
    check_03()
    check_06()
    check_07()
    check_08()

    print("\n-- B 组：上游抽样基准（比结构，不比计数） --")
    check_09()
    check_10()
    check_11()
    check_12()
    check_13()

    print(f"\n==== 共 {total} 项，失败 {failed} 项 ====")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
