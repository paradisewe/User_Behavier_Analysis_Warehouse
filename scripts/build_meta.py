#!/usr/bin/env python3
"""A2 元数据构建：Hive DDL 注释 + PG 实际结构 + 指标口径配置 → meta.* 四张表。

单一事实来源：
  - 表/列的中文注释与 Hive 原始类型：sql/DWS/dws_ddl.hql、sql/ADS/ads_ddl.hql
  - PG 实际类型/可空/主键/行数：information_schema + pg_index
  - 粒度与下游用途：config/tables.json（人工契约）
  - 指标口径：config/metrics.json（人工资产）

用法：/d/pythonS/anaconda/python.exe scripts/build_meta.py
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DDL_FILES = [REPO / "sql/DWS/dws_ddl.hql", REPO / "sql/ADS/ads_ddl.hql"]
TABLES_CFG = REPO / "config/tables.json"
METRICS_CFG = REPO / "config/metrics.json"
PG_CONTAINER = "pg16"
PG_DB_USER, PG_DB_NAME = "dw", "dw"
EXCLUDED_COLUMN = "etl_time"

CREATE_RE = re.compile(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*\(", re.I)
COL_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s+([A-Za-z]+(?:\s*\(\s*\d+(?:\s*,\s*\d+)?\s*\))?)\s+COMMENT\s+'([^']*)'")
PART_RE = re.compile(r"PARTITIONED\s+BY\s*\(([^)]*)\)", re.I)
COMMENT_RE = re.compile(r"COMMENT\s+'([^']*)'")
ERRORS = []


def fail(msg):
    ERRORS.append(msg)


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
         PG_CONTAINER, "psql", "-U", PG_DB_USER, "-d", PG_DB_NAME,
         "-t", "-A", "-F", "|", "-c", sql],
        capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return [line.split("|") for line in r.stdout.strip().splitlines() if line]


def psql_exec(sql):
    r = subprocess.run(
        ["docker", "exec", "-i", "-e", f"PGPASSWORD={os.environ['PG_PASSWORD']}",
         PG_CONTAINER, "psql", "-U", PG_DB_USER, "-d", PG_DB_NAME,
         "-q", "-v", "ON_ERROR_STOP=1"],
        input=sql, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())


def parse_ddl():
    """返回 {hive 全名: {"comment": ..., "columns": [{name,hive_type,comment,is_partition}]}}"""
    out = {}
    for path in DDL_FILES:
        lines = path.read_text(encoding="utf-8").splitlines()
        i = 0
        while i < len(lines):
            m = CREATE_RE.search(lines[i])
            if not m:
                i += 1
                continue
            full = f"{m.group(1)}.{m.group(2)}"
            body = []
            i += 1
            while i < len(lines) and not lines[i].lstrip().startswith(")"):
                body.append(lines[i])
                i += 1
            i += 1  # 跳过右括号
            tail = []
            while i < len(lines) and not lines[i].strip().upper().startswith("STORED") \
                    and not CREATE_RE.search(lines[i]):
                tail.append(lines[i])
                i += 1
            cols = []
            for line in body:
                cm = COL_RE.match(line)
                if cm:
                    cols.append({"name": cm.group(1).lower(),
                                 "hive_type": cm.group(2).upper(),
                                 "comment": cm.group(3),
                                 "is_partition": False})
            tail_text = "\n".join(tail)
            pm = PART_RE.search(tail_text)
            if pm:
                pcm = COL_RE.match(pm.group(1).strip())
                if pcm:
                    cols.append({"name": pcm.group(1).lower(),
                                 "hive_type": pcm.group(2).upper(),
                                 "comment": pcm.group(3),
                                 "is_partition": True})
            tcm = COMMENT_RE.search(tail_text)
            out[full] = {"comment": tcm.group(1) if tcm else "", "columns": cols}
    return out


def pg_columns(table):
    rows = psql(f"""SELECT column_name, data_type, is_nullable, ordinal_position
                    FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='{table}'
                    ORDER BY ordinal_position""")
    return [(r[0], r[1], r[2] == "YES", int(r[3])) for r in rows]


def pg_pk(table):
    rows = psql(f"""SELECT a.attname FROM pg_index i
                    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                    WHERE i.indrelid = 'public.{table}'::regclass AND i.indisprimary
                    ORDER BY a.attnum""")
    return [r[0] for r in rows]


def lit(value):
    return "'" + str(value).replace("'", "''") + "'"


def main():
    load_env()
    if not psql("SELECT 1 FROM information_schema.schemata WHERE schema_name='meta'"):
        sys.exit("[FAIL] meta schema 不存在：先跑 docker/pg/meta_schema.sql")

    tables_cfg = json.loads(TABLES_CFG.read_text(encoding="utf-8"))
    metrics = json.loads(METRICS_CFG.read_text(encoding="utf-8"))
    excluded_sources = set(tables_cfg.get("_excluded_sources", []))
    tables = {k: v for k, v in tables_cfg.items() if not k.startswith("_")}
    ddl = parse_ddl()

    used_sources = {v["source"] for v in tables.values()}
    for src in used_sources:
        if src not in ddl:
            fail(f"tables.json 引用的 Hive 表不在 DDL 里：{src}")
    for src in ddl:
        if src not in used_sources and src not in excluded_sources:
            fail(f"DDL 表未被契约覆盖（也没写进 _excluded_sources）：{src}")

    table_rows, column_rows = [], []
    columns_by_table = {}

    for dw_table, cfg in tables.items():
        src = cfg["source"]
        if src not in ddl:
            continue
        meta = ddl[src]
        ddl_cols = {c["name"]: c for c in meta["columns"] if c["name"] != EXCLUDED_COLUMN}
        synthetic = cfg.get("synthetic_columns", {})
        pg_cols = pg_columns(dw_table)
        pg_names = [c[0] for c in pg_cols]
        columns_by_table[dw_table] = pg_names

        if not pg_cols:
            fail(f"{dw_table} 在 PG 里不存在或没有列")
            continue
        missing = [c for c in ddl_cols if c not in pg_names]
        extra = [c for c in pg_names if c not in ddl_cols and c not in synthetic]
        if missing:
            fail(f"{dw_table} PG 缺列（Hive 有）：{', '.join(missing)}")
        if extra:
            fail(f"{dw_table} PG 多出未声明的列：{', '.join(extra)}")
        if not meta["comment"]:
            fail(f"{dw_table} 的 Hive 表注释为空（{src}）")

        pk = pg_pk(dw_table)
        row_count = int(psql(f"SELECT count(*) FROM public.{dw_table}")[0][0])
        table_rows.append((dw_table, cfg["layer"], src, meta["comment"],
                           cfg["grain"], cfg["served_for"], ",".join(pk), row_count))

        for name, data_type, nullable, ordinal in pg_cols:
            comment = ddl_cols[name]["comment"] if name in ddl_cols else synthetic.get(name, "")
            hive_type = ddl_cols[name]["hive_type"] if name in ddl_cols else "SYNTHETIC"
            if not comment:
                fail(f"{dw_table}.{name} 没有中文注释")
            column_rows.append((dw_table, name, ordinal, data_type, hive_type,
                                comment, nullable, name in pk))
        print(f"[OK] {dw_table}: {len(pg_cols)} 列 / PK={','.join(pk)} / {row_count} 行")

    # ---------- 指标校验 ----------
    seen = set()
    metric_rows, mapping_rows = [], []
    for m in metrics:
        mid = m["metric_id"]
        if mid in seen:
            fail(f"metric_id 重复：{mid}")
        seen.add(mid)
        for field in ("metric_name", "definition", "formula", "grain", "source_table",
                      "value_column", "unit", "time_window", "caveat", "aliases", "columns"):
            if not m.get(field):
                fail(f"指标 {mid} 缺字段 {field}")
        src = m["source_table"]
        if src not in tables:
            fail(f"指标 {mid} 的 source_table 不在契约里：{src}")
            continue
        cols = m["columns"]
        if m["value_column"] not in cols:
            fail(f"指标 {mid} 的 value_column 不在 columns 里")
        for c in cols:
            if c not in columns_by_table.get(src, []):
                fail(f"指标 {mid} 的列 {src}.{c} 不存在")
        metric_rows.append((mid, m["metric_name"], m["definition"], m["formula"],
                            m["grain"], src, m["value_column"], m["unit"],
                            m["time_window"], m["caveat"], "、".join(m["aliases"])))
        for c in cols:
            mapping_rows.append((mid, src, c, "value" if c == m["value_column"] else "dimension"))

    ads_tables = [t for t, c in tables.items() if c["layer"] == "ADS"]
    covered = {m["source_table"] for m in metrics}
    for t in ads_tables:
        if t not in covered:
            fail(f"ADS 表没有任何指标：{t}")

    if ERRORS:
        print("\n[FAIL] 契约校验未通过：")
        for e in ERRORS:
            print("  -", e)
        return 1

    # ---------- 写入 ----------
    def values(rows):
        return ",".join("(" + ",".join(r) + ")" for r in rows)

    def b(v):
        return "TRUE" if v else "FALSE"

    sql = ["BEGIN;",
           "TRUNCATE meta.column_metric, meta.metric_info, meta.column_info, meta.table_info;"]

    sql.append("INSERT INTO meta.table_info (table_name,layer,source_table,table_comment,"
               "grain,served_for,primary_key,row_count) VALUES " + values([
                   (lit(r[0]), lit(r[1]), lit(r[2]), lit(r[3]), lit(r[4]), lit(r[5]),
                    lit(r[6]), str(r[7])) for r in table_rows]) + ";")

    sql.append("INSERT INTO meta.column_info (table_name,column_name,ordinal,data_type,"
               "hive_type,comment,is_nullable,is_primary_key) VALUES " + values([
                   (lit(r[0]), lit(r[1]), str(r[2]), lit(r[3]), lit(r[4]), lit(r[5]),
                    b(r[6]), b(r[7])) for r in column_rows]) + ";")

    sql.append("INSERT INTO meta.metric_info (metric_id,metric_name,definition,formula,grain,"
               "source_table,value_column,unit,time_window,caveat,aliases) VALUES " + values([
                   tuple(lit(x) for x in r) for r in metric_rows]) + ";")

    sql.append("INSERT INTO meta.column_metric (metric_id,table_name,column_name,role) VALUES "
               + values([tuple(lit(x) for x in r) for r in mapping_rows]) + ";")
    sql.append("COMMIT;")

    psql_exec("\n".join(sql))
    print(f"\n[OK] 写入 meta.table_info={len(table_rows)} / column_info={len(column_rows)} "
          f"/ metric_info={len(metric_rows)} / column_metric={len(mapping_rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
