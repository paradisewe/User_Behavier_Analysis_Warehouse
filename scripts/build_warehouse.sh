#!/bin/bash
# 一键建仓：CSV → Docker Hive → ODS→DWD→DWS→ADS → 烟测
set -euo pipefail

# ---------- 配置集中区 ----------
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DATA_SRC="${DATA_SRC:-/d/github_offline/data_analysis/User_behavier_analysis/raw_users_behavier_date/UserBehavior.csv}"
DATA_DST="$REPO_DIR/hive-data/ods/user_behavior/UserBehavior.csv"
DATA_BYTES=3672347465
URL="jdbc:hive2://localhost:10000"
CONTAINER="hive4"
SQL_DIR="/opt/sql"
HQLS=( ODS/ods_ddl.hql ODS/ods_etl.hql DWD/dwd_ddl.hql DWD/dwd_etl.hql \
       DWS/dws_ddl.hql DWS/dws_etl.hql ADS/ads_ddl.hql ADS/ads_etl.hql )

# ---------- 1. 数据就位 ----------
echo "=============== 数据就位 ================"
mkdir -p "$(dirname "$DATA_DST")"
if [ -f "$DATA_DST" ]; then
    echo "数据文件已存在，跳过复制"
else
    cp -- "$DATA_SRC" "$DATA_DST"
    echo "已拷贝数据文件到 $DATA_DST"
fi

actual="$(stat -c%s -- "$DATA_DST")"
if [ "$actual" != "$DATA_BYTES" ]; then
    echo "字节数不符：期望 $DATA_BYTES，实际 $actual" >&2
    exit 1
fi
echo "字节数正确：$actual"

# ---------- 2. 起容器 ----------
echo "=============== 启动 Hive 容器 ================"
docker compose -f "$REPO_DIR/docker-compose.yml" up -d

# ---------- 3. 探活 ----------
echo "=============== 等待 HiveServer2 就绪 ================"
ready=0
for i in $(seq 1 30); do
    if docker exec "$CONTAINER" bash -c \
        "beeline -u '$URL' -n hive -e 'SELECT 1;'" >/dev/null 2>&1; then
        echo "HiveServer2 就绪"
        ready=1
        break
    fi
    echo "等待 HiveServer2... ($i/30)"
    sleep 10
done
if [ "$ready" -ne 1 ]; then
    echo "HiveServer2 在 300 秒内未就绪，退出" >&2
    exit 1
fi

# ---------- 4. 逐层执行 DDL/ETL ----------
echo "=============== 执行 DDL/ETL ================"
for f in "${HQLS[@]}"; do
    echo "== $f =="
    docker exec "$CONTAINER" bash -c \
        "beeline -u '$URL' -n hive --force=false -f '$SQL_DIR/$f'"
done

# ---------- 5. ADS 烟测 ----------
echo "=============== ADS 烟测 ================"
docker exec "$CONTAINER" bash -c \
    "beeline -u '$URL' -n hive --force=false -e 'SELECT * FROM ads.ads_traffic_repurchase_df LIMIT 10;'"
docker exec "$CONTAINER" bash -c \
    "beeline -u '$URL' -n hive --force=false -e 'SELECT * FROM ads.ads_traffic_item_funnel_df LIMIT 10;'"
docker exec "$CONTAINER" bash -c \
    "beeline -u '$URL' -n hive --force=false -e 'SELECT * FROM ads.ads_traffic_behavior_dist_df LIMIT 10;'"

echo "=============== 全部完成 ================"



















































































# 逐行注释版。
# ## 0. 文件头

# ```bash
# #!/bin/bash
# # 指定解释器为 bash。数组、[[ ]]、"${arr[@]}" 都是 bash 特性，不能用 sh。
# # 常见错误：写成 #!/bin/sh，然后脚本里用数组，dash 直接报 Syntax error。

# set -euo pipefail
# # -e：任何命令返回非 0，脚本立即退出（fail fast）。
# # -u：使用未定义变量时报错退出，防止 $DATA_SRC 拼错成空字符串还继续跑。
# # -o pipefail：管道中任意一段失败，整条管道算失败；不加的话只看最后一个命令。
# # 常见错误：只写 set -e。管道 `a | b` 里 a 失败、b 成功，脚本不会停。
# # 常见错误：set -u 下遍历空数组 "${ARR[@]}"，老 bash 会报 unbound variable；bash 4.4+ 才安全。
# ```

# ---

# ## 1. 配置集中区

# ```bash
# REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# # 取脚本所在目录的上一级，转成绝对路径。脚本放 scripts/ 下，这里就是仓库根。
# # 常见错误：写成 REPO_DIR="$(dirname "$0")/.."，得到的是相对路径，换个目录调用就错。
# # 常见错误：写成 REPO_DIR="$ (dirname ...)"，$ 和 ( 之间有空格，不是命令替换。
# # 常见错误：脚本被 source 时 $0 是调用者 shell，不是脚本本身；要兼容得用 ${BASH_SOURCE[0]:-$0}。

# DATA_SRC="/d/github_offline/data_analysis/User_behavier_analysis/raw_users_behavier_date/UserBehavior.csv"
# # 源 CSV 的绝对路径。Git Bash 下 Windows 的 D:\ 写成 /d/。
# # 常见错误：写成 D:\github_offline\...，反斜杠在 shell 里是转义符，路径直接被破坏。

# DATA_DST="$REPO_DIR/hive-data/ods/user_behavior/UserBehavior.csv"
# # 目标路径：仓库根下的 hive-data/ods/...，这个目录通常要挂载进 Hive 容器。
# # 常见错误：写成相对路径 hive-data/...，脚本在别的目录被调用时就落到别处去了。

# DATA_BYTES=3672347465
# # 期望字节数，硬编码。约 3.4 GB。
# # 常见错误：写成 3,672,347,465 带逗号，shell 会当成三个参数或直接报错。

# URL="jdbc:hive2://localhost:10000"
# # beeline 连接串。注意这是在容器内执行时用的 localhost。
# # 常见错误：容器内 beeline 连 localhost 却以为连的是宿主机；如果 beeline 不在 hive4 容器里跑，localhost 含义就变了。

# CONTAINER="hive4"
# # 容器名，后面 docker exec 反复用。抽成变量避免拼错。
# # 常见错误：docker exec hive4 和 docker exec hive-4 混用，容器名不一致直接报 No such container。

# SQL_DIR="/opt/sql"
# # 容器内 SQL 文件所在目录，靠 docker-compose 的 volume 挂进去。
# # 常见错误：脚本里写 /opt/sql，compose 里却没挂载这个目录，beeline -f 报 File not found。

# HQLS=( ODS/ods_ddl.hql ODS/ods_etl.hql DWD/dwd_ddl.hql DWD/dwd_etl.hql \
#        DWS/dws_ddl.hql DWS/dws_etl.hql ADS/ads_ddl.hql ADS/ads_etl.hql )
# # bash 数组，按 ODS→DWD→DWS→ADS 顺序存 HQL 相对路径。
# # 常见错误：元素里有空格却不加引号，比如 "ODS/my ddl.hql"，会被拆成两个元素。
# # 常见错误：后面遍历写成 ${HQLS[@]} 不加引号，元素含空格或 * 会被拆分/展开。
# ```

# ---

# ## 2. 数据就位

# ```bash
# echo "=============== 数据就位 ================"
# # 分段打印，方便看日志定位到哪一步。
# # 常见错误：echo 里用单引号包了变量，比如 echo '$DATA_DST'，变量不会展开。

# mkdir -p "$(dirname "$DATA_DST")"
# # 建目标文件的父目录。-p 幂等：已存在不报错，多级不存在一起建。
# # 常见错误：写成 mkdir "$(dirname "$DATA_DST")"，目录已存在时返回非 0，set -e 直接退出。
# # 常见错误：用 "$DATA_DST/.." 取父目录。DATA_DST 是文件，文件后面接 /.. 不是有效目录。

# if [ -f "$DATA_DST" ]; then
# # 判断目标是否已经是普通文件。-f 只认普通文件，目录、断链、设备文件都为假。
# # 常见错误：写成 [ -f $DATA_DST ] 不加引号，路径含空格或变量为空时直接语法错/逻辑反。
# # 常见错误：写成 [-f "$DATA_DST" ]，[ 后面没空格，shell 找不到命令 [-f。
# # 常见错误：用 -e 代替 -f，目录也会被当成“已存在”，于是跳过复制。

#     echo "数据文件已存在，跳过复制"
#     # 幂等：重复跑脚本不重复拷贝 3.4 GB。
# else
#     cp -- "$DATA_SRC" "$DATA_DST"
#     # 复制，保留源文件。-- 防止路径以 - 开头被当成选项。
#     # 常见错误：写成 cp DATA_SRC DATA_DST，变量没加 $，复制的是两个字面量文件名。
#     # 常见错误：写成 cp $DATA_SRC | mv $DATA_SRC $DATA_DST，管道毫无意义，mv 还会把源文件移走。
#     # 常见错误：用 mv 代替 cp，第一次跑完源 CSV 就没了，第二次跑直接失败。
#     echo "已拷贝数据文件到 $DATA_DST"
# fi

# actual="$(stat -c%s -- "$DATA_DST")"
# # GNU stat：-c 指定格式，%s 输出字节数，只输出数字。-- 防止文件名以 - 开头。
# # 常见错误：macOS/BSD 上 -c%s 不认，要 stat -f%z；跨平台可改用 wc -c < "$DATA_DST"。
# # 常见错误：不加 2>/dev/null，文件不存在时 stat 的报错会混进 stdout 附近，干扰判断。
# # 常见错误：写成 "$(stat -c%s $DATA_DST)"，路径含空格时 stat 收到多个参数。

# if [ "$actual" != "$DATA_BYTES" ]; then
# # 字符串比较。两边都是纯数字，用 != 没问题；用 -ne 也行，但注意 shell 整数范围。
# # 常见错误：写成 [ "$actual" != $DATA_BYTES ]，右边不加引号，虽然这里不会错，但习惯不好。
# # 常见错误：写成 [ "$(stat ...)" = "$DATA_BYTES" ] && echo ok || echo bad，只打印不退出，脚本继续跑。
#     echo "字节数不符：期望 $DATA_BYTES，实际 $actual" >&2
#     # 错误信息走 stderr，不污染正常输出。
#     exit 1
#     # 校验失败必须退出。这是“数据不对就别往下建仓”的闸门。
#     # 常见错误：忘了 exit 1，后面拿错数据继续跑，最后 ADS 指标全错还找不到原因。
# fi
# echo "字节数正确：$actual"
# ```

# ---

# ## 3. 起容器

# ```bash
# echo "=============== 启动 Hive 容器 ================"

# docker compose -f "$REPO_DIR/docker-compose.yml" up -d
# # -f 指定 compose 文件，避免依赖当前工作目录；-d 后台运行。
# # 已在跑时 up -d 是幂等的：不会重建，只会确保服务在运行。
# # 常见错误：写成 docker-compose（老版本二进制），新环境可能只有 docker compose 子命令。
# # 常见错误：不写 -f，脚本在别的目录被调用时找不到 docker-compose.yml。
# # 常见错误：以为 up -d 返回 0 就等于 HiveServer2 能连了；容器起来到服务可用还有几十秒，所以要探活。
# ```

# ---

# ## 4. 探活循环

# ```bash
# echo "=============== 等待 HiveServer2 就绪 ================"

# ready=0
# # 标志变量。循环正常结束不代表成功，必须靠它区分“break 出来的”和“跑完 30 次仍失败”。
# # 常见错误：不设标志，for 循环跑完直接往下走，Hive 没起来也继续执行 HQL，报一堆连接错误。

# for i in $(seq 1 30); do
# # 循环 30 次。seq 是外部命令，也可以用 {1..30}（bash 内建，少 fork 一次）。
# # 常见错误：写成 for i in {1..30} 却在 sh 下跑，brace expansion 不生效。

#     if docker exec "$CONTAINER" bash -c \
#         "beeline -u '$URL' -n hive -e 'SELECT 1;'" >/dev/null 2>&1; then
#     # 在容器内跑一条最简单的 SELECT 1，成功即认为 HiveServer2 可服务。
#     # >/dev/null 2>&1：stdout 和 stderr 都丢弃，避免每次重试刷一屏连接错误。
#     # 顺序不能反：2>&1 >/dev/null 只丢 stdout，stderr 仍会打出来。
#     # 常见错误：只写 >/dev/null，stderr 照样刷屏。
#     # 常见错误：URL 外层用双引号又在内层不加单引号，$URL 在宿主机就被展开，容器内收到的是展开后的值——这里恰好也行，但嵌套引号一复杂就乱。
#     # 常见错误：docker exec 不加 bash -c，beeline 的参数被宿主机 shell 先解析，引号丢失。

#         echo "HiveServer2 就绪"
#         ready=1
#         break
#         # 成功就置标志并跳出。
#     fi

#     echo "等待 HiveServer2... ($i/30)"
#     sleep 10
#     # 每次失败等 10 秒，30 次约 300 秒。
#     # 常见错误：sleep 放错位置，成功 break 前也 sleep，白白多等 10 秒。
# done

# if [ "$ready" -ne 1 ]; then
# # 循环走完还没就绪 → 退出。
# # 常见错误：这里写成 [ "$ready" -eq 0 ] && echo timeout，只打印不 exit，后面继续跑。
#     echo "HiveServer2 在 300 秒内未就绪，退出" >&2
#     exit 1
# fi
# ```

# ---

# ## 5. 逐层执行 DDL/ETL

# ```bash
# echo "=============== 执行 DDL/ETL ================"

# for f in "${HQLS[@]}"; do
# # 遍历数组。必须 "${HQLS[@]}"：双引号保证每个元素独立一个词。
# # 常见错误：写 "${HQLS[*]}"，所有元素拼成一个字符串，只循环一次。
# # 常见错误：写 ${HQLS[@]} 不加引号，元素含空格/通配符会被拆分或展开。
# # 常见错误：写 $HQLS[@]，$HQLS 等价于 ${HQLS[0]}，结果变成“第一个元素 + 字面量 [@]”。

#     echo "== $f =="

#     docker exec "$CONTAINER" bash -c \
#         "beeline -u '$URL' -n hive --force=false -f '$SQL_DIR/$f'"
#     # --force=false 是关键：beeline 默认 --force=true，-f 执行多语句时中间失败也继续跑并最终返回 0。
#     # 不加这个参数，set -e 完全捕获不到 SQL 错误，脚本会“假装成功”。
#     # 常见错误：漏掉 --force=false，某层 ETL 报错但脚本一路绿灯跑到烟测。
#     # 常见错误：写成 -f $SQL_DIR/$f 不加引号，路径含空格时被拆参数。
#     # 常见错误：以为 docker exec 失败会传播——docker exec 返回容器内命令的退出码，beeline 非 0 时 docker exec 也非 0，set -e 才会生效；但前提是 beeline 真的返回非 0，所以 --force=false 不能少。
#     # 常见错误：HQL 文件里用了 Hive 不支持的语法，beeline 报错但被 --force=true 吞掉。
# done
# # 循环里 docker exec 是一条简单命令，任一失败 set -e 立即退出——这就是“任何一个失败脚本立即死”的保证。
# # 常见错误：把 docker exec 包进 if 或 || true，set -e 就失效了，失败被吞。
# ```

# ---

# ## 6. ADS 烟测

# ```bash
# echo "=============== ADS 烟测 ================"

# docker exec "$CONTAINER" bash -c \
#     "beeline -u '$URL' -n hive --force=false -e 'SELECT * FROM ads.ads_traffic_repurchase_df LIMIT 10;'"
# # 烟测第一张表：表存在、能查、有数据（有数据要人眼看输出）。
# # LIMIT 10 是为了快，不扫全表。
# # 常见错误：写成 SELECT * FROM ads_traffic_repurchase_df 漏了库前缀，表不在 default 库就报 Table not found。
# # 常见错误：用 -f 跑单条语句，多此一举；单条用 -e。
# # 常见错误：不加 --force=false，表不存在时 beeline 可能仍返回 0。

# docker exec "$CONTAINER" bash -c \
#     "beeline -u '$URL' -n hive --force=false -e 'SELECT * FROM ads.ads_traffic_item_funnel_df LIMIT 10;'"
# # 烟测第二张表。注意你原稿里后两个表名没写 ads. 前缀，这里补上了。
# # 常见错误：表实际建在别的库（比如 default），这里写 ads. 会报错；以实际 DDL 为准。

# docker exec "$CONTAINER" bash -c \
#     "beeline -u '$URL' -n hive --force=false -e 'SELECT * FROM ads.ads_traffic_behavior_dist_df LIMIT 10;'"
# # 烟测第三张表。
# # 常见错误：三张表名复制粘贴时漏改，三条查同一张表，另外两张没测到。

# echo "=============== 全部完成 ================"
# # 走到这里说明：数据校验通过、容器起来、Hive 就绪、8 个 HQL 全成功、3 张 ADS 表可查。
# # 注意：烟测只证明“能查”，不证明“指标算得对”；数值正确性要人工抽查或另写校验 SQL。
# ```

# ---

# ## 7. 收尾检查清单

# ```bash
# # 写完先做语法检查，不执行：
# bash -n scripts/build_warehouse.sh
# # 能抓：缺 fi、[ 缺空格、引号不配对、语法级错误。
# # 抓不到：cp DATA_SRC 没加 $、逻辑判断写反、--force=false 漏了——这些要靠人眼和 shellcheck。

# shellcheck scripts/build_warehouse.sh
# # 能抓：SC2086 变量没加引号、SC2154 未定义变量、SC2164 cd 没检查、SC2015 A&&B||C 陷阱等。
# # 对手敲易错体质，强烈建议每次改完都跑一遍。
# ```

# ---

# ## 最需要盯的 5 个“常见错误”

# 1. **`cp DATA_SRC` 不加 `$`** → 复制的是字面量文件名，不是变量值。
# 2. **`"$F/.."` 取父目录** → 文件后面接 `..` 不是有效目录，用 `dirname "$F"`。
# 3. **`[ -f $F ]` 不加引号** → 变量为空或含空格时逻辑反转或语法错。
# 4. **`beeline -f` 不加 `--force=false`** → SQL 报错被吞，`set -e` 形同虚设。
# 5. **探活循环没有 `ready` 标志和 `exit 1`** → Hive 没起来也继续跑，错误堆到后面更难定位。