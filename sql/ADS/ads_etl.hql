-- ADS 层 ETL：全部只读 DWS；无分区表，无需动态分区 SET

-- 01 行为分布：宽表(pv/fav/cart/buy 各一列)逆透视成 4 行，窗口算占比
INSERT OVERWRITE TABLE ads.ads_traffic_behavior_dist_df
SELECT behavior_type,
       behavior_cnt,
       round(behavior_cnt / sum(behavior_cnt) over (), 4) AS pct
FROM (
    SELECT 'pv'  AS behavior_type, sum(pv_cnt)  AS behavior_cnt FROM dws.dws_traffic_platform_1d_df
    UNION ALL
    SELECT 'fav' , sum(fav_cnt)  FROM dws.dws_traffic_platform_1d_df
    UNION ALL
    SELECT 'cart', sum(cart_cnt) FROM dws.dws_traffic_platform_1d_df
    UNION ALL
    SELECT 'buy' , sum(buy_cnt)  FROM dws.dws_traffic_platform_1d_df
) t;

-- 02 用户级漏斗：一次扫描出 4 个用户数，外层统一算相对浏览的转化率
WITH u AS (
    SELECT sum(if(pv_cnt   > 0, 1, 0)) AS pv_user_cnt,
           sum(if(fav_cnt  > 0, 1, 0)) AS fav_user_cnt,
           sum(if(cart_cnt > 0, 1, 0)) AS cart_user_cnt,
           sum(if(buy_cnt  > 0, 1, 0)) AS buy_user_cnt
    FROM dws.dws_traffic_user_summary_df
)
INSERT OVERWRITE TABLE ads.ads_traffic_user_funnel_df
SELECT 'pv'   , pv_user_cnt  , round(1.0, 4)                          FROM u
UNION ALL
SELECT 'fav'  , fav_user_cnt , round(fav_user_cnt  / pv_user_cnt, 4)  FROM u
UNION ALL
SELECT 'cart' , cart_user_cnt, round(cart_user_cnt / pv_user_cnt, 4)  FROM u
UNION ALL
SELECT 'buy'  , buy_user_cnt , round(buy_user_cnt  / pv_user_cnt, 4)  FROM u;

-- 03 顺序漏斗：一次扫描算全部原始量与分母，外层算比率（口径：首次行为时间先后）
WITH m AS (
    SELECT sum(if(first_pv_ts   IS NOT NULL, 1, 0)) AS pv_pairs,
           sum(if(first_fav_ts  IS NOT NULL, 1, 0)) AS fav_pairs,
           sum(if(first_cart_ts IS NOT NULL, 1, 0)) AS cart_pairs,
           sum(if(first_pv_ts   IS NOT NULL AND first_cart_ts IS NOT NULL
                  AND first_cart_ts >= first_pv_ts, 1, 0))                AS pv_to_cart,
           sum(if(first_pv_ts   IS NOT NULL AND first_buy_ts  IS NOT NULL
                  AND first_buy_ts  >= first_pv_ts, 1, 0))                AS pv_to_buy,
           sum(if(first_cart_ts IS NOT NULL AND first_buy_ts  IS NOT NULL
                  AND first_buy_ts  >= first_cart_ts, 1, 0))              AS cart_to_buy,
           sum(if(first_fav_ts  IS NOT NULL AND first_buy_ts  IS NOT NULL
                  AND first_buy_ts  >= first_fav_ts, 1, 0))               AS fav_to_buy
    FROM dws.dws_traffic_user_item_df
)
INSERT OVERWRITE TABLE ads.ads_traffic_item_funnel_df
SELECT 'pv_to_cart' , pv_to_cart , round(pv_to_cart  / pv_pairs  , 4) FROM m
UNION ALL
SELECT 'pv_to_buy'  , pv_to_buy  , round(pv_to_buy   / pv_pairs  , 4) FROM m
UNION ALL
SELECT 'cart_to_buy', cart_to_buy, round(cart_to_buy / cart_pairs, 4) FROM m
UNION ALL
SELECT 'fav_to_buy' , fav_to_buy , round(fav_to_buy  / fav_pairs , 4) FROM m;

-- 04 每日趋势：1d 表直接投影
INSERT OVERWRITE TABLE ads.ads_traffic_daily_trend_df
SELECT stat_date, uv, buy_cnt, buy_user_cnt
FROM dws.dws_traffic_platform_1d_df;

-- 05 时段活跃：窗口按行为类型分区内算占比
INSERT OVERWRITE TABLE ads.ads_traffic_hourly_activity_df
SELECT behavior_hour,
       behavior_type,
       behavior_cnt,
       round(behavior_cnt / sum(behavior_cnt) over (partition by behavior_type), 4) AS pct
FROM dws.dws_traffic_hour_df;

-- 06 类目TOP10：row_number 排名 + 窗口算总量（rn 是别名，避开 rank 关键字）
INSERT OVERWRITE TABLE ads.ads_traffic_category_top_df
SELECT rn, category_id, buy_cnt, buyer_cnt, round(buy_cnt / total_buy, 4) AS buy_pct
FROM (
    SELECT row_number() over (order by buy_cnt desc) AS rn,
           category_id,
           buy_cnt,
           buyer_cnt,
           sum(buy_cnt) over () AS total_buy
    FROM dws.dws_traffic_category_df
) t
WHERE rn <= 10;

-- 07 复购：一次扫描两个口径，外层算比率
INSERT OVERWRITE TABLE ads.ads_traffic_repurchase_df
SELECT total_buyers, repeat_buyers, round(repeat_buyers / total_buyers, 4) AS repurchase_rate
FROM (
    SELECT count(if(buy_cnt >= 1, 1, null)) AS total_buyers,
           count(if(buy_cnt >= 2, 1, null)) AS repeat_buyers
    FROM dws.dws_traffic_user_summary_df
) t;

-- 08 行为深度分层：CASE 优先级即互斥规则（购买 > 加购 > 收藏 > 浏览），CASE 在 GROUP BY 中重复出现
INSERT OVERWRITE TABLE ads.ads_traffic_segmentation_df
SELECT segment, user_cnt, round(user_cnt / sum(user_cnt) over (), 4) AS pct
FROM (
    SELECT case
               when buy_cnt  > 0 then '已购买'
               when cart_cnt > 0 then '加购未购买'
               when fav_cnt  > 0 then '仅收藏'
               else '仅浏览'
           end AS segment,
           count(*) AS user_cnt
    FROM dws.dws_traffic_user_summary_df
    GROUP BY case
               when buy_cnt  > 0 then '已购买'
               when cart_cnt > 0 then '加购未购买'
               when fav_cnt  > 0 then '仅收藏'
               else '仅浏览'
             end
) t;