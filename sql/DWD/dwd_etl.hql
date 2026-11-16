-- 动态分区配置
SET hive.exec.dynamic.partition=true;
SET hive.exec.dynamic.partition.mode=nonstrict;
SET hive.exec.max.dynamic.partitions=2000;
SET hive.exec.max.dynamic.partitions.pernode=1000;

--“内层裁剪数据、外层加工逻辑”

INSERT OVERWRITE TABLE dwd.dwd_traffic_user_behavior_di PARTITION (dt)
SELECT 
    user_id,
    item_id,
    category_id,
    behavior_type,
    behavior_ts,
    -- 在外层进行时间派生
    from_unixtime(behavior_ts + 28800, 'yyyy-MM-dd HH:mm:ss') AS behavior_time,
    from_unixtime(behavior_ts + 28800, 'yyyy-MM-dd') AS behavior_date,
    CAST(from_unixtime(behavior_ts + 28800, 'HH') AS INT) AS behavior_hour,
    current_timestamp() AS etl_time,
    -- 分区字段放在最后
    from_unixtime(behavior_ts + 28800, 'yyyy-MM-dd') AS dt
FROM (
    -- 内层只负责：过滤清洗 + 打标去重
    SELECT 
        user_id,
        item_id,
        category_id,
        behavior_type,
        behavior_ts,
        ROW_NUMBER() OVER (
            PARTITION BY user_id, item_id, category_id, behavior_type, behavior_ts
            ORDER BY behavior_ts
        ) AS rn
    FROM ods.ods_user_behavior_df
    WHERE 
        lower(trim(behavior_type)) IN ('pv','fav','cart','buy') 
    --WHERE 里没写 behavior_ts IS NOT NULL？不用写——NULL BETWEEN x AND y 结果是 NULL（不是 true），WHERE 只留 true，NULL 行自动被丢。这叫三值逻辑，SQL 和现实直觉最大的分歧点之一
        AND behavior_ts BETWEEN 1509494400 AND 1514764800 
        AND user_id IS NOT NULL 
        AND item_id IS NOT NULL 
        AND category_id IS NOT NULL
) t
WHERE rn = 1;