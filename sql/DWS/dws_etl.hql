-- 动态分区配置
SET hive.exec.dynamic.partition=true;
SET hive.exec.dynamic.partition.mode=nonstrict;
SET hive.exec.max.dynamic.partitions=2000;
SET hive.exec.max.dynamic.partitions.pernode=1000;

--01 日×行为
INSERT OVERWRITE TABLE dws.dws_traffic_platform_1d_df PARTITION (stat_date)
SELECT
    SUM((CASE WHEN behavior_type = 'pv'     THEN 1  ELSE 0  END))   AS pv_cnt,
    SUM((CASE WHEN behavior_type = 'fav'    THEN 1  ELSE 0  END))   AS fav_cnt,
    SUM((CASE WHEN behavior_type = 'cart'   THEN 1  ELSE 0  END))   AS cart_cnt,
    SUM((CASE WHEN behavior_type = 'buy'    THEN 1  ELSE 0  END))   AS buy_cnt,
    COUNT(DISTINCT user_id )                                        AS uv,
    COUNT(DISTINCT IF(behavior_type = 'buy', user_id, NULL))        AS buy_user_cnt,
    current_timestamp()                                             AS etl_time,
    behavior_date                                                   AS stat_date
FROM dwd.dwd_traffic_user_behavior_di
group by behavior_date;


--02 小时×行为
INSERT OVERWRITE TABLE dws.dws_traffic_hour_df
SELECT
    behavior_hour,  
    behavior_type,
    count(*)            AS behavior_cnt,
    current_timestamp() AS etl_time
FROM dwd.dwd_traffic_user_behavior_di
GROUP BY behavior_type, behavior_hour;


--03 用户粒度
INSERT OVERWRITE TABLE dws.dws_traffic_user_summary_df
SELECT
    user_id,
    SUM((CASE WHEN behavior_type = 'pv'     THEN 1  ELSE 0  END))   AS pv_cnt,
    SUM((CASE WHEN behavior_type = 'fav'    THEN 1  ELSE 0  END))   AS fav_cnt,
    SUM((CASE WHEN behavior_type = 'cart'   THEN 1  ELSE 0  END))   AS cart_cnt,
    SUM((CASE WHEN behavior_type = 'buy'    THEN 1  ELSE 0  END))   AS buy_cnt,
    COUNT(DISTINCT behavior_date)                                   AS active_days,
    min(behavior_date)                                              AS first_active_date,
    max(behavior_date)                                              AS last_active_date,
    min(case when behavior_type = 'buy' then behavior_date end)     AS first_buy_date,
    max(case when behavior_type = 'buy' then behavior_date end)     AS last_buy_date,
    current_timestamp()                                             AS etl_time
FROM dwd.dwd_traffic_user_behavior_di
GROUP BY user_id;



--04 类目粒度
INSERT OVERWRITE TABLE dws.dws_traffic_category_df
SELECT category_id,
       sum(case when behavior_type = 'pv'   then 1 else 0 end)          AS pv_cnt,
       sum(case when behavior_type = 'fav'  then 1 else 0 end)          AS fav_cnt,
       sum(case when behavior_type = 'cart' then 1 else 0 end)          AS cart_cnt,
       sum(case when behavior_type = 'buy'  then 1 else 0 end)          AS buy_cnt,
       count(DISTINCT case when behavior_type = 'buy' then user_id end) AS buyer_cnt,
       current_timestamp()                                              AS etl_time
FROM dwd.dwd_traffic_user_behavior_di
GROUP BY category_id;


--05 用户×商品粒度，全量，无分区
INSERT OVERWRITE TABLE dws.dws_traffic_user_item_df
SELECT user_id, item_id, category_id,
       first_pv_ts, first_fav_ts, first_cart_ts, first_buy_ts,
       current_timestamp() AS etl_time
FROM (
    SELECT user_id, item_id,
           max(category_id) AS category_id,
           min(case when behavior_type='pv'   then behavior_ts end) AS first_pv_ts,
           min(case when behavior_type='fav'  then behavior_ts end) AS first_fav_ts,
           min(case when behavior_type='cart' then behavior_ts end) AS first_cart_ts,
           min(case when behavior_type='buy'  then behavior_ts end) AS first_buy_ts
    FROM dwd.dwd_traffic_user_behavior_di
    GROUP BY user_id, item_id
) t;