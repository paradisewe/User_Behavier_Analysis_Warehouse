--#建ods库
--CREATE DATABASE IF NOT EXISTS ods；
--#hive无法读取csv
    --#staging装载通道（存数据及其定义方便hive读取）
    --#external外部表（不存数据提供hive读取数据的路径）	内部表 DROP 会连数据一起删；外部表 DROP 只删描述。原始数据必须外部

---出现问题了所以先删掉表再重建
DROP TABLE IF EXISTS ods.ods_user_behavior_df;

-- 1. 建库
CREATE DATABASE IF NOT EXISTS ods;

-- 2. staging 外部表：CSV 的"Hive 视图"，只负责按行读
CREATE EXTERNAL TABLE IF NOT EXISTS ods.ods_stg_user_behavior_csv (
    user_id        STRING COMMENT '用户ID',
    item_id        STRING COMMENT '商品ID',
    category_id    STRING COMMENT '商品类目ID',
    behavior_type  STRING COMMENT '行为类型 pv/cart/fav/buy',
    ts             STRING COMMENT '行为时间戳（unix 秒）'
)
ROW FORMAT DELIMITED FIELDS TERMINATED BY ','
STORED AS TEXTFILE
LOCATION '/opt/hive/data/warehouse/ods/user_behavior';

-- 3. 正式 ODS 表：列式 ORC + 按行为日期分区
CREATE TABLE IF NOT EXISTS ods.ods_user_behavior_df (
    user_id        BIGINT    COMMENT '用户ID',
    item_id        BIGINT    COMMENT '商品ID',
    category_id    BIGINT    COMMENT '类目ID',
    behavior_type  STRING    COMMENT '行为类型 pv/cart/fav/buy',
    behavior_ts    BIGINT    COMMENT '行为时间戳（unix 秒）',
    etl_time       TIMESTAMP COMMENT 'ETL 装载时间'
)
PARTITIONED BY (dt STRING COMMENT '数据装载日期 yyyy-MM-dd')
STORED AS ORC;