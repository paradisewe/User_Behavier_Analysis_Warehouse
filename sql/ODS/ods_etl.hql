-- 动态分区三件套
SET hive.exec.dynamic.partition=true;--开启动态
SET hive.exec.dynamic.partition.mode=nonstrict;--应用动态
SET hive.exec.max.dynamic.partitions=2000;---最多创建2000个分区

-- staging(STRING 文本) → 正式 ODS(ORC)：CAST + 算分区列，一行不滤
INSERT OVERWRITE TABLE ods.ods_user_behavior_df PARTITION (dt)
SELECT
    CAST(user_id     AS BIGINT) AS user_id,                         --cast 失败不报错，该值变 NULL，行保留。脏数据不丢、只是字段空着
    CAST(item_id     AS BIGINT) AS item_id,
    CAST(category_id AS BIGINT) AS category_id,
    behavior_type               AS behavior_type,
    CAST(ts           AS BIGINT) AS behavior_ts,
    current_timestamp()         AS etl_time,                        --记录这批数据什么时候被装载进仓。
    from_unixtime(unix_timestamp(), 'yyyy-MM-dd') AS dt   -- 分区列必须放最后
FROM ods.ods_stg_user_behavior_csv;