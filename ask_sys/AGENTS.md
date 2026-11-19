# Agent 实施规范（AGENTS.md）

> 适用：在 `ask_sys/` 实施 LangGraph 混合编排的 Agent。
> 先读 `ask_sys/plan.md`（方案、接口、任务卡、验收），本文件只讲**怎么干、什么不能干**。

---

## 1. 一分钟上下文

- 这是一个淘宝用户行为数仓项目：Hive 建仓 → PostgreSQL 镜像（19 张 `dw_*`）→ 元数据 `meta.*` → 四分支问数（pipeline 已上线，8/8 通过）。
- 本次任务：在 `ask_sys/ver_langgraph/` 新增 LangGraph 编排，与 `ver_pipeline` 并存；默认引擎不变。
- 共享能力已抽到 `ask_sys/common/`：`nl2sql`（唯一取数链路）、`knowledge`、`routing`、`db`、`env`、`llm`、`text`。
- 模型 `qwen3.8-flash` 已实测支持原生 `tool_calls`；LangGraph 1.2.x 可装。

---

## 2. 文件所有权

### 允许新增/修改

```
requirements-graph.txt
ask_sys/ver_langgraph/**            # state/tools/nodes/graph/__init__
ask_sys/dev/probe_graph.py
ask_sys/verify/graph.py
ask_sys/cli.py                      # 仅加 --engine 参数
scripts/ask_api.py                  # 仅加 engine 参数与 /ask/stream
web/index.html                      # 仅加引擎切换与 SSE 消费
README.md                           # 仅加两种编排对照
docs/learning/13-*.md               # 新笔记（本地文档）
```

### 禁止修改

```
ask_sys/common/**                   # 既有行为；确需增量必须先报告
ask_sys/ver_pipeline/**             # 参照实现，不改
sql/  docker/  docker-compose.yml   # 数据平台
hive-data/  export/  verify_out/    # 运行时数据
.env                                # 凭据；位置也必须在仓库根
scripts/build_warehouse.sh 等数仓脚本
assets/  web 的既有布局             # 除非任务卡明确要求
```

> 工作树里可能有用户未提交的改动（UI、截图、重构）。**不要回滚、不要覆盖、不要 `git checkout --`**。

---

## 3. 硬性规则

1. **取数只能走 `nl2sql.ask`**：召回 → guard → `dw_ro` → 30s 角色超时。禁止新增 raw SQL 工具。
2. **工具永不抛异常**：失败返回 `{"error": "..."}` 作为观察。
3. **不复制 `common` 实现**：graph 只调用，不抄 `ask`/`knowledge` 的代码。
4. **路径只从 `ask_sys.common.env.REPO` 取**；禁止 `sys.path.insert`（`scripts/ask_api.py` 入口除外）。
5. **不打印、不提交任何密钥**；`.env` 只读。
6. **所有 LLM/工具调用必须有超时**；循环必须有上限。
7. **不提交、不推送**，除非用户明确要求。
8. **不运行 `docker compose down -v`**；不重建数据、不重导 TSV。
9. 中文注释、中文日志，风格与现有 `ask_sys` 一致（短句、说清“为什么”）。
10. 命令一律用 `D:\pythonS\anaconda\python.exe`（系统 `python` 可能是 Store 占位）。

---

## 4. 开工流程（每张任务卡）

1. 读 `plan.md` 的对应任务卡（T0~T5）与验收；
2. 用 3~5 行复述：要改哪些文件、复用什么接口、验收命令是什么；
3. 只实施**一张卡**，不要顺手做下一张；
4. 先跑静态闸，再跑该卡的验收；
5. 在最终回复里给出：改动文件 / 执行命令 / 关键输出 / 偏差 / 下一步；
6. 验收不过，最多修复两次；仍不过就**停止并报告**，不要扩大改动。

---

## 5. 通用验收命令

```bash
cd D:/github_offline/data_analysis/User_Behavier_Analysis_Warehouse

# 编译
/d/pythonS/anaconda/python.exe -m py_compile ask_sys/ver_langgraph/*.py

# 现有 pipeline 不得回归（每张卡都要跑）
/d/pythonS/anaconda/python.exe -m ask_sys.verify.branches      # 8/8 PASS

# 单题冒烟
/d/pythonS/anaconda/python.exe -m ask_sys.cli --engine graph "12-02 的 DAU 是多少"

# 服务（改 API/前端后）
/d/pythonS/anaconda/python.exe scripts/ask_api.py
curl -s http://127.0.0.1:5001/health
```

注意：服务已在 5001 运行时，改代码后需要重启；不要杀掉其它无关进程。

---

## 6. 各阶段闸（摘要）

| 卡 | 必须通过 |
|---|---|
| T0 | 三个探针全部成功；打印 `tool_calls` 名称与参数；失败即停 |
| T1 | 5 个非 agent 题分支与 pipeline 一致；`verify.branches` 8/8 |
| T2 | 3 个多步用例（工具次数 + 关键数字）；循环上限 ≤4 轮 |
| T3 | `curl -N /ask/stream` 收到 node/tool/tool_end/result；pipeline 响应不变 |
| T4 | 浏览器 graph 模式能看到真实节点；pipeline 模式与旧截图一致 |
| T5 | 全部用例 + 安全探针；README 对照表可复现 |

验收口径与用例数字见 `plan.md` 第 7 节，不要在实现里改验收标准。

---

## 7. 停止条件（必须上报，不得自行绕过）

1. T0 工具探针失败（需改协议）；
2. 需要修改 `common` 的既有函数或行为；
3. 需要改数据库对象 / compose / `.env`；
4. agent 实测超出预算（4 轮 / 8k / 90s）且调提示词无效；
5. 同一验收连续两次修复失败；
6. 发现工作树里有与你改动冲突的用户修改。

---

## 8. 回报模板

```
【任务卡】T2
【改动】ask_sys/ver_langgraph/{tools.py,nodes.py,state.py}
【复用】nl2sql.ask / knowledge.search / db.q
【验证】
  - py_compile：OK
  - verify.branches：8/8 PASS
  - 多步用例：3/3（≥2 次 query_data，数字命中）
【偏差】agent 首轮偶尔直接答（未调工具），已在提示词加约束
【风险】单题最长 47s，仍在 90s 预算内
【下一步】T3
```

---

## 9. 交付定义（Definition of Done）

- 四分支在图里全部可用，快路径与 pipeline 分支/行数一致；
- agent 至少 2 次取数、答案含真实数字、循环有上限；
- `/ask` 支持 `engine`，默认 pipeline 行为零变化；
- `/ask/stream` 事件可被浏览器消费；
- 安全探针全拦住，`dw_ro` 仍只读；
- README 有对照表，笔记 13 记录踩坑；
- 未提交任何密钥、数据、运行时产物。
