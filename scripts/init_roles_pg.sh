#!/bin/bash
# 初始化只读角色 dw_ro：独立口令 + 最小权限 + 角色级超时
# 必须在 docker/pg/schema.sql 建表之后执行——GRANT SELECT ON ALL TABLES 只覆盖「已存在」的表
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

if [ -f .env ]; then
    set -a; . ./.env; set +a
fi
: "${PG_PASSWORD:?没找到 PG_PASSWORD（.env）——管理员连接用}"
: "${PG_RO_PASSWORD:?没找到 PG_RO_PASSWORD（.env）——只读账号必须用独立口令}"

psql_admin() {   # 只输出裸值
    docker exec -e PGPASSWORD="$PG_PASSWORD" pg16 psql -U dw -d dw -t -A "$@"
}

# ---------- 闸①：镜像表必须在位 ----------
cnt=$(psql_admin -c "SELECT count(*) FROM information_schema.tables
                     WHERE table_schema='public' AND table_name LIKE 'dw\_%'")
if [ "${cnt:-0}" -lt 19 ]; then
    echo "[FAIL] 只读到 ${cnt:-0} 张 dw_* 表：先跑 docker/pg/schema.sql，再执行本脚本" >&2
    exit 1
fi
echo "[OK] 镜像表在位：${cnt} 张"

# ---------- 闸②：建角色 / 轮换口令（幂等） ----------
RO_ESC=${PG_RO_PASSWORD//\'/\'\'}
if psql_admin -c "SELECT 1 FROM pg_roles WHERE rolname='dw_ro'" | grep -qx 1; then
    docker exec -e PGPASSWORD="$PG_PASSWORD" pg16 psql -U dw -d dw -q \
        -c "ALTER ROLE dw_ro WITH LOGIN PASSWORD '$RO_ESC'"
    echo "[OK] dw_ro 已存在，口令已轮换为 PG_RO_PASSWORD"
else
    docker exec -e PGPASSWORD="$PG_PASSWORD" pg16 psql -U dw -d dw -q \
        -c "CREATE ROLE dw_ro LOGIN PASSWORD '$RO_ESC'"
    echo "[OK] dw_ro 已创建（口令来自 PG_RO_PASSWORD）"
fi

# ---------- 授权（先有表再 GRANT；DEFAULT PRIVILEGES 管未来新表） ----------
docker exec -i -e PGPASSWORD="$PG_PASSWORD" pg16 psql -U dw -d dw -q -v ON_ERROR_STOP=1 <<'SQL'
GRANT CONNECT ON DATABASE dw TO dw_ro;
GRANT USAGE ON SCHEMA public TO dw_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO dw_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO dw_ro;
ALTER ROLE dw_ro SET statement_timeout = '30s';
SQL
echo "[OK] 授权完成：CONNECT + USAGE + SELECT（现有表与未来表）"

# ---------- 验收：走 TCP 用只读口令实连 ----------
probe=$(docker exec -e PGPASSWORD="$PG_RO_PASSWORD" pg16 psql -h 127.0.0.1 -U dw_ro -d dw -t -A -c "
    SELECT current_user || '|'
        || CASE WHEN has_table_privilege('dw_ro','dw_ads_traffic_rfm_df','SELECT') THEN 't' ELSE 'f' END || '|'
        || CASE WHEN has_table_privilege('dw_ro','dw_ads_traffic_rfm_df','DELETE') THEN 't' ELSE 'f' END || '|'
        || current_setting('statement_timeout')")

if [ "$probe" = "dw_ro|t|f|30s" ]; then
    echo "[PASS] TCP 登录 + SELECT 可用 + DELETE 被拒 + statement_timeout=30s"
else
    echo "[FAIL] 只读角色验收异常：$probe（期望 dw_ro|t|f|30s）" >&2
    exit 1
fi
