CREATE DATABASE IF NOT EXISTS ads;


-- 01 行为分布（基准 01_behavior_dist）
CREATE TABLE IF NOT EXISTS ads.ads_traffic_behavior_dist_df (
    behavior_type STRING COMMENT '行为类型 pv/fav/cart/buy',
    behavior_cnt  BIGINT COMMENT '全期行为次数',
    pct           DOUBLE COMMENT '占全部行为的比例（4位小数）'
)
COMMENT 'ADS-01-全期行为类型分布'
STORED AS ORC;

-- 02 用户级漏斗（基准 02_user_funnel）
CREATE TABLE IF NOT EXISTS ads.ads_traffic_user_funnel_df (
    stage          STRING COMMENT '漏斗层 pv/fav/cart/buy',
    user_cnt       BIGINT COMMENT '发生过该行为的用户数',
    from_view_rate DOUBLE COMMENT '相对浏览用户的转化率（pv 层恒为 1）'
)
COMMENT 'ADS-02-用户级行为漏斗（用户发生过该行为即计入）'
STORED AS ORC;

-- 03 用户×商品级顺序漏斗（基准 03_item_funnel）
CREATE TABLE IF NOT EXISTS ads.ads_traffic_item_funnel_df (
    transition STRING COMMENT '转化路径 pv_to_cart/pv_to_buy/cart_to_buy/fav_to_buy',
    pair_cnt   BIGINT COMMENT '满足该路径的用户-商品对数',
    rate       DOUBLE COMMENT '转化率（分母为路径起点对应的对数，4位小数）'
)
COMMENT 'ADS-03-用户-商品级顺序漏斗（首次行为时间先后判定）'
STORED AS ORC;

-- 04 每日趋势（基准 04_daily_trend）
CREATE TABLE IF NOT EXISTS ads.ads_traffic_daily_trend_df (
    stat_date    STRING COMMENT '统计日期 yyyy-MM-dd（北京时间）',
    uv           BIGINT COMMENT '当日独立用户数 DAU',
    buy_cnt      BIGINT COMMENT '当日购买次数',
    buy_user_cnt BIGINT COMMENT '当日购买用户数'
)
COMMENT 'ADS-04-每日活跃与购买趋势'
STORED AS ORC;

-- 05 时段活跃（基准 05_hourly_activity）
CREATE TABLE IF NOT EXISTS ads.ads_traffic_hourly_activity_df (
    behavior_hour INT    COMMENT '小时 0-23（北京时间）',
    behavior_type STRING COMMENT '行为类型',
    behavior_cnt  BIGINT COMMENT '该时段该行为次数',
    pct           DOUBLE COMMENT '占该行为全时段的比例（4位小数）'
)
COMMENT 'ADS-05-24小时×行为活跃分布'
STORED AS ORC;

-- 06 类目 TOP10（基准 06_category_top）
CREATE TABLE IF NOT EXISTS ads.ads_traffic_category_top_df (
    rn         INT    COMMENT '购买量排名 1-10',
    category_id BIGINT COMMENT '类目ID',
    buy_cnt    BIGINT COMMENT '全期购买次数',
    buyer_cnt  BIGINT COMMENT '全期购买用户数',
    buy_pct    DOUBLE COMMENT '购买量占全部分类购买量的比例（4位小数）'
)
COMMENT 'ADS-06-购买量TOP10类目'
STORED AS ORC;

-- 07 复购（基准 07_repurchase）
CREATE TABLE IF NOT EXISTS ads.ads_traffic_repurchase_df (
    total_buyers    BIGINT COMMENT '全期购买用户数（≥1次）',
    repeat_buyers   BIGINT COMMENT '复购用户数（≥2次）',
    repurchase_rate DOUBLE COMMENT '复购率（4位小数）'
)
COMMENT 'ADS-07-复购率单行快照'
STORED AS ORC;

-- 08 用户行为深度分层（基准 08_user_segmentation）
CREATE TABLE IF NOT EXISTS ads.ads_traffic_segmentation_df (
    segment  STRING COMMENT '分层：已购买/加购未购买/仅收藏/仅浏览',
    user_cnt BIGINT COMMENT '该层用户数',
    pct      DOUBLE COMMENT '占全部用户比例（4位小数）'
)
COMMENT 'ADS-08-用户行为深度分层'
STORED AS ORC;

-- 09 用户RFM分层（用户级，一支一行）
CREATE TABLE IF NOT EXISTS ads.ads_traffic_rfm_df (
    user_id       BIGINT COMMENT '用户ID',
    recency_days  INT    COMMENT 'R：窗口末日−最近购买日天数；未购买为NULL',
    frequency     BIGINT COMMENT 'F：窗口内购买次数；未购买为0',
    r_score       INT    COMMENT 'R得分1~4（0天=4/1天=3/2~3天=2/其余=1）',
    f_score       INT    COMMENT 'F得分1~4（买家按购买次数四分位）',
    segment       STRING COMMENT '分层：重要价值/重要发展/重要保持/重要挽留/一般价值/一般挽留/未购买用户'
)
COMMENT 'ADS-09-用户RFM分层（RF两维，无金额维度）'
STORED AS ORC;

-- 10 次日留存（逐日）
CREATE TABLE IF NOT EXISTS ads.ads_traffic_retention_daily_df (
    stat_date       STRING COMMENT '同期群日期 yyyy-MM-dd（北京时间）',
    cohort_users    BIGINT COMMENT '当日活跃用户数（分母）',
    retained_users  BIGINT COMMENT '次日仍活跃用户数（分子）',
    next_day_rate   DOUBLE COMMENT '次日留存率（4位小数）'
)
COMMENT 'ADS-10-逐日次日留存'
STORED AS ORC;

-- 11 次日留存（按活跃天数分层）
CREATE TABLE IF NOT EXISTS ads.ads_traffic_retention_layer_df (
    layer             STRING COMMENT '活跃分层：1天/2~3天/4~5天/6~9天',
    users             BIGINT COMMENT '该层用户数',
    eligible_pairs    BIGINT COMMENT '可留存人-日对数（分母）',
    retained_pairs    BIGINT COMMENT '次日仍活跃人-日对数（分子）',
    next_day_rate     DOUBLE COMMENT '层内次日留存率（4位小数）',
    avg_eligible_days DOUBLE COMMENT '人均可留存的活跃天数'
)
COMMENT 'ADS-11-分层次日留存（人-日对口径）'
STORED AS ORC;

-- 12 用户路径汇总
CREATE TABLE IF NOT EXISTS ads.ads_traffic_path_summary_df (
    path             STRING COMMENT '路径：仅浏览/直接购买/先加购/先收藏',
    pairs            BIGINT COMMENT '该路径的用户-商品对数',
    purchased        BIGINT COMMENT '其中最终购买的对数',
    share_of_browsed DOUBLE COMMENT '占全部有浏览对数的比例（4位小数）',
    purchase_rate    DOUBLE COMMENT '该路径购买转化率（4位小数）'
)
COMMENT 'ADS-12-用户-商品路径汇总（按首次行为时序判定）'
STORED AS ORC;

-- 13 类目关联规则
CREATE TABLE IF NOT EXISTS ads.ads_traffic_association_rules_df (
    rn                  INT    COMMENT '按 lift 降序排名',
    antecedent_category BIGINT COMMENT '前项类目ID',
    consequent_category BIGINT COMMENT '后项类目ID',
    support             DOUBLE COMMENT '支持度：同时含两类的篮子/总篮子',
    confidence          DOUBLE COMMENT '置信度：含前项中同时含后项的比例',
    lift                DOUBLE COMMENT '提升度：置信度/后项基准概率',
    pair_baskets        BIGINT COMMENT '同时含两类的篮子数',
    antecedent_baskets  BIGINT COMMENT '含前项的篮子数',
    consequent_baskets  BIGINT COMMENT '含后项的篮子数',
    total_baskets       BIGINT COMMENT '有效购物篮总数（购买≥2类目的用户数）'
)
COMMENT 'ADS-13-类目关联规则Top100（2-项集精确计数）'
STORED AS ORC;