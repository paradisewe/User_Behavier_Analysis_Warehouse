#!/bin/bash

# A1.3 导出 + 加载：Hive DWS/ADS → export/*.tsv → PostgreSQL
set -euo pipefail

# 项目目录
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# 服务端口
URL="jdbc:hive2://localhost:10000"
# 容器
CONTAINER="hive4"
# 导出位置
EXPORT_DIR="$REPO_DIR/export"
mkdir -p "$EXPORT_DIR"

# 读 .env 里的密码；变量名是 PG_PASSWORD
if [ -f "$REPO_DIR/.env" ]; then
    set -a; . "$REPO_DIR/.env"; set +a
fi
: "${PG_PASSWORD:?没找到 PG_PASSWORD，请检查 .env}"

TMP_SQL="$(mktemp)"
trap 'rm -f "$TMP_SQL"' EXIT


# dw表名|Hive表|列清单（顺序必须与 schema.sql 一致；分区表 stat_date 放首列）
TABLES=(
  "dw_dws_traffic_platform_1d_df|dws.dws_traffic_platform_1d_df|stat_date,pv_cnt,fav_cnt,cart_cnt,buy_cnt,uv,buy_user_cnt"
  "dw_dws_traffic_hour_df|dws.dws_traffic_hour_df|behavior_hour,behavior_type,behavior_cnt"
  "dw_dws_traffic_user_summary_df|dws.dws_traffic_user_summary_df|user_id,pv_cnt,fav_cnt,cart_cnt,buy_cnt,active_days,first_active_date,last_active_date,first_buy_date,last_buy_date"
  "dw_dws_traffic_category_df|dws.dws_traffic_category_df|category_id,pv_cnt,fav_cnt,cart_cnt,buy_cnt,buyer_cnt"
  "dw_dws_traffic_user_active_day_df|dws.dws_traffic_user_active_day_df|user_id,stat_date"
  "dw_dws_traffic_user_category_buy_df|dws.dws_traffic_user_category_buy_df|user_id,category_id,stat_date,buy_cnt"
  "dw_ads_traffic_behavior_dist_df|ads.ads_traffic_behavior_dist_df|behavior_type,behavior_cnt,pct"
  "dw_ads_traffic_user_funnel_df|ads.ads_traffic_user_funnel_df|stage,user_cnt,from_view_rate"
  "dw_ads_traffic_item_funnel_df|ads.ads_traffic_item_funnel_df|transition,pair_cnt,rate"
  "dw_ads_traffic_daily_trend_df|ads.ads_traffic_daily_trend_df|stat_date,uv,buy_cnt,buy_user_cnt"
  "dw_ads_traffic_hourly_activity_df|ads.ads_traffic_hourly_activity_df|behavior_hour,behavior_type,behavior_cnt,pct"
  "dw_ads_traffic_category_top_df|ads.ads_traffic_category_top_df|rn,category_id,buy_cnt,buyer_cnt,buy_pct"
  "dw_ads_traffic_repurchase_df|ads.ads_traffic_repurchase_df|total_buyers,repeat_buyers,repurchase_rate"
  "dw_ads_traffic_segmentation_df|ads.ads_traffic_segmentation_df|segment,user_cnt,pct"
  "dw_ads_traffic_rfm_df|ads.ads_traffic_rfm_df|user_id,recency_days,frequency,r_score,f_score,segment"
  "dw_ads_traffic_retention_daily_df|ads.ads_traffic_retention_daily_df|stat_date,cohort_users,retained_users,next_day_rate"
  "dw_ads_traffic_retention_layer_df|ads.ads_traffic_retention_layer_df|layer,users,eligible_pairs,retained_pairs,next_day_rate,avg_eligible_days"
  "dw_ads_traffic_path_summary_df|ads.ads_traffic_path_summary_df|path,pairs,purchased,share_of_browsed,purchase_rate"
  "dw_ads_traffic_association_rules_df|ads.ads_traffic_association_rules_df|rn,antecedent_category,consequent_category,support,confidence,lift,pair_baskets,antecedent_baskets,consequent_baskets,total_baskets"
)


# 参数：--only <dw表名> 只跑一张
ONLY=""
if [ "${1:-}" = "--only" ]; then
    ONLY="${2:?用法: bash scripts/export_to_pg.sh --only dw_表名}"
fi

q() {   # $1 = SQL；输出干净的 TSV（stderr 落到文件，不吞掉）
    docker exec "$CONTAINER" bash -c \
        "beeline -u '$URL' -n hive --silent=true --showHeader=false --outputformat=tsv2 -e \"$1\"" \
        2>"$EXPORT_DIR/beeline.err" | tr -d '\r'
}


export_one() {
    local dw_table=$1 hive_table=$2 cols=$3
    local tsv="$EXPORT_DIR/$dw_table.tsv"
    local hive_cnt tsv_cnt

    # ① Hive 侧行数（基准）
    hive_cnt=$(q "SELECT count(*) FROM $hive_table" | grep -E '^[0-9]+$' | head -n1) || true
    if [ -z "${hive_cnt:-}" ]; then
        echo "[FAIL] $dw_table Hive 计数为空（表名错或查询失败）"
        tail -n 5 "$EXPORT_DIR/beeline.err"
        return 1
    fi

    # ② 导出
    if ! q "SELECT $cols FROM $hive_table" > "$tsv"; then
        echo "[FAIL] $dw_table 导出命令失败"
        tail -n 5 "$EXPORT_DIR/beeline.err"
        return 1
    fi

    # ③ 非空断言
    if [ ! -s "$tsv" ]; then
        echo "[FAIL] $dw_table 导出文件为空"
        return 1
    fi

    # ④ 行数比对
    tsv_cnt=$(awk 'END{print NR}' "$tsv")
    if [ "$hive_cnt" != "$tsv_cnt" ]; then
        echo "[FAIL] $dw_table 行数不符 Hive=$hive_cnt TSV=$tsv_cnt"
        return 1
    fi

    echo "[PASS] $dw_table $tsv_cnt 行"
    printf '%s\t%s\t%s\n' "$dw_table" "$hive_cnt" "$tsv_cnt" >> "$EXPORT_DIR/expected.tsv"
}


# ============ 第二段：TSV → PostgreSQL ============

load_one() {   # $1=dw表名  $2=列清单（与 export_one 同一份，保证列序一致）
    local dw_table=$1 cols=$2
    local tsv="$EXPORT_DIR/$dw_table.tsv"
    local want tsv_cnt bad_line n

    want=$(printf '%s' "$cols" | awk -F',' '{print NF}')

    # ① 先校验字段数——放在 TRUNCATE 之前，坏文件不许碰表
    read -r tsv_cnt bad_line < <(awk -F'\t' -v w="$want" '
        NF != w && !bad { bad = NR }
        END { print NR, bad + 0 }
    ' "$tsv")

    if [ "$bad_line" -ne 0 ]; then
        echo "[FAIL] $dw_table 第 $bad_line 行字段数≠$want（列清单与数据不一致）"
        return 1
    fi

    # ② 生成 SQL 到临时文件（/tmp，不进 export/，守目录契约）
    cat > "$TMP_SQL" <<SQL
\set ON_ERROR_STOP on
BEGIN;
TRUNCATE TABLE $dw_table;
COPY $dw_table ($cols)
FROM '/export/$dw_table.tsv'
WITH (FORMAT text, DELIMITER E'\t', NULL 'NULL', ENCODING 'UTF8');
COMMIT;
ANALYZE $dw_table;
SQL

    # ③ 执行：事务内 COPY 失败，TRUNCATE 一起回滚
    if ! docker exec -i -e "PGPASSWORD=$PG_PASSWORD" pg16 \
            psql -U dw -d dw -q -v ON_ERROR_STOP=1 < "$TMP_SQL"; then
        echo "[FAIL] $dw_table 加载失败（事务已回滚，旧数据未动）"
        return 1
    fi

    # ④ 落库断言：抓「COMMIT 之后表是空的」
    n=$(docker exec -e "PGPASSWORD=$PG_PASSWORD" pg16 \
            psql -U dw -d dw -t -A -c "SELECT count(*) FROM $dw_table")
    if [ "$n" != "$tsv_cnt" ]; then
        echo "[FAIL] $dw_table 落库 $n ≠ 导出 $tsv_cnt"
        return 1
    fi

    echo "[PASS] $dw_table 已加载 $n 行"
}


# 全量跑时清空记账文件；--only 时保留（方便累加单表）
if [ -z "$ONLY" ]; then
    : > "$EXPORT_DIR/expected.tsv"
fi

FAIL=0
for entry in "${TABLES[@]}"; do
    IFS='|' read -r dw_table hive_table cols <<< "$entry"
    if [ -n "$ONLY" ] && [ "$dw_table" != "$ONLY" ]; then
        continue
    fi
    if export_one "$dw_table" "$hive_table" "$cols"; then
        load_one "$dw_table" "$cols" || FAIL=1
    else
        FAIL=1
    fi
done

echo "==== 结果：$([ "$FAIL" -eq 0 ] && echo 全部通过 || echo 有失败项) ===="
exit "$FAIL"