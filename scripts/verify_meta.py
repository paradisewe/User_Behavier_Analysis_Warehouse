#!/usr/bin/env python3
"""A2 验收：meta.* 与 public.dw_* 是否一致，指标口径是否自洽。

检查项：
  1. meta 四张表存在
  2. table_info = 19 张镜像表，注释/粒度/用途/主键非空，row_count 与实时计数一致
  3. column_info 与 information_schema 逐列对齐（类型/可空/主键/序号/注释非空率 100%）
  4. metric_info 字段完整，ADS 13 张表各有指标，value_column 在映射里恰好一次
  5. column_metric 无孤儿（指标/表/列都必须存在）
  6. dw_ro 对 meta 有 SELECT（B 阶段的检索账号）
  7. 契约禁止项：不出现 dw_dws_traffic_user_item_df

用法：/d/pythonS/anaconda/python.exe scripts/verify_meta.py
"""
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PG_CONTAINER = "pg16"
FORBIDDEN = "dw_dws_traffic_user_item_df"
EXPECTED_TABLES = 19
FAIL = 0


def ok(msg):
    print(f"[PASS] {msg}")


def bad(msg):
    global FAIL
    FAIL = 1
    print(f"[FAIL] {msg}")


def load_env():
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    if not os.environ.get("PG_PASSWORD"):
        sys.exit("缺少 PG_PASSWORD（.env）")


def psql(sql):
    r = subprocess.run(
        ["docker", "exec", "-e", f"PGPASSWORD={os.environ['PG_PASSWORD']}",
         PG_CONTAINER, "psql", "-U", "dw", "-d", "dw",
         "-t", "-A", "-F", "|", "-c", sql],
        capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return [line.split("|") for line in r.stdout.strip().splitlines() if line]


def main():
    load_env()

    # 1. 四张表
    have = {r[0] for r in psql("SELECT table_name FROM information_schema.tables "
                               "WHERE table_schema='meta'")}
    want = {"table_info", "column_info", "metric_info", "column_metric"}
    if want <= have:
        ok(f"meta 四张表齐全：{', '.join(sorted(want))}")
    else:
        bad(f"meta 缺表：{', '.join(sorted(want - have))}")
        sys.exit(1)

    # 2. table_info
    live = {r[0] for r in psql("SELECT table_name FROM information_schema.tables "
                               "WHERE table_schema='public' AND table_name LIKE 'dw%'")}
    rows = psql("SELECT table_name, layer, source_table, table_comment, grain, served_for, "
                "primary_key, row_count FROM meta.table_info")
    meta_tables = {r[0]: r for r in rows}
    if len(rows) != EXPECTED_TABLES:
        bad(f"table_info 行数 {len(rows)} ≠ {EXPECTED_TABLES}")
    if set(meta_tables) != live:
        bad(f"table_info 与 public 表集合不一致：多 {sorted(set(meta_tables) - live)} / 缺 {sorted(live - set(meta_tables))}")
    for name, r in meta_tables.items():
        empties = [k for k, v in zip(("layer", "source", "comment", "grain", "served_for", "pk"), r[1:7]) if not v]
        if empties:
            bad(f"{name} 空字段：{','.join(empties)}")
        if r[1] not in ("DWS", "ADS"):
            bad(f"{name} layer 非法：{r[1]}")
    ok(f"table_info {len(rows)} 张，字段非空")

    # 行数实时核对（7M 表一次 count 约 1 秒）
    mismatch = []
    for name, r in meta_tables.items():
        live_cnt = int(psql(f"SELECT count(*) FROM public.{name}")[0][0])
        if live_cnt != int(r[7]):
            mismatch.append(f"{name} meta={r[7]} 实际={live_cnt}")
    if mismatch:
        bad("row_count 过期：" + "；".join(mismatch))
    else:
        ok(f"table_info.row_count 与实时计数一致（{len(meta_tables)} 张）")

    # 3. column_info 逐列对齐
    meta_cols = {}
    for r in psql("SELECT table_name, column_name, ordinal, data_type, hive_type, comment, "
                  "is_nullable, is_primary_key FROM meta.column_info"):
        meta_cols[(r[0], r[1])] = r
    pg_cols = {}
    for r in psql("SELECT table_name, column_name, ordinal_position, data_type, is_nullable "
                  "FROM information_schema.columns WHERE table_schema='public' "
                  "AND table_name LIKE 'dw%' ORDER BY table_name, ordinal_position"):
        pg_cols[(r[0], r[1])] = r
    if set(meta_cols) != set(pg_cols):
        bad(f"column_info 列集合与 PG 不一致：多 {sorted(set(meta_cols) - set(pg_cols))[:3]} / "
            f"缺 {sorted(set(pg_cols) - set(meta_cols))[:3]}")
    else:
        ok(f"column_info 列集合与 PG 一致（{len(pg_cols)} 列）")

    diff, empty_comment = [], []
    for key, pg in pg_cols.items():
        m = meta_cols.get(key)
        if not m:
            continue
        if m[3] != pg[3] or (m[6] == "t") != (pg[4] == "YES") or int(m[2]) != int(pg[2]):
            diff.append(f"{key[0]}.{key[1]}")
        if not m[5].strip():
            empty_comment.append(f"{key[0]}.{key[1]}")
    if diff:
        bad(f"类型/可空/序号不一致：{', '.join(diff[:5])}")
    else:
        ok("column_info 类型/可空/序号与 information_schema 一致")
    if empty_comment:
        bad(f"中文注释为空：{', '.join(empty_comment[:5])}")
    else:
        ok("中文注释非空率 100%")

    # 主键标记
    pk_bad = []
    for name in meta_tables:
        pk_live = {r[0] for r in psql(f"""SELECT a.attname FROM pg_index i
                    JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey)
                    WHERE i.indrelid='public.{name}'::regclass AND i.indisprimary""")}
        pk_meta = {c for (t, c), r in meta_cols.items() if t == name and r[7] == "t"}
        if pk_live != pk_meta:
            pk_bad.append(name)
    if pk_bad:
        bad(f"主键标记不一致：{', '.join(pk_bad)}")
    else:
        ok("is_primary_key 与 pg_index 一致")

    # 4. metric_info
    metrics = psql("SELECT metric_id, metric_name, definition, formula, grain, source_table, "
                   "value_column, unit, time_window, caveat, aliases FROM meta.metric_info")
    if len(metrics) < 20:
        bad(f"指标数量 {len(metrics)} 偏少（期望 ≥20）")
    required = ("metric_name", "definition", "formula", "grain", "source_table",
                "value_column", "unit", "time_window", "caveat", "aliases")
    bad_fields = []
    for r in metrics:
        miss = [k for k, v in zip(required, r[1:]) if not v.strip()]
        if miss:
            bad_fields.append(f"{r[0]}({','.join(miss)})")
        if r[5] not in meta_tables:
            bad_fields.append(f"{r[0]}(source={r[5]} 不存在)")
    if bad_fields:
        bad(f"指标字段/来源异常：{', '.join(bad_fields[:5])}")
    else:
        ok(f"metric_info {len(metrics)} 条，字段完整、来源存在")

    ads = [t for t, r in meta_tables.items() if r[1] == "ADS"]
    covered = {r[5] for r in metrics}
    miss_ads = [t for t in ads if t not in covered]
    if miss_ads:
        bad(f"ADS 表无指标：{', '.join(miss_ads)}")
    else:
        ok(f"ADS {len(ads)} 张表各有至少 1 个指标")

    if FORBIDDEN in covered:
        bad(f"契约被改：指标引用了 {FORBIDDEN}")

    # 5. column_metric
    maps = psql("SELECT metric_id, table_name, column_name, role FROM meta.column_metric")
    mapping = {}
    orphan = []
    for mid, t, c, role in maps:
        if mid not in {r[0] for r in metrics}:
            orphan.append(f"{mid}->表不存在")
        elif (t, c) not in meta_cols:
            orphan.append(f"{mid}->{t}.{c}")
        mapping.setdefault(mid, []).append((t, c, role))
    if orphan:
        bad(f"column_metric 孤儿映射：{', '.join(orphan[:5])}")
    else:
        ok(f"column_metric {len(maps)} 条，无孤儿")

    value_bad = []
    for r in metrics:
        mid, src, vcol = r[0], r[5], r[6]
        vals = [c for t, c, role in mapping.get(mid, []) if role == "value"]
        if len(vals) != 1 or vals[0] != vcol:
            value_bad.append(f"{mid}(value={vals},期望 {vcol})")
    if value_bad:
        bad(f"value 映射不唯一/不匹配：{', '.join(value_bad[:5])}")
    else:
        ok("每个指标的 value 列映射唯一且与 value_column 一致")

    # 6. 只读账号对 meta 的权限
    priv = psql("SELECT has_table_privilege('dw_ro','meta.table_info','SELECT')")
    if priv and priv[0][0] == "t":
        ok("dw_ro 可读 meta.table_info")
    else:
        bad("dw_ro 对 meta 无 SELECT（重跑 scripts/init_roles_pg.sh）")

    print(f"\n==== 结果：{'全部通过' if FAIL == 0 else '有失败项'} ====")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
