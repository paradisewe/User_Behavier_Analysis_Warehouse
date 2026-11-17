--建仓
CREATE DATABASE IF NOT EXISTS dws;



--聚合表

--01 日×行为
CREATE TABLE IF NOT EXISTS dws.dws_traffic_platform_1d_df(
    pv_cnt          BIGINT      COMMENT '当日浏览行为总数',
    fav_cnt         BIGINT      COMMENT '当日收藏行为总数',
    cart_cnt        BIGINT      COMMENT '当日加购行为总数',
    buy_cnt         BIGINT      COMMENT '当日购买行为总数',
    uv              BIGINT      COMMENT '当日独立用户数',
    buy_user_cnt    BIGINT      COMMENT '当日购买用户数',
    etl_time        TIMESTAMP   COMMENT 'ETL处理时间'
)
COMMENT 'DWS-平台日粒度行为汇总：各行为次数/UV/购买用户数，服务每日趋势与DAU分析'
PARTITIONED BY (stat_date STRING COMMENT '统计日期 yyyy-MM-dd（北京时间）')
STORED AS ORC;


--02 小时×行为
CREATE TABLE IF NOT EXISTS dws.dws_traffic_hour_df (
    behavior_hour INT     COMMENT '小时 0-23（北京时间）',
    behavior_type STRING  COMMENT '行为类型 pv/fav/cart/buy',
    behavior_cnt  BIGINT  COMMENT '行为次数',
    etl_time      TIMESTAMP COMMENT 'ETL处理时间'
)
COMMENT 'DWS-小时×行为全量汇总'
STORED AS ORC;

--03 用户粒度

CREATE TABLE IF NOT EXISTS dws.dws_traffic_user_summary_df(

    user_id	            BIGINT      COMMENT '用户ID',
    pv_cnt              BIGINT      COMMENT '全期浏览行为次数',
    fav_cnt             BIGINT      COMMENT '全期收藏行为次数',
    cart_cnt            BIGINT      COMMENT '全期加购行为次数',
    buy_cnt	            BIGINT	    COMMENT '全期购买行为次数',
    active_days	        BIGINT	    COMMENT '活跃天数（有行为的自然日数，北京时间）',
    first_active_date	STRING	    COMMENT '首次活跃日期',
    last_active_date	STRING	    COMMENT '末次活跃日期',
    first_buy_date	    STRING	    COMMENT '首次购买日期（未购买为 NULL）',
    last_buy_date	    STRING	    COMMENT '最近购买日期（未购买为 NULL）',
    etl_time	        TIMESTAMP	COMMENT 'ETL处理时间'
)
COMMENT 'DWS-用户粒度全量汇总-一人一行'
STORED AS ORC;

--04 类目粒度
CREATE TABLE IF NOT EXISTS dws.dws_traffic_category_df(

    category_id	    BIGINT	    COMMENT '商品类目ID',
    pv_cnt          BIGINT      COMMENT '全期浏览次数',
    fav_cnt         BIGINT      COMMENT '全期收藏次数',
    cart_cnt        BIGINT      COMMENT '全期加购次数',
    buy_cnt	        BIGINT	    COMMENT '全期购买次数',
    buyer_cnt	    BIGINT	    COMMENT '全期购买用户数',
    etl_time	    TIMESTAMP	COMMENT 'ETL处理时间'
)
COMMENT 'DWS-类目粒度全量汇总-一类一行'
STORED AS ORC;



--05 用户×商品粒度，全量，无分区
CREATE TABLE IF NOT EXISTS dws.dws_traffic_user_item_df(
    user_id	        BIGINT	    COMMENT '用户ID',
    item_id	        BIGINT	    COMMENT '商品ID',
    category_id	    BIGINT	    COMMENT '商品类目ID（冗余属性，取该对中出现过的类目）',
    first_pv_ts	    BIGINT	    COMMENT '首次浏览时间戳（未浏览为 NULL）',
    first_fav_ts	BIGINT	    COMMENT '首次收藏时间戳（未收藏为 NULL）',
    first_cart_ts	BIGINT	    COMMENT '首次加购时间戳（未加购为 NULL）',
    first_buy_ts	BIGINT	    COMMENT '首次购买时间戳（未购买为 NULL）',
    etl_time	    TIMESTAMP	COMMENT 'ETL处理时间'
)
COMMENT 'DWS-用户×商品粒度全量汇总'
STORED AS ORC;


--06 用户×活跃日
CREATE TABLE IF NOT EXISTS dws.dws_traffic_user_active_day_df(

    user_id	    BIGINT	    COMMENT '用户ID',
    stat_date	STRING	    COMMENT '活跃日期 yyyy-MM-dd（北京时间）',
    etl_time	TIMESTAMP	COMMENT 'ETL处理时间'
)
COMMENT 'DWS-用户×活跃日全量汇总'
STORED AS ORC;




--07 用户×购买类目（粒度：用户×类目×日）
CREATE TABLE IF NOT EXISTS dws.dws_traffic_user_category_buy_df(
    user_id      BIGINT    COMMENT '用户ID',
    category_id  BIGINT    COMMENT '商品类目ID',
    stat_date    STRING    COMMENT '购买日期 yyyy-MM-dd（北京时间）',
    buy_cnt      BIGINT    COMMENT '该用户在该类目当日的购买次数',
    etl_time     TIMESTAMP COMMENT 'ETL处理时间'
)
COMMENT 'DWS-用户×购买类目×日汇总（关联规则购物篮原料）'
STORED AS ORC;