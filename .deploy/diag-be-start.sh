#!/usr/bin/env bash
set -uo pipefail
cd /opt/data-platform

echo "=== 容器与进程状态 ==="
docker compose ps doris-be
docker exec doris-be bash -c 'ps -eo pid,etime,cmd | grep -E "[d]oris_be|[s]tart_be|[k]eepalive|[b]e_entrypoint" | head -10' 2>&1

echo
echo "=== 端口监听情况 ==="
docker exec doris-be bash -c 'ss -ltn 2>/dev/null | head -8' 2>&1

echo
echo "=== be.out 尾部（启动脚本输出） ==="
docker exec doris-be bash -c 'tail -40 /opt/apache-doris/be/log/be.out 2>&1' 2>&1 | tail -40

echo
echo "=== 是否有 be.INFO（说明 BE 曾启动） ==="
docker exec doris-be bash -c 'ls -la /opt/apache-doris/be/log/ 2>/dev/null | head -10'

echo
echo "=== 存储目录状态 ==="
docker exec doris-be bash -c 'ls -la /opt/apache-doris/be/storage/ 2>/dev/null | head -10; echo "--- data 子目录 ---"; ls -la /opt/apache-doris/be/storage/data 2>/dev/null | head -5'

echo
echo "=== 容器日志（keepalive 全部输出） ==="
docker logs doris-be 2>&1 | tail -25
