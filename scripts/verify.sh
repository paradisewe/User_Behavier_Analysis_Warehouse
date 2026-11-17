#!/bin/bash
# 分层验收：结构 → 内部一致性 → 导出 ADS 供 verify_compare.py 跨管道对账
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
URL="jdbc:hive2://localhost:10000"
OUT_DIR="$REPO_DIR/verify_out"; mkdir -p "$OUT_DIR"

# ---------- 黄金数字（数据或口径变更时才改这里） ----------
GOLD_DWD=100147758          # DWD 去重后行数
GOLD_DIFF=3049              # ODS − DWD = 3000 脏行 + 49 重复
ADS_TABLES=13               # 01~08 + 09 RFM / 10 留存日 / 11 留存层 / 12 路径 / 13 关联规则

FAIL=0

q() {   # $1=SQL → 输出干净值（每行一个）
  docker exec hive4 bash -c "beeline -u '$URL' -n hive --silent=true --showHeader=false --outputformat=tsv2 -e \"$1\" 2>/dev/null" \
    | tr -d '\r' | grep -v '^$' || true
}

check_eq() {  # $1=名称 $2=实际 $3=期望
  if [ "$2" = "$3" ]; then echo "[PASS] $1 = $2"
  else echo "[FAIL] $1 实际='$2' 期望='$3'"; FAIL=1; fi
}

export_tsv() {   # $1=SQL  $2=文件名
  q "$1" > "$OUT_DIR/$2"
  if [ ! -s "$OUT_DIR/$2" ]; then
    echo "[FAIL] $2 导出为空（表未建 / SQL 报错 / 无数据）"
    FAIL=1
  fi
}

# ============================================================
echo "==== 阶段 0 · 数据质量 ===="
# DWD 的 behavior_type 去重后必须恰好 4 种（抓「WHERE 规范化了但 SELECT 存原值」的隐患）
DWD_TYPE=$(q "SELECT count(*) FROM (SELECT behavior_type FROM dwd.dwd_traffic_user_behavior_di GROUP BY behavior_type) t")
check_eq "behavior_type 种类数" "$DWD_TYPE" "4"

# ============================================================
echo
echo "==== 阶段 1 · 结构 ===="
for db in ods dwd dws ads; do
  case $db in
    ods) exp=2 ;;      # staging 外部表 + 正式 ODS
    dwd) exp=1 ;;
    dws) exp=7 ;;
    ads) exp=$ADS_TABLES ;;
  esac
  CNT=$(q "SHOW TABLES IN $db" | wc -l | tr -d ' ')
  check_eq "$db 表数量" "$CNT" "$exp"
done

# ============================================================
echo
echo "==== 阶段 2 · 内部一致性 ===="

# --- 2.1 ODS / DWD 行数守恒 ---
ODS_count=$(q "SELECT count(*) FROM ods.ods_user_behavior_df")
DWD_count=$(q "SELECT count(*) FROM dwd.dwd_traffic_user_behavior_di")
check_eq "DWD 行数" "$DWD_count" "$GOLD_DWD"
if [ -n "$ODS_count" ] && [ -n "$DWD_count" ]; then
  check_eq "ODS−DWD 差额" "$((ODS_count - DWD_count))" "$GOLD_DIFF"
else
  echo "[FAIL] ODS/DWD 行数取值为空"; FAIL=1
fi

# --- 2.2 buy 次数跨层守恒（DWD = 三张 DWS = ADS） ---
DWD_buy=$(q "SELECT count(*) FROM dwd.dwd_traffic_user_behavior_di WHERE behavior_type='buy'")
DWS_1D_buy=$(q "SELECT sum(buy_cnt) FROM dws.dws_traffic_platform_1d_df")
DWS_USER_buy=$(q "SELECT sum(buy_cnt) FROM dws.dws_traffic_user_summary_df")
DWS_CAT_buy=$(q "SELECT sum(buy_cnt) FROM dws.dws_traffic_category_df")
check_eq "DWS platform_1d buy" "$DWS_1D_buy" "$DWD_buy"
check_eq "DWS user_summary buy" "$DWS_USER_buy" "$DWD_buy"
check_eq "DWS category buy" "$DWS_CAT_buy" "$DWD_buy"

# --- 2.3 用户规模守恒（DWD 去重用户 = ADS 分层 = RFM = 留存分层） ---
USER_rows=$(q "SELECT count(*) FROM dws.dws_traffic_user_summary_df")
SEG_sum=$(q "SELECT sum(user_cnt) FROM ads.ads_traffic_segmentation_df")
check_eq "ADS-08 分层人数合计" "$SEG_sum" "$USER_rows"

# 核心窗口活跃用户数（09/11 的分母基准）
CORE_USERS=$(q "SELECT count(DISTINCT user_id) FROM dws.dws_traffic_user_active_day_df WHERE stat_date BETWEEN '2017-11-25' AND '2017-12-03'")
RFM_rows=$(q "SELECT count(*) FROM ads.ads_traffic_rfm_df")
LAYER_users=$(q "SELECT sum(users) FROM ads.ads_traffic_retention_layer_df")
check_eq "ADS-09 RFM 用户数" "$RFM_rows" "$CORE_USERS"
check_eq "ADS-11 分层留存用户合计" "$LAYER_users" "$CORE_USERS"

# --- 2.4 留存表形状 ---
RET_DAYS=$(q "SELECT count(*) FROM ads.ads_traffic_retention_daily_df")
check_eq "ADS-10 逐日留存行数" "$RET_DAYS" "8"      # 9 天窗口去掉末日

# --- 2.5 路径总对数 = 核心窗口有浏览的用户-商品对数（跨层核对，需扫 user_item） ---
PATH_pairs=$(q "SELECT sum(pairs) FROM ads.ads_traffic_path_summary_df")
PV_pairs=$(q "SELECT count(*) FROM dws.dws_traffic_user_item_df WHERE first_pv_ts IS NOT NULL AND from_unixtime(first_pv_ts + 28800, 'yyyy-MM-dd') BETWEEN '2017-11-25' AND '2017-12-03'")
check_eq "ADS-12 路径总对数" "$PATH_pairs" "$PV_pairs"

# --- 2.6 关联规则：条数与购物篮分母一致 ---
RULES_cnt=$(q "SELECT count(*) FROM ads.ads_traffic_association_rules_df")
check_eq "ADS-13 关联规则条数(TopN)" "$RULES_cnt" "100"

RULES_baskets=$(q "SELECT max(total_baskets) FROM ads.ads_traffic_association_rules_df")
DWS_baskets=$(q "SELECT count(*) FROM (
    SELECT user_id FROM dws.dws_traffic_user_category_buy_df
    WHERE stat_date BETWEEN '2017-11-25' AND '2017-12-03'
    GROUP BY user_id HAVING count(DISTINCT category_id) >= 2
) t")
check_eq "ADS-13 购物篮数一致" "$RULES_baskets" "$DWS_baskets"

# ============================================================
echo
echo "==== 阶段 3 · 导出 ADS（供 verify_compare.py） ===="

export_tsv "SELECT behavior_type, behavior_cnt, pct FROM ads.ads_traffic_behavior_dist_df" 01.tsv
export_tsv "SELECT stage, user_cnt, from_view_rate FROM ads.ads_traffic_user_funnel_df" 02.tsv
export_tsv "SELECT transition, pair_cnt, rate FROM ads.ads_traffic_item_funnel_df" 03.tsv
export_tsv "SELECT rn, category_id, buy_cnt, buyer_cnt FROM ads.ads_traffic_category_top_df" 06.tsv
export_tsv "SELECT total_buyers, repeat_buyers, repurchase_rate FROM ads.ads_traffic_repurchase_df" 07.tsv
export_tsv "SELECT segment, user_cnt, pct FROM ads.ads_traffic_segmentation_df" 08.tsv

# 09~13：先聚合成小表再导出（RFM 用户级 98 万行，不导明细）
export_tsv "SELECT segment, count(*) AS users, round(avg(frequency), 2) AS avg_f, round(avg(recency_days), 2) AS avg_r, round(count(*) / sum(count(*)) over (), 4) AS share FROM ads.ads_traffic_rfm_df GROUP BY segment" 09.tsv
export_tsv "SELECT stat_date, cohort_users, retained_users, next_day_rate FROM ads.ads_traffic_retention_daily_df" 10.tsv
export_tsv "SELECT layer, users, eligible_pairs, retained_pairs, next_day_rate, avg_eligible_days FROM ads.ads_traffic_retention_layer_df" 11.tsv
export_tsv "SELECT path, pairs, purchased, share_of_browsed, purchase_rate FROM ads.ads_traffic_path_summary_df" 12.tsv
export_tsv "SELECT rn, antecedent_category, consequent_category, support, confidence, lift, pair_baskets, antecedent_baskets, consequent_baskets, total_baskets FROM ads.ads_traffic_association_rules_df ORDER BY rn" 13.tsv

echo "====================总结==============================="
echo
echo "==== 结果：$([ "$FAIL" -eq 0 ] && echo 全部通过 || echo 有失败项) ===="
exit "$FAIL"
