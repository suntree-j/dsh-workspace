#!/usr/bin/env bash
# ============================================================
# scripts/setup-tls.sh — 为看板生成自签证书（启用 HTTPS）
# ============================================================
#
# 用法（服务器上，仓库根目录，需要 root）：
#   SERVER_IP=1.2.3.4 bash scripts/setup-tls.sh
#
# 为什么要上 HTTPS（真实排查结论，不是"为了安全所以上 HTTPS"）：
#   现象：客户端经公网访问看板，约 40% 的请求返回 **502**，
#         但该 502 **没有 Server 头、响应体为空** —— nginx 的 502
#         一定带 `Server: nginx/1.24.0` 与一段 HTML 错误页，所以它不是 nginx 发的。
#   同时：TCP 80 十次全部连接成功，服务端自测十次全部 200，
#         nginx 访问日志里没有任何 502，内核 ListenOverflows = 0。
#   结论：**明文 HTTP 在传输途中被中间设备改写**。
#         这类设备通常不碰加密流量，因此启用 HTTPS 是最可能根治的办法。
#
# 为什么是自签证书：
#   项目按 IP 直接访问（`http://<ip>/data/`），没有域名，
#   而受信任的 CA 不签 IP 证书（Let's Encrypt 明确不支持）。
#   自签证书的代价是浏览器首次访问要点一次"继续前往"，
#   换来的是**内容不可被中间设备读取或改写** —— 对这个偶发 502 是值得的。
#
# 幂等：证书已存在且未过期时直接复用，不覆盖（避免每次重跑都让浏览器重新警告）。
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

CERT_DIR="/etc/ssl/data-platform"
CERT="${CERT_DIR}/server.crt"
KEY="${CERT_DIR}/server.key"
DAYS=3650

main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} 配置 TLS 自签证书（启用 HTTPS）${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    if [ "$(id -u)" -ne 0 ]; then
        log_error "需要 root： sudo SERVER_IP=<ip> bash scripts/setup-tls.sh"
        exit 1
    fi

    if ! command -v openssl >/dev/null 2>&1; then
        log_error "找不到 openssl"
        exit 1
    fi

    # 服务器 IP：优先命令行/env，其次从 SSH 连接来源推断
    local ip="${SERVER_IP:-}"
    if [ -z "${ip}" ]; then
        ip="$(ip -4 addr show scope global 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1 | head -1)"
    fi
    if [ -z "${ip}" ]; then
        log_error "无法确定服务器 IP，请显式传入： SERVER_IP=1.2.3.4 bash scripts/setup-tls.sh"
        exit 1
    fi
    log_info "证书将为下列地址签发： ${ip} / 127.0.0.1 / localhost"

    mkdir -p "${CERT_DIR}"

    if [ -f "${CERT}" ] && [ -f "${KEY}" ]; then
        local end_date
        end_date="$(openssl x509 -enddate -noout -in "${CERT}" 2>/dev/null | cut -d= -f2)"
        if [ -n "${end_date}" ] && openssl x509 -checkend 86400 -noout -in "${CERT}" >/dev/null 2>&1; then
            log_ok "证书已存在且未过期（到期：${end_date}），复用"
            printf '  如需重新生成： rm -f %s %s 后重跑本脚本\n' "${CERT}" "${KEY}"
            return 0
        fi
        log_warn "证书已过期或即将过期，重新生成"
    fi

    log_info "生成自签证书（有效期 ${DAYS} 天）..."

    # 关键：subjectAltName 必须包含 IP，否则浏览器会以
    #   "证书对此地址无效" 而不是 "自签名" 报警告，误导使用者。
    local san="DNS:localhost,IP:127.0.0.1,IP:${ip}"

    openssl req -x509 -nodes -newkey rsa:2048 \
        -keyout "${KEY}" -out "${CERT}" \
        -days "${DAYS}" \
        -subj "/C=CN/ST=Beijing/L=Beijing/O=Data Platform/OU=Graduation Project/CN=${ip}" \
        -addext "subjectAltName=${san}" \
        -addext "basicConstraints=critical,CA:FALSE" \
        -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
        -addext "extendedKeyUsage=serverAuth" \
        2>/dev/null

    if [ ! -s "${CERT}" ] || [ ! -s "${KEY}" ]; then
        log_error "证书生成失败"
        exit 1
    fi

    chmod 600 "${KEY}"
    chmod 644 "${CERT}"

    log_ok "证书已生成"
    printf '  证书： %s\n' "${CERT}"
    printf '  私钥： %s（0600，仅 root 可读）\n' "${KEY}"
    printf '  主体： %s\n' "$(openssl x509 -subject -noout -in "${CERT}" | cut -d= -f2-)"
    printf '  含 IP： %s\n' "$(openssl x509 -ext subjectAltName -noout -in "${CERT}" | tr -d ' ')"

    printf '\n'
    log_info "接下来执行： bash scripts/deploy-web.sh   （会 reload Nginx 使 HTTPS 生效）"
}

main "$@" < /dev/null
exit $?
