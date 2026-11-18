BEGIN;

-- ============================================================
-- DWS 层
-- ============================================================

-- 01 日×行为（分区列 stat_date 镜像为普通列并作主键）
CREATE TABLE IF NOT EXISTS dw_dws_traffic_platform_1d_df (
    stat_date    DATE   NOT NULL,
    pv_cnt       BIGINT,
    fav_cnt      BIGINT,
    cart_cnt     BIGINT,
    buy_cnt      BIGINT,
    uv           BIGINT,
    buy_user_cnt BIGINT,
    PRIMARY KEY (stat_date)
);
COMMENT ON TABLE dw_dws_traffic_platform_1d_df IS 'DWS-平台日粒度行为汇总：各行为次数/UV/购买用户数，服务每日趋势与DAU分析';

-- 02 小时×行为
CREATE TABLE IF NOT EXISTS dw_dws_traffic_hour_df (
    behavior_hour INTEGER     NOT NULL,
    behavior_type VARCHAR(32) NOT NULL,
    behavior_cnt  BIGINT,
    PRIMARY KEY (behavior_hour, behavior_type)
);
COMMENT ON TABLE dw_dws_traffic_hour_df IS 'DWS-小时×行为全量汇总';

-- 03 用户粒度
CREATE TABLE IF NOT EXISTS dw_dws_traffic_user_summary_df (
    user_id           BIGINT NOT NULL,
    pv_cnt            BIGINT,
    fav_cnt           BIGINT,
    cart_cnt          BIGINT,
    buy_cnt           BIGINT,
    active_days       BIGINT,
    first_active_date DATE,
    last_active_date  DATE,
    first_buy_date    DATE,
    last_buy_date     DATE,
    PRIMARY KEY (user_id)
);
COMMENT ON TABLE dw_dws_traffic_user_summary_df IS 'DWS-用户粒度全量汇总-一人一行';

-- 04 类目粒度
CREATE TABLE IF NOT EXISTS dw_dws_traffic_category_df (
    category_id BIGINT NOT NULL,
    pv_cnt      BIGINT,
    fav_cnt     BIGINT,
    cart_cnt    BIGINT,
    buy_cnt     BIGINT,
    buyer_cnt   BIGINT,
    PRIMARY KEY (category_id)
);
COMMENT ON TABLE dw_dws_traffic_category_df IS 'DWS-类目粒度全量汇总-一类一行';

-- 05 用户×活跃日
CREATE TABLE IF NOT EXISTS dw_dws_traffic_user_active_day_df (
    user_id   BIGINT NOT NULL,
    stat_date DATE   NOT NULL,
    PRIMARY KEY (user_id, stat_date)
);
CREATE INDEX IF NOT EXISTS idx_uad_date ON dw_dws_traffic_user_active_day_df (stat_date);
COMMENT ON TABLE dw_dws_traffic_user_active_day_df IS 'DWS-用户×活跃日全量汇总';

-- 06 用户×购买类目（粒度：用户×类目×日）
CREATE TABLE IF NOT EXISTS dw_dws_traffic_user_category_buy_df (
    user_id     BIGINT NOT NULL,
    category_id BIGINT NOT NULL,
    stat_date   DATE   NOT NULL,
    buy_cnt     BIGINT,
    PRIMARY KEY (user_id, category_id, stat_date)
);
CREATE INDEX IF NOT EXISTS idx_ucb_cat  ON dw_dws_traffic_user_category_buy_df (category_id);
COMMENT ON TABLE dw_dws_traffic_user_category_buy_df IS 'DWS-用户×购买类目×日汇总（关联规则购物篮原料）';

-- ============================================================
-- ADS 层
-- ============================================================

-- 01 行为分布
CREATE TABLE IF NOT EXISTS dw_ads_traffic_behavior_dist_df (
    behavior_type VARCHAR(32) NOT NULL,
    behavior_cnt  BIGINT,
    pct           DOUBLE PRECISION,
    PRIMARY KEY (behavior_type)
);
COMMENT ON TABLE dw_ads_traffic_behavior_dist_df IS 'ADS-01-全期行为类型分布';

-- 02 用户级漏斗
CREATE TABLE IF NOT EXISTS dw_ads_traffic_user_funnel_df (
    stage          VARCHAR(32) NOT NULL,
    user_cnt       BIGINT,
    from_view_rate DOUBLE PRECISION,
    PRIMARY KEY (stage)
);
COMMENT ON TABLE dw_ads_traffic_user_funnel_df IS 'ADS-02-用户级行为漏斗（用户发生过该行为即计入）';

-- 03 用户×商品级顺序漏斗
CREATE TABLE IF NOT EXISTS dw_ads_traffic_item_funnel_df (
    transition VARCHAR(32) NOT NULL,
    pair_cnt   BIGINT,
    rate       DOUBLE PRECISION,
    PRIMARY KEY (transition)
);
COMMENT ON TABLE dw_ads_traffic_item_funnel_df IS 'ADS-03-用户-商品级顺序漏斗（首次行为时间先后判定）';

-- 04 每日趋势
CREATE TABLE IF NOT EXISTS dw_ads_traffic_daily_trend_df (
    stat_date    DATE NOT NULL,
    uv           BIGINT,
    buy_cnt      BIGINT,
    buy_user_cnt BIGINT,
    PRIMARY KEY (stat_date)
);
COMMENT ON TABLE dw_ads_traffic_daily_trend_df IS 'ADS-04-每日活跃与购买趋势';

-- 05 时段活跃
CREATE TABLE IF NOT EXISTS dw_ads_traffic_hourly_activity_df (
    behavior_hour INTEGER     NOT NULL,
    behavior_type VARCHAR(32) NOT NULL,
    behavior_cnt  BIGINT,
    pct           DOUBLE PRECISION,
    PRIMARY KEY (behavior_hour, behavior_type)
);
COMMENT ON TABLE dw_ads_traffic_hourly_activity_df IS 'ADS-05-24小时×行为活跃分布';

-- 06 类目 TOP10
CREATE TABLE IF NOT EXISTS dw_ads_traffic_category_top_df (
    rn          INTEGER NOT NULL,
    category_id BIGINT,
    buy_cnt     BIGINT,
    buyer_cnt   BIGINT,
    buy_pct     DOUBLE PRECISION,
    PRIMARY KEY (rn)
);
COMMENT ON TABLE dw_ads_traffic_category_top_df IS 'ADS-06-购买量TOP10类目';

-- 07 复购（单行快照，snapshot_id 固定为 1）
CREATE TABLE IF NOT EXISTS dw_ads_traffic_repurchase_df (
    snapshot_id     SMALLINT NOT NULL DEFAULT 1,
    total_buyers    BIGINT,
    repeat_buyers   BIGINT,
    repurchase_rate DOUBLE PRECISION,
    PRIMARY KEY (snapshot_id)
);
COMMENT ON TABLE dw_ads_traffic_repurchase_df IS 'ADS-07-复购率单行快照';

-- 08 用户行为深度分层
CREATE TABLE IF NOT EXISTS dw_ads_traffic_segmentation_df (
    segment  VARCHAR(32) NOT NULL,
    user_cnt BIGINT,
    pct      DOUBLE PRECISION,
    PRIMARY KEY (segment)
);
COMMENT ON TABLE dw_ads_traffic_segmentation_df IS 'ADS-08-用户行为深度分层';

-- 09 用户RFM分层（用户级，一支一行）
CREATE TABLE IF NOT EXISTS dw_ads_traffic_rfm_df (
    user_id      BIGINT      NOT NULL,
    recency_days INTEGER,
    frequency    BIGINT      NOT NULL,
    r_score      INTEGER,
    f_score      INTEGER,
    segment      VARCHAR(32) NOT NULL,
    PRIMARY KEY (user_id)
);
CREATE INDEX IF NOT EXISTS idx_rfm_segment ON dw_ads_traffic_rfm_df (segment);
COMMENT ON TABLE dw_ads_traffic_rfm_df IS 'ADS-09-用户RFM分层（RF两维，无金额维度）';

-- 10 次日留存（逐日）
CREATE TABLE IF NOT EXISTS dw_ads_traffic_retention_daily_df (
    stat_date      DATE NOT NULL,
    cohort_users   BIGINT,
    retained_users BIGINT,
    next_day_rate  DOUBLE PRECISION,
    PRIMARY KEY (stat_date)
);
COMMENT ON TABLE dw_ads_traffic_retention_daily_df IS 'ADS-10-逐日次日留存';

-- 11 次日留存（按活跃天数分层）
CREATE TABLE IF NOT EXISTS dw_ads_traffic_retention_layer_df (
    layer             VARCHAR(32) NOT NULL,
    users             BIGINT,
    eligible_pairs    BIGINT,
    retained_pairs    BIGINT,
    next_day_rate     DOUBLE PRECISION,
    avg_eligible_days DOUBLE PRECISION,
    PRIMARY KEY (layer)
);
COMMENT ON TABLE dw_ads_traffic_retention_layer_df IS 'ADS-11-分层次日留存（人-日对口径）';

-- 12 用户路径汇总
CREATE TABLE IF NOT EXISTS dw_ads_traffic_path_summary_df (
    path             VARCHAR(32) NOT NULL,
    pairs            BIGINT,
    purchased        BIGINT,
    share_of_browsed DOUBLE PRECISION,
    purchase_rate    DOUBLE PRECISION,
    PRIMARY KEY (path)
);
COMMENT ON TABLE dw_ads_traffic_path_summary_df IS 'ADS-12-用户-商品路径汇总（按首次行为时序判定）';

-- 13 类目关联规则
CREATE TABLE IF NOT EXISTS dw_ads_traffic_association_rules_df (
    rn                  INTEGER NOT NULL,
    antecedent_category BIGINT,
    consequent_category BIGINT,
    support             DOUBLE PRECISION,
    confidence          DOUBLE PRECISION,
    lift                DOUBLE PRECISION,
    pair_baskets        BIGINT,
    antecedent_baskets  BIGINT,
    consequent_baskets  BIGINT,
    total_baskets       BIGINT,
    PRIMARY KEY (rn)
);
CREATE INDEX IF NOT EXISTS idx_ant ON dw_ads_traffic_association_rules_df (antecedent_category);
CREATE INDEX IF NOT EXISTS idx_cons ON dw_ads_traffic_association_rules_df (consequent_category);
COMMENT ON TABLE dw_ads_traffic_association_rules_df IS 'ADS-13-类目关联规则Top100（2-项集精确计数）';

COMMIT;
