#!/bin/bash
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
URL="jdbc:hive2://localhost:10000"
OUT_DIR="$REPO_DIR/verify_out"; mkdir -p "$OUT_DIR"
# 黄金数字（数据或口径变更时才改这里）
GOLD_DWD=100147758
GOLD_DIFF=3049          # ODS − DWD = 3000 脏行 + 49 重复

FAIL=0
q() {   # $1=SQL → 输出干净值（每行一个）
  docker exec hive4 bash -c "beeline -u '$URL' -n hive --silent=true --showHeader=false --outputformat=tsv2 -e \"$1\" 2>/dev/null" | tr -d '\r' | grep -v '^$' || true 
}
check_eq() {  # $1=名称 $2=实际 $3=期望
  if [ "$2" = "$3" ]; then echo "[PASS] $1 = $2"
  else echo "[FAIL] $1 实际=$2 期望=$3"; FAIL=1; fi
}


# 阶段 0 · 数据质量（1 条）：DWD 的 behavior_type 去重后应恰好 4 行（用 q "SELECT count(*) FROM (SELECT behavior_type FROM dwd.dwd_traffic_user_behavior_di GROUP BY behavior_type) t"）。⚠️ 这条不是凑数——见解释 ⑥，它能抓住一个真实隐患。

DWD_TYPE=$(q "SELECT count(*) FROM (SELECT behavior_type FROM dwd.dwd_traffic_user_behavior_di GROUP BY behavior_type) t")
check_eq "阶段0 behavior_type 种类数" "$DWD_TYPE" "4"

# 阶段 1 · 结构（16 张表）：对 ods/dwd/dws/ads 四库分别 SHOW TABLES IN <db>，用 grep -c 数行数，期望 2 / 1 / 5 / 8（ods 含 staging 外部表）。

for db in ods dwd dws ads; do
    case $db in ods) exp=2 ;; dwd) exp=1 ;; dws) exp=5 ;; ads) exp=8 ;; esac
    CNT=$(q "SHOW TABLES IN $db" | wc -l | tr -d ' ')
    check_eq "阶段1 $db 表数量" "$CNT" "$exp"
done





# 阶段 2 · 内部一致性（6 条等式，全部整数）：

ODS_count=$(q "SELECT count(*) FROM ods.ods_user_behavior_df")
DWD_count=$(q "SELECT count(*) FROM dwd.dwd_traffic_user_behavior_di")
check_eq "阶段2 DWD 行数" "$DWD_count" "$GOLD_DWD"
check_eq "阶段2 ODS−DWD 差额" "$((ODS_count - DWD_count))" "$GOLD_DIFF"

DWD_buy=$(q "SELECT count(*) FROM dwd.dwd_traffic_user_behavior_di WHERE behavior_type='buy'")
DWS_1D_buy=$(q "SELECT sum(buy_cnt) FROM dws.dws_traffic_platform_1d_df")
DWS_USER_buy=$(q "SELECT sum(buy_cnt) FROM dws.dws_traffic_user_summary_df")
DWS_CAT_buy=$(q "SELECT sum(buy_cnt) FROM dws.dws_traffic_category_df")
check_eq "DWS platform_1d buy" "$DWS_1D_buy" "$DWD_buy"
check_eq "DWS user_summary buy" "$DWS_USER_buy" "$DWD_buy"
check_eq "DWS category buy" "$DWS_CAT_buy" "$DWD_buy"

USER_rows=$(q "SELECT count(*) FROM dws.dws_traffic_user_summary_df")
SEG_sum=$(q "SELECT sum(user_cnt) FROM ads.ads_traffic_segmentation_df")
check_eq "ADS 分层人数合计" "$SEG_sum" "$USER_rows"
# 阶段 3 · 导出 ADS 五表（供 6b 的 python 比对）：

export_tsv() {   # $1=SQL  $2=文件名
  q "$1" > "$OUT_DIR/$2"
  if [ ! -s "$OUT_DIR/$2" ]; then
    echo "[FAIL] $2 导出为空（SQL 报错或表无数据）"
    FAIL=1
  fi
}

export_tsv "SELECT behavior_type, behavior_cnt, pct FROM ads.ads_traffic_behavior_dist_df" 01.tsv
export_tsv "SELECT stage, user_cnt, from_view_rate FROM ads.ads_traffic_user_funnel_df"    02.tsv
export_tsv "SELECT transition, pair_cnt, rate FROM ads.ads_traffic_item_funnel_df"         03.tsv
export_tsv "SELECT rn, category_id, buy_cnt, buyer_cnt FROM ads.ads_traffic_category_top_df" 06.tsv
export_tsv "SELECT total_buyers, repeat_buyers, repurchase_rate FROM ads.ads_traffic_repurchase_df" 07.tsv
export_tsv "SELECT segment, user_cnt, pct FROM ads.ads_traffic_segmentation_df"            08.tsv




echo "==== 结果：$([ "$FAIL" -eq 0 ] && echo 全部通过 || echo 有失败项) ===="
exit "$FAIL"











