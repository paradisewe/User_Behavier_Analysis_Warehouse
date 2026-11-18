-- A2 元数据知识库：表 / 字段 / 指标 / 字段-指标映射
-- 与镜像同库不同 schema：meta.* 描述 public.dw_* 的口径，B 阶段的检索与 SQL 生成都读它
BEGIN;

CREATE SCHEMA IF NOT EXISTS meta;

-- 1) 表清单与契约
CREATE TABLE IF NOT EXISTS meta.table_info (
    table_name    TEXT PRIMARY KEY,
    layer         TEXT NOT NULL CHECK (layer IN ('DWS', 'ADS')),
    source_table  TEXT NOT NULL,
    table_comment TEXT NOT NULL,
    grain         TEXT NOT NULL,
    served_for    TEXT NOT NULL,
    primary_key   TEXT NOT NULL,
    row_count     BIGINT NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE meta.table_info IS '镜像表清单：粒度/下游用途/主键/行数快照（表级契约）';
COMMENT ON COLUMN meta.table_info.served_for IS '服务哪类问题——契约字段，防止建表时漏维度（8c 教训）';

-- 2) 字段级元数据
CREATE TABLE IF NOT EXISTS meta.column_info (
    table_name     TEXT NOT NULL,
    column_name    TEXT NOT NULL,
    ordinal        INTEGER NOT NULL,
    data_type      TEXT NOT NULL,
    hive_type      TEXT NOT NULL,
    comment        TEXT NOT NULL,
    is_nullable    BOOLEAN NOT NULL,
    is_primary_key BOOLEAN NOT NULL,
    PRIMARY KEY (table_name, column_name)
);
COMMENT ON TABLE meta.column_info IS '镜像字段：PG 实际类型 + Hive 原始类型 + 中文注释（单一事实来源）';

-- 3) 指标口径
CREATE TABLE IF NOT EXISTS meta.metric_info (
    metric_id    TEXT PRIMARY KEY,
    metric_name  TEXT NOT NULL,
    definition   TEXT NOT NULL,
    formula      TEXT NOT NULL,
    grain        TEXT NOT NULL,
    source_table TEXT NOT NULL,
    value_column TEXT NOT NULL,
    unit         TEXT NOT NULL,
    time_window  TEXT NOT NULL,
    caveat       TEXT NOT NULL,
    aliases      TEXT NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE meta.metric_info IS '指标口径资产：定义/公式/粒度/窗口/坑（问数答错的一半原因在口径不清）';

-- 4) 字段-指标映射（检索入口）
CREATE TABLE IF NOT EXISTS meta.column_metric (
    metric_id   TEXT NOT NULL,
    table_name  TEXT NOT NULL,
    column_name TEXT NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('value', 'dimension')),
    PRIMARY KEY (metric_id, table_name, column_name)
);
COMMENT ON TABLE meta.column_metric IS '指标到字段的映射：问「复购率」应召回哪个表的哪几列';
CREATE INDEX IF NOT EXISTS idx_cm_column ON meta.column_metric (table_name, column_name);
CREATE INDEX IF NOT EXISTS idx_cm_metric ON meta.column_metric (metric_id);

COMMIT;
