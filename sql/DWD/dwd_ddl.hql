-----建库
CREATE DATABASE IF NOT EXISTS dwd;

-----建表
CREATE TABLE IF NOT EXISTS dwd.dwd_traffic_user_behavior_di(
    user_id             BIGINT          COMMENT '用户ID',
    item_id             BIGINT          COMMENT '商品ID',
    category_id         BIGINT          COMMENT '商品类目ID',
    behavior_type       STRING          COMMENT '行为类型 pv/fav/cart/buy',
    behavior_ts         BIGINT          COMMENT '行为时间戳（原始 Unix 秒）',
    behavior_time       STRING          COMMENT '行为时间 yyyy-MM-dd HH:mm:ss（北京时间）',
    behavior_date       STRING          COMMENT '行为日期 yyyy-MM-dd（北京时间）',
    behavior_hour       INT             COMMENT '行为小时 0-23（北京时间）',
    etl_time            TIMESTAMP       COMMENT 'ETL处理时间'
)
PARTITIONED BY (dt STRING COMMENT '业务日期 yyyy-MM-dd（北京时间）')
STORED AS ORC;







