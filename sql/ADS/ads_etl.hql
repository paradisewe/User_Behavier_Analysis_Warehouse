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


-- 09 用户RFM分层（核心窗口 2017-11-25 ~ 2017-12-03）-- 口径常量：窗口末日 = 2017-12-03（与上游 df.date.max() 一致）

INSERT OVERWRITE TABLE ads.ads_traffic_rfm_df
SELECT a.user_id,
       s.recency_days,
       NVL(s.frequency, 0) AS frequency,
       s.r_score,
       s.f_score,
       CASE WHEN s.user_id IS NULL                  THEN '未购买用户'
            WHEN s.r_score >= 3 AND s.f_score >= 3  THEN '重要价值客户'
            WHEN s.r_score >= 3 AND s.f_score  = 2  THEN '重要发展客户'
            WHEN s.r_score <  3 AND s.f_score >= 3  THEN '重要保持客户'
            WHEN s.r_score <  3 AND s.f_score  = 2  THEN '重要挽留客户'
            WHEN s.r_score >= 3                     THEN '一般价值客户'
            ELSE '一般挽留客户'
       END AS segment
FROM (
    -- 分母：窗口内活跃过的全部用户（含未购买）
    SELECT user_id
    FROM dws.dws_traffic_user_active_day_df
    WHERE stat_date BETWEEN '2017-11-25' AND '2017-12-03'
    GROUP BY user_id
) a
LEFT JOIN (
    -- 分子：窗口内买家，算 F 与 R，并原地打分
    SELECT user_id, frequency, recency_days, r_score, f_score
    FROM (
        SELECT user_id,
               COUNT(*)                                     AS frequency,
               DATEDIFF('2017-12-03', MAX(behavior_date))    AS recency_days,
               CASE WHEN DATEDIFF('2017-12-03', MAX(behavior_date)) = 0  THEN 4
                    WHEN DATEDIFF('2017-12-03', MAX(behavior_date)) = 1  THEN 3
                    WHEN DATEDIFF('2017-12-03', MAX(behavior_date)) <= 3 THEN 2
                    ELSE 1
               END                                          AS r_score,
               NTILE(4) OVER (ORDER BY COUNT(*))            AS f_score
        FROM dwd.dwd_traffic_user_behavior_di
        WHERE behavior_type = 'buy'
          AND behavior_date BETWEEN '2017-11-25' AND '2017-12-03'
        GROUP BY user_id
    ) t
) s ON a.user_id = s.user_id;



-- 10 逐日次日留存（日历次日口径）
WITH core AS (
    SELECT user_id, stat_date
    FROM dws.dws_traffic_user_active_day_df
    WHERE stat_date BETWEEN '2017-11-25' AND '2017-12-03'
),
cohort AS (
    SELECT stat_date, COUNT(*) AS cohort_users
    FROM core
    GROUP BY stat_date
),
retained AS (
    SELECT c.stat_date, COUNT(*) AS retained_users
    FROM core c
    JOIN core n
      ON c.user_id = n.user_id
     AND n.stat_date = DATE_ADD(c.stat_date, 1)
    GROUP BY c.stat_date
)
INSERT OVERWRITE TABLE ads.ads_traffic_retention_daily_df
SELECT d.stat_date,
       d.cohort_users,
       NVL(r.retained_users, 0)                                   AS retained_users,
       ROUND(NVL(r.retained_users, 0) / d.cohort_users, 4)        AS next_day_rate
FROM cohort d
LEFT JOIN retained r ON d.stat_date = r.stat_date
WHERE d.stat_date < '2017-12-03';      -- 末日无次日，排除



-- 11 分层次日留存（人-日对口径，层按窗口内活跃天数）
WITH core AS (
    SELECT user_id, stat_date
    FROM dws.dws_traffic_user_active_day_df
    WHERE stat_date BETWEEN '2017-11-25' AND '2017-12-03'
),
lay AS (
    SELECT user_id,
           CASE WHEN COUNT(*) <= 1 THEN '1天'
                WHEN COUNT(*) <= 3 THEN '2~3天'
                WHEN COUNT(*) <= 5 THEN '4~5天'
                ELSE '6~9天'
           END AS layer
    FROM core
    GROUP BY user_id
),
pair_stat AS (
    SELECT l.layer,
           COUNT(*)                                                AS eligible_pairs,
           SUM(CASE WHEN n.user_id IS NOT NULL THEN 1 ELSE 0 END)  AS retained_pairs
    FROM core c
    JOIN lay l ON c.user_id = l.user_id
    LEFT JOIN core n
      ON c.user_id = n.user_id
     AND n.stat_date = DATE_ADD(c.stat_date, 1)
    WHERE c.stat_date < '2017-12-03'
    GROUP BY l.layer
),
layer_users AS (
    SELECT layer, COUNT(*) AS users
    FROM lay
    GROUP BY layer
)
INSERT OVERWRITE TABLE ads.ads_traffic_retention_layer_df
SELECT u.layer,
       u.users,
       NVL(p.eligible_pairs, 0)                                              AS eligible_pairs,
       NVL(p.retained_pairs, 0)                                              AS retained_pairs,
       ROUND(NVL(p.retained_pairs, 0) / NVL(p.eligible_pairs, 1), 4)         AS next_day_rate,
       ROUND(NVL(p.eligible_pairs, 0) / u.users, 2)                          AS avg_eligible_days
FROM layer_users u
LEFT JOIN pair_stat p ON u.layer = p.layer;


--12 路径（ts*10 + 权重 打破同秒并列，权重 pv0/fav1/cart2/buy3）
INSERT OVERWRITE TABLE ads.ads_traffic_path_summary_df
SELECT path, pairs, purchased,
       round(pairs / sum(pairs) OVER (), 4)  AS share_of_browsed,
       round(purchased / pairs, 4)           AS purchase_rate
FROM (
    SELECT path,
           count(*)                                            AS pairs,
           sum(CASE WHEN outcome = '购买' THEN 1 ELSE 0 END)    AS purchased
    FROM (
        SELECT user_id, item_id,
               CASE
                 WHEN buy_k IS NULL AND cart_k IS NULL AND fav_k IS NULL THEN '仅浏览'
                 WHEN buy_k IS NOT NULL
                      AND (cart_k IS NULL OR buy_k < cart_k)
                      AND (fav_k  IS NULL OR buy_k < fav_k)          THEN '直接购买'
                 WHEN cart_k IS NOT NULL
                      AND (fav_k IS NULL OR cart_k <= fav_k)         THEN '先加购'
                 WHEN fav_k IS NOT NULL                              THEN '先收藏'
                 ELSE '先加购'
               END AS path,
               CASE WHEN buy_k IS NOT NULL THEN '购买' ELSE '未购买' END AS outcome
        FROM (
            SELECT user_id, item_id,
                   first_pv_ts   * 10     AS pv_k,
                   first_fav_ts  * 10 + 1 AS fav_k,
                   first_cart_ts * 10 + 2 AS cart_k,
                   first_buy_ts  * 10 + 3 AS buy_k
            FROM dws.dws_traffic_user_item_df
            WHERE first_pv_ts IS NOT NULL
              AND from_unixtime(first_pv_ts + 28800, 'yyyy-MM-dd')
                  BETWEEN '2017-11-25' AND '2017-12-03'
        ) p
    ) c
    GROUP BY path
) t;

-- 13 关联规则（2-项集精确计数，替代 Apriori）
WITH basket AS (
    SELECT user_id, category_id
    FROM dws.dws_traffic_user_category_buy_df
    WHERE stat_date BETWEEN '2017-11-25' AND '2017-12-03'
    GROUP BY user_id, category_id
),
sz AS (
    SELECT user_id, count(*) AS n FROM basket GROUP BY user_id
),
valid AS (
    SELECT b.user_id, b.category_id
    FROM basket b JOIN sz s ON b.user_id = s.user_id
    WHERE s.n >= 2
),
cat_cnt AS (
    SELECT category_id, count(*) AS ib FROM valid GROUP BY category_id
),
tot AS (
    SELECT count(*) AS nb FROM sz WHERE n >= 2
),
pair AS (
    SELECT a.category_id AS ante, b.category_id AS cons, count(*) AS both_baskets
    FROM valid a
    JOIN valid b ON a.user_id = b.user_id AND a.category_id <> b.category_id
    GROUP BY a.category_id, b.category_id
),
calc AS (
    SELECT p.ante, p.cons,
           round(p.both_baskets / t.nb, 6)                     AS support,
           round(p.both_baskets / ia.ib, 4)                    AS confidence,
           round((p.both_baskets / ia.ib) / (ic.ib / t.nb), 4) AS lift,
           p.both_baskets, ia.ib AS ante_b, ic.ib AS cons_b, t.nb
    FROM pair p
    CROSS JOIN tot t
    JOIN cat_cnt ia ON p.ante = ia.category_id
    JOIN cat_cnt ic ON p.cons = ic.category_id
    WHERE p.both_baskets / t.nb >= 0.0005
      AND p.both_baskets / ia.ib >= 0.10
      AND (p.both_baskets / ia.ib) / (ic.ib / t.nb) > 1.0
),
ranked AS (
    SELECT row_number() OVER (ORDER BY lift DESC) AS rn, c.*
    FROM calc c
)
INSERT OVERWRITE TABLE ads.ads_traffic_association_rules_df
SELECT rn, ante, cons, support, confidence, lift,
       both_baskets, ante_b, cons_b, nb
FROM ranked
WHERE rn <= 100;