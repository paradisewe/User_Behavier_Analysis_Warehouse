#!/usr/bin/env python3
"""A1.4 镜像验收：Hive 行数 / TSV 行数 / PG 行数 三方一致 + 列指纹 + ANALYZE 探针。"""
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXPORT_DIR = REPO / "export"
EXPORT_SH = REPO / "scripts" / "export_to_pg.sh"
PG_CONTAINER = "pg16"
EXPECTED_TABLES = 19
EXPECTED_TOTAL_ROWS = 10_839_144
FORBIDDEN = "dw_dws_traffic_user_item_df"
NUMERIC = {"bigint", "integer", "smallint", "double precision", "real", "numeric"}
FLOAT_TYPES = {"double precision", "real", "numeric"}
FLOAT_TOL = 1e-6

FAIL = 0


def ok(msg):
    print(f"[PASS] {msg}")


def bad(msg):
    global FAIL
    FAIL = 1
    print(f"[FAIL] {msg}")


def load_env():
    p = REPO / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    if not os.environ.get("PG_PASSWORD"):
        sys.exit("缺少 PG_PASSWORD（检查 .env）")


def pg(sql):
    r = subprocess.run(
        ["docker", "exec", "-e", f"PGPASSWORD={os.environ['PG_PASSWORD']}",
         PG_CONTAINER, "psql", "-U", "dw", "-d", "dw",
         "-t", "-A", "-F", "|", "-c", sql],
        capture_output=True, text=True, encoding="utf-8",
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "psql 失败")
    return r.stdout.strip()


def parse_tables():
    """从 export_to_pg.sh 的 TABLES 数组解析，避免再抄一份列清单。"""
    pat = re.compile(r'"(dw_[^|"]+)\|([^|"]+)\|([^"]+)"')
    return [(m.group(1), m.group(2), [c for c in m.group(3).split(",") if c])
            for m in pat.finditer(EXPORT_SH.read_text(encoding="utf-8"))]


def read_expected():
    p = EXPORT_DIR / "expected.tsv"
    if not p.exists():
        sys.exit("缺少 export/expected.tsv，先跑 export_to_pg.sh")
    exp = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        f = line.split("\t")
        if len(f) == 3:
            exp[f[0]] = (int(f[1]), int(f[2]))   # 同表多行（多次 --only）取最后一条
    return exp


def scan_tsv(path, numeric):
    """一次遍历：返回 (行数, 首条坏行号, {列序号: 数值和或非空计数})。"""
    agg = {i: (0.0 if numeric.get(i) else 0) for i in range(NCOLS[0])}
    lines = bad_line = 0
    with open(path, "rb") as f:
        for raw in f:
            lines += 1
            parts = raw.rstrip(b"\r\n").split(b"\t")
            if len(parts) != NCOLS[0]:
                bad_line = lines
                break
            for i, v in enumerate(parts):
                if i in numeric:
                    if v not in (b"NULL", b""):
                        agg[i] += float(v) if numeric[i] else int(v)
                elif v != b"NULL":
                    agg[i] += 1
    return lines, bad_line, agg


NCOLS = [0]   # 当前表的列数，scan_tsv 里用


def pg_fingerprint(t, cols, types):
    exprs = []
    for c in cols:
        dt = types.get(c, "")
        if dt in NUMERIC:
            exprs.append(f'COALESCE(sum("{c}"), 0)::text')
        else:
            exprs.append(f'count("{c}")::text')
    return pg(f'SELECT {", ".join(exprs)} FROM {t}').split("|")


def main():
    load_env()
    tables = parse_tables()
    expected = read_expected()

    if len(tables) != EXPECTED_TABLES:
        bad(f"export_to_pg.sh 表数量 {len(tables)} ≠ {EXPECTED_TABLES}")

    names = [t[0] for t in tables]
    live = pg("SELECT table_name FROM information_schema.tables "
              "WHERE table_schema='public' AND table_name LIKE 'dw%' ORDER BY 1")
    live_set = set(live.splitlines()) if live else set()

    missing = [n for n in names if n not in live_set]
    if missing:
        bad(f"PG 缺表：{', '.join(missing)}")
    extra = sorted(live_set - set(names))
    if extra:
        bad(f"PG 多出非契约表：{', '.join(extra)}")
    if FORBIDDEN in live_set:
        bad(f"契约被改：存在 {FORBIDDEN}")

    total = 0
    for t, hive_t, cols in tables:
        exp = expected.get(t)
        if not exp:
            bad(f"{t} 无记账（expected.tsv 缺行）")
            continue
        hive_cnt, tsv_cnt = exp
        tsv = EXPORT_DIR / f"{t}.tsv"
        if not tsv.exists() or tsv.stat().st_size == 0:
            bad(f"{t} TSV 不存在或为空")
            continue

        coltypes = {}
        rows = pg(f"SELECT column_name, data_type FROM information_schema.columns "
                  f"WHERE table_schema='public' AND table_name='{t}' ORDER BY ordinal_position")
        for line in rows.splitlines():
            if "|" in line:
                c, dt = line.split("|", 1)
                coltypes[c] = dt
        lost_cols = [c for c in cols if c not in coltypes]
        if lost_cols:
            bad(f"{t} PG 缺列：{', '.join(lost_cols)}")

        numeric = {i: coltypes.get(c) in FLOAT_TYPES
                   for i, c in enumerate(cols) if coltypes.get(c) in NUMERIC}
        NCOLS[0] = len(cols)
        lines, bad_line, agg = scan_tsv(tsv, numeric)

        if bad_line:
            bad(f"{t} TSV 第 {bad_line} 行字段数 ≠ {len(cols)}")
            continue

        pg_cnt = int(pg(f"SELECT count(*) FROM {t}") or 0)
        total += pg_cnt

        if not (hive_cnt == tsv_cnt == lines == pg_cnt):
            bad(f"{t} 行数不一致 Hive={hive_cnt} TSV记账={tsv_cnt} TSV实数={lines} PG={pg_cnt}")
            continue

        vals = pg_fingerprint(t, cols, coltypes)
        fp_bad = []
        for i, c in enumerate(cols):
            dt = coltypes[c]
            if dt in NUMERIC:
                a, b = agg[i], vals[i]
                if dt in FLOAT_TYPES:
                    if abs(float(b) - a) > max(1.0, abs(a)) * FLOAT_TOL:
                        fp_bad.append(f"{c} sum TSV={a} PG={b}")
                elif int(b) != a:
                    fp_bad.append(f"{c} sum TSV={a} PG={b}")
            else:
                if int(vals[i]) != agg[i]:
                    fp_bad.append(f"{c} 非空数 TSV={agg[i]} PG={vals[i]}")
        if fp_bad:
            bad(f"{t} 列指纹不符：" + "；".join(fp_bad[:3]))
            continue

        rel = pg(f"SELECT reltuples::bigint FROM pg_class "
                 f"WHERE relname='{t}' AND relnamespace='public'::regnamespace") or "-1"
        rel = int(rel)
        if rel < 0:
            bad(f"{t} 未 ANALYZE（reltuples=-1）")
        elif pg_cnt > 0 and abs(rel - pg_cnt) / pg_cnt > 0.2:
            bad(f"{t} 统计信息失真 reltuples={rel} 实际={pg_cnt}")

        ok(f"{t} rows={pg_cnt} 指纹={len(cols)} 项 reltuples={rel}")

    if total != EXPECTED_TOTAL_ROWS:
        bad(f"总行数 {total} ≠ 基线 {EXPECTED_TOTAL_ROWS}")
    else:
        ok(f"总行数 {total}")

    print(f"\n==== 结果：{'全部通过' if FAIL == 0 else '有失败项'} ====")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()