#!/usr/bin/env bash
# ============================================================
# 写入 test_connection 测试数据（幂等）
# ============================================================
#
# 为什么要单独用 shell 脚本而不是放在 02_doris_init.sql 里：
#   - Doris 不支持 MySQL 风格的 `DELETE FROM t WHERE ...`
#     （会报错），DUPLICATE KEY 模型也不保证逐行删除语义；
#   - 也不支持 `INSERT ... SELECT ... WHERE NOT EXISTS`。
#   而容器每次重建都会重跑初始化目录，直接 INSERT 会产生重复行，
#   因此把「先查后插」的幂等判断放在 shell 侧。
#
# 执行时机：由 BE 容器的初始化流程在 02_doris_init.sql 之后调用。
# 环境变量：
#   FE_HOST —— FE 的地址（由保活包装注入，默认 127.0.0.1）
# ============================================================
set -uo pipefail

FE_HOST="${FE_HOST:-127.0.0.1}"
QUERY_PORT="${FE_QUERY_PORT:-9030}"
DB="ecommerce"
TABLE="test_connection"

mysql_q() {
    mysql -h "${FE_HOST}" -P "${QUERY_PORT}" -uroot --connect-timeout=15 "$@"
}

echo "[seed] 检查 ${DB}.${TABLE} 是否已有测试数据 (FE=${FE_HOST}:${QUERY_PORT})"

count=""
for attempt in 1 2 3 4 5; do
    count=$(mysql_q -N -B -e "SELECT COUNT(*) FROM ${DB}.${TABLE} WHERE id = 1;" 2>/dev/null) && break
    echo "[seed] 第 ${attempt}/5 次查询失败，10s 后重试"
    sleep 10
done

if [ "${count}" = "1" ]; then
    echo "[seed] 已存在 id=1 的记录，跳过插入"
    exit 0
fi

echo "[seed] 写入测试数据 ..."
if mysql_q -e "INSERT INTO ${DB}.${TABLE} VALUES (1, 'doris connection ok', NOW());"; then
    echo "[seed] 写入成功"
else
    echo "[seed] 写入失败" >&2
    exit 1
fi

echo "[seed] 当前内容："
mysql_q -B -e "SELECT * FROM ${DB}.${TABLE};"
