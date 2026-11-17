# 淘宝用户行为数仓（1 亿行 · Docker 单容器）

> 把 3.42 GB 原始行为日志建成 **ODS → DWD → DWS → ADS** 四层数仓，13 张结果表回答 13 个业务问题，
> 并与一条**独立的 DuckDB 管道**交叉对账：A 组 53 项指标全部 `diff = 0`，B 组 55 项与上游抽样基准做结构对照——**108 项全 PASS**。

技术栈：`Docker` · `Apache Hive 4.0.0（单容器 / Tez 本地模式）` · `Hive SQL` · `Derby（命名卷持久化）` · `Bash` · `Python`

---

## 1. 项目背景

数据来自阿里天池「淘宝用户行为数据集」：约 **1 亿行**用户行为流水，字段为
`user_id, item_id, category_id, behavior_type(pv/fav/cart/buy), timestamp`，核心观测窗口 **2017-11-25 ~ 12-03**。

> 🔗 **上游分析项目**：[paradisewe/user-behavior-analysis](https://github.com/paradisewe/user-behavior-analysis)
> 本项目是它的**数仓化 / 工程化版本**——同一份数据、同一套指标，从「分析脚本」升级为「可复现的四层数仓」；
> 对账 A 组（53 项）基准来自上游项目的 DuckDB 全量管道；B 组（55 项）用它的 5% 抽样结论做结构对照。

| 指标 | 数值 |
|---|---|
| 原始文件 | 3.42 GB，100,150,807 行，无表头 |
| 去重后明细 | 100,147,758 行 |
| 用户数 | 987,993 |
| 商品类目数 | 9,439 |
| 用户-商品对 | 75,967,699 |

项目目标不是「跑几个分析」，而是搭一套**可复现、可对账、可重建**的数仓工程：

- 任意机器 `docker compose up` + 一条脚本即可从 CSV 重建全仓
- 每一层都有**内部一致性校验**，每一张 ADS 表都和独立管道**跨管道对账**
- 全部中文 COMMENT / 指标口径落文档，为二期「AI 问数」提供元数据地基

---

## 2. 核心结论（ADS 层产出）

| # | 业务问题 | 结果 |
|---|---|---|
| 01 | 行为类型分布 | pv 89,713,247（89.58%）· cart 5,530,446（5.52%）· fav 2,888,258（2.88%）· buy 2,015,807（2.01%） |
| 02 | 用户级漏斗 | 浏览 984,111 → 加购 738,996（75.09%）→ 购买 672,404（**68.33%**） |
| 03 | 用户-商品级顺序漏斗 | 浏览→加购 **1.07%**｜浏览→购买 **1.40%**｜加购→购买 **6.06%**（约 5.7 倍杠杆） |
| 04 | 每日趋势 | 日均 DAU 约 71 万；**12-02 峰值 970,401、12-03 966,977**，较前 7 日基线高约 31%~37% |
| 05 | 时段活跃 | 晚间 **21~22 点**为行为高峰，凌晨 4~5 点最低 |
| 06 | 购买 TOP 类目 | **1464116**（34,589 次购买 / 29,953 位买家）领跑 |
| 07 | 复购率 | 购买用户 672,404，其中 **443,858 人复购（66.01%）** |
| 08 | 用户行为深度分层 | 已购买 68.06% ｜ 加购未购买 21.04% ｜ 仅浏览 6.03% ｜ 仅收藏 4.87% |
| 09 | RFM 用户分层 | 重要价值 **19.39%** ｜ 重要保持 14.64% ｜ 一般挽留 12.08% ｜ 重要挽留 10.56% ｜ 重要发展 6.45% ｜ 一般价值 4.93% ｜ 未购买 31.94% |
| 10 | 次日留存（逐日） | 基线 77.7%~79.6%；**12-01 跳升至 98.25%**、12-02 为 97.98%（复现上游「12-02 异常」结论） |
| 11 | 次日留存（活跃分层） | 6~9 天层 **88.05%** ｜ 4~5 天层 59.22% ｜ 2~3 天层 29.59% ｜ 1 天层 0% |
| 12 | 用户-商品路径 | 仅浏览 93.40% ｜ 先加购 3.61%（购买率 **9.50%**）｜ 先收藏 1.62%（7.98%）｜ 直接购买 1.37% |
| 13 | 类目关联规则 | Top100（按 lift）；有效购物篮 **421,004**，为抽样 20,970 的 **20.08 倍**（抽样代表性验证） |

> 注：01/02/03/06/07/08 与 DuckDB 全量管道**逐指标精确相等**；04/05 因时区口径不同（北京时间 vs UTC）只对趋势形状；
> 09~13 与上游 5% 抽样基准比结构（占比/比率/均值），全部落在容差内。

---

## 3. 数仓架构

```
UserBehavior.csv（3.42 GB / 1 亿行）
        │  装载（外部表读 CSV → ORC，按【装载日期】分区）
        ▼
ODS 贴源层（2 张表 · 原样镜像，不清洗）
        │  清洗：规范化 + 时间窗过滤 + 非空 + 整行去重（按【业务日期】分区）
        ▼
DWD 明细层（1 张事实表 · 一行一次行为）
        │  公共指标聚合（指标只算一次）
        ▼
DWS 汇总层（7 张：平台日粒度 / 时段 / 用户 / 类目 / 用户×商品首行为 / 人-日对 / 用户×类目购买）
        │  面向业务问题的最终加工（只读 DWS，禁止跨层）
        ▼
ADS 应用层（13 张：一表一问题）
        │
        ├─ verify.sh（17 项：结构 + 内部一致性）
        └─ verify_compare.py（108 项：A 组精确对账 + B 组结构对照）
```

**单容器 Hive 的运行模型**：HiveServer2 + Tez 本地执行 + 内嵌 Derby 元数据 + 挂载目录做存储，
不需要 Hadoop 集群——「集群不是 Hive 的必需品，是规模化的产物」。

---

## 4. 表清单（共 23 张）

### ODS（2 张）

| 表 | 说明 | 分区 |
|---|---|---|
| `ods.ods_stg_user_behavior_csv` | 外部表，CSV 解析通道（全 STRING） | 无 |
| `ods.ods_user_behavior_df` | 贴源镜像，ORC | `dt` = **装载日期** |

### DWD（1 张）

| 表 | 说明 | 分区 |
|---|---|---|
| `dwd.dwd_traffic_user_behavior_di` | 清洗后明细（含北京时间派生列：time/date/hour） | `dt` = **业务日期**（北京时间） |

### DWS（7 张）

| 表 | 粒度 | 服务的 ADS |
|---|---|---|
| `dws_traffic_platform_1d_df` | 日 | 01 / 04 |
| `dws_traffic_hour_df` | 小时 × 行为 | 05 |
| `dws_traffic_user_summary_df` | 用户（全期） | 02 / 07 / 08 |
| `dws_traffic_category_df` | 类目（全期） | 06 |
| `dws_traffic_user_item_df` | 用户 × 商品（首次行为时间戳） | 03 / 12 |
| `dws_traffic_user_active_day_df` | 用户 × 活跃日（人-日对） | 10 / 11 |
| `dws_traffic_user_category_buy_df` | 用户 × 类目 × 日（购物篮原子） | 13 |

### ADS（13 张）

`ads_traffic_behavior_dist_df` · `ads_traffic_user_funnel_df` · `ads_traffic_item_funnel_df` ·
`ads_traffic_daily_trend_df` · `ads_traffic_hourly_activity_df` · `ads_traffic_category_top_df` ·
`ads_traffic_repurchase_df` · `ads_traffic_segmentation_df` ·
`ads_traffic_rfm_df` · `ads_traffic_retention_daily_df` · `ads_traffic_retention_layer_df` ·
`ads_traffic_path_summary_df` · `ads_traffic_association_rules_df`

---

## 5. 关键设计决策

| 决策 | 原因 |
|---|---|
| **ODS 按装载日期分区，DWD 才按业务日期** | 贴源层业务字段不可信：原始数据含 3,000 条脏时间戳（1902~2037），按业务日期动态分区会**分区爆炸**并压垮元数据提交。分区键只能取「本层已可信」的字段（层级信任链） |
| **ODS 全 STRING 贴源，DWD 才定型** | 保留脏值原貌；CAST 失败变 NULL 但行不丢，DWD 再统一过滤 |
| **清洗 = 规范化 + 过滤 + 去重** | `lower(trim(behavior_type))` 规范化；时间窗 `1509494400 ~ 1514764800` + 键非空过滤；`ROW_NUMBER()` 整行去重（去 49 条重复） |
| **北京时间 +8** | 业务口径；`from_unixtime(ts + 28800)`。代价：与 UTC 基准的日/小时粒度有边界偏移，故 04/05 只对趋势 |
| **全链路幂等** | `CREATE IF NOT EXISTS` + `INSERT OVERWRITE`；容器重建后一条脚本整体恢复 |
| **Metastore 用命名卷 + 种子法** | Derby 拒绝「已存在但非合法库」的目录（XBM0J），空卷挂载必然启动失败；先把合法库种进卷才能持久化（生产建议改 PostgreSQL） |
| **双管道对账** | 同一份 CSV 用 Hive 与 DuckDB 两条独立实现，逐指标相减——比分看板更能证明正确性 |

---

## 6. 快速开始

**前置**：Docker Desktop（或 Linux Docker）、Git Bash / bash、Python 3（跑对账脚本）。

```bash
# 0) 首次或元数据卷被删后：初始化 Derby 元数据库（种子法，只需一次）
docker volume create hive-metastore
docker run --rm -u root -v hive-metastore:/seed --entrypoint sh apache/hive:4.0.0 -c "
  find /seed -mindepth 1 -delete
  rm -rf /tmp/mdb
  /opt/hive/bin/schematool -dbType derby -initOrUpgradeSchema \
    -url 'jdbc:derby:;databaseName=/tmp/mdb;create=true' >/dev/null
  cp -a /tmp/mdb/. /seed/
  rm -f /seed/db.lck /seed/dbex.lck
  chown -R hive:hive /seed"

# 1) 启动 Hive
docker compose up -d

# 2) 一键建仓（校验数据 → 探活 → ODS→DWD→DWS→ADS → 烟测）
bash scripts/build_warehouse.sh

# 3) 对账（结构/一致性 17 项 + 跨管道 108 项）
bash scripts/verify.sh
python scripts/verify_compare.py
```

**耗时**：首次建仓 30~60 分钟（主要花在 1 亿行 CSV → ORC 和 DWS 的 shuffle）；重跑 ETL 快于首次。

**数据未入库**（3.42 GB）：需自行从阿里天池下载 `UserBehavior.csv`。默认从同级仓库
`../User_behavier_analysis/raw_users_behavier_date/` 拷贝，也可用环境变量覆盖：

```bash
DATA_SRC=/path/to/UserBehavior.csv bash scripts/build_warehouse.sh
```

**对账基准**（DuckDB 结果）默认取 `../User_behavier_analysis/sql/results/`，同样可覆盖：

```bash
BASELINE_DIR=/path/to/results python scripts/verify_compare.py
```

**常见环境问题**：Git Bash 下 `docker exec` 传容器内绝对路径必须用 `bash -c "..."` 包一层
（MSYS 会把 `/opt/...` 改写成 `C:/Program Files/...`，报 `No FileSystem for scheme "C"`）。

---

## 7. 对账结果

```
verify.sh           17 项 PASS（数据质量 1 + 结构 4 + 一致性 12）   退出码 0
verify_compare.py   108 项 PASS（A 组 53 项 diff=0；B 组 55 项在容差内）  退出码 0
```

代表性指标（A 组：基线 = DuckDB 全量管道，Hive = 本数仓）：

| 指标 | 基线 | Hive | diff |
|---|---|---|---|
| pv 次数 | 89,713,247 | 89,713,247 | 0 |
| 用户-商品 浏览→加购 | 1.07% | 1.07% | 0 |
| 加购→购买 | 6.06% | 6.06% | 0 |
| 复购率 | 66.01% | 66.01% | 0 |
| 已购买用户占比 | 68.06% | 68.06% | 0 |

B 组（基线 = 上游 5% 抽样的分析结论，只比结构）：

| 指标 | 抽样基线 | 全量复算 | diff |
|---|---|---|---|
| RFM 重要价值占比 | 19.39% | 19.39% | 0 |
| 次日留存 6~9 天层 | 88.02% | 88.05% | 0.0003 |
| 路径：先加购占比 / 购买率 | 3.60% / 9.44% | 3.61% / 9.50% | ≤0.0006 |
| 有效购物篮（全量/抽样） | 20,970 | 421,004（20.08 倍） | — |

**核心对账数字**：`100,150,807（原始）− 3,000（脏时间戳）− 49（重复）= 100,147,758（DWD）`。

---

## 8. 踩坑记录（本项目的真实故障）

| 现象 | 根因 | 解决 |
|---|---|---|
| `No FileSystem for scheme "C"` | Git Bash（MSYS）把 `-f /opt/sql/...` 改写成 `C:/Program Files/...` | `docker exec hive4 bash -c "beeline ..."` 包一层 |
| 1 亿行 ETL 计算全绿，提交报 `MoveTask ... rolling back to savepoint` | 分区爆炸：ODS 用业务日期分区，3,000 条脏时间戳炸出数百个分区（1902~2037），巨型元数据事务压垮 Derby | ODS 改装载日期分区；业务日期分区下移到 DWD |
| 容器启动即死：`ERROR XBM0J: Directory ... already exists` | Docker 挂载会预建目录，Derby 拒绝在「已存在但非合法库」的目录上建库 | 命名卷 + **种子法**（临时目录建库 → 拷入卷 → chown） |
| `SHOW PARTITIONS` 正常但查询 0 行 | Hive `INSERT` **按位置**而非按列名映射，DDL 列序与 SELECT 列序错位 | SELECT 列序严格对齐 DDL；写完先数一遍列数 |
| 脚本「跑完了」但没有输出文件 | 空脚本 `exit 0`；`run_sql.py` 逐查询吞异常；`grep -v` 无匹配返回 1 叠加 `pipefail` | 验收看**副作用**（文件 mtime / 行数），不看退出码；`q()` 加 `\|\| true` 并用 `-s` 断言非空 |
| `verify_compare.py` 全部 `Hive=None` | 阶段 3 六条导出表名复制粘贴全写成 `behavior_dist` | 表名逐条核对；导出助手加空文件断言 |
| `verify.sh` 报「购物篮数不一致」，差 9,075 | 校验口径写成 `count(*)`（行数，含日期维度），生产是 `count(DISTINCT category_id)`（类目数） | 校验脚本必须复用与生产**完全相同**的集合定义；**假红灯和假绿灯同样有害** |

> 每个故障都留下了完整的排查记录（报错原文、验证命令、根因推理），整理在项目本地的学习笔记中，未随仓库发布。

---

## 9. 目录结构

```
User_Behavier_Analysis_Warehouse/
├── docker-compose.yml           # Hive 4.0.0 单容器：warehouse 绑定挂载 + metastore 命名卷
├── sql/
│   ├── ODS/ods_ddl.hql, ods_etl.hql
│   ├── DWD/dwd_ddl.hql, dwd_etl.hql
│   ├── DWS/dws_ddl.hql, dws_etl.hql
│   └── ADS/ads_ddl.hql, ads_etl.hql
├── scripts/
│   ├── build_warehouse.sh       # 一键建仓（数据校验 → 探活 → 四层 → 烟测）
│   ├── verify.sh                # 结构 + 内部一致性（17 项）
│   └── verify_compare.py        # 跨管道对账（108 项）
├── hive-data/                   # 运行时 ORC 数据（gitignore）
│   └── ods.db/ dwd.db/ dws.db/ ads.db/
├──（命名卷 hive-metastore）     # Derby 元数据库，挂载到容器 /opt/hive/metastore_db
└── verify_out/                  # 对账导出的 TSV（gitignore）
```

---

## 10. 二期 Roadmap · AI 问数

借鉴 [shopkeeper-agent](https://github.com/didilili/shopkeeper-agent) 的智能问数链路：

| 阶段 | 内容 |
|---|---|
| 服务层 | DWS/ADS 导出 **MySQL 镜像**，Agent 毫秒级查询 |
| 元数据知识库 | MySQL（结构化元数据）+ Qdrant（字段/指标向量）+ ES（字段取值检索） |
| 编排 | **LangGraph** 多阶段：抽关键词 → 多路召回 → 上下文补全 → SQL 生成 → EXPLAIN 校验 → 回改 → 执行 → 流式返回 |
| 增量 | 融合 Dify 版「问项目」RAG 分支；Dify 版定位为 MVP，叙事为「原型 → 工程化」 |
| **评测集** | 50 条问题（含多表 JOIN / 口径歧义 / 日期陷阱），指标 = 准确率 / 口径命中率 / 轮次 / 延迟；**失败案例写进 README** |

一期的价值：**Agent 的「眼睛」是元数据**——本项目的表 / 字段 / 指标口径已全部结构化，直接成为知识库语料。

---

## 11. 关联项目与数据来源

### 关联项目

| 项目 | 关系 |
|---|---|
| [user-behavior-analysis](https://github.com/paradisewe/user-behavior-analysis) | **上游分析项目**：Python 抽样管道（498 万行）+ DuckDB 全量 SQL + Streamlit 看板 + Dify 问数助手。本项目把它的结论工程化为四层数仓，并用它的 DuckDB 结果作为对账基准 |
| [shopkeeper-agent](https://github.com/didilili/shopkeeper-agent) | 二期 AI 问数参考：LangGraph + 元数据知识库 + NL2SQL 校验执行 |

**两个仓库的分工**：

```
user-behavior-analysis              User_Behavier_Analysis_Warehouse
（分析探索：数据说了什么）    ──→    （工程实现：怎么稳定、可复现地算出来）
  Python 抽样 / DuckDB 全量              Hive 四层 / 一键建仓 / 108 项对账
  Streamlit 看板 / Dify 问数              ADS 结果表 / 元数据地基（二期 Agent 用）
```

### 数据与致谢

- 数据：阿里天池 · 淘宝用户行为数据集（公开数据，仅用于学习）
- 方法论蓝本：本地 Olist 电商数仓项目（四层 48 表设计稿，未公开）
- 二期参考：[shopkeeper-agent](https://github.com/didilili/shopkeeper-agent)
