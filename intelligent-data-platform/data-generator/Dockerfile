# ============================================================
# Data Generator 镜像
# ============================================================
#
# 构建：
#   docker compose build data-generator
# 运行（按需，不随 up -d 自动启动）：
#   docker compose run --rm data-generator python -m src.generate_mysql_data
#   docker compose run --rm data-generator python -m src.generate_events
#
# 基础镜像使用明确版本，不使用 latest。
# 与宿主机 Python 3.13 保持一致，避免版本差异导致行为不一致。
# ============================================================
FROM python:3.13.14-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# ------------------------------------------------------------
# 依赖单独一层：只要 requirements.txt 不变就复用缓存
# ------------------------------------------------------------
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# ------------------------------------------------------------
# 源码
#
# 说明：docker-compose.yml 会把 ./data-generator 挂载到 /app，
#       因此本地开发时源码改动立即生效、无需重新构建。
#       这里的 COPY 保证镜像在**独立运行**时也是完整的。
#       入口为 python -m src.*，故工作目录必须是 /app（上一级）。
# ------------------------------------------------------------
COPY . /app

# 运行时状态目录（快照会写入这里）
#
# 权限说明：容器以非 root 的 appuser 运行，而 docker-compose.yml 会把
# 宿主机的 ./data-generator 挂载到 /app（覆盖此处权限）。
# 因此这里放开 state 目录权限，保证挂载后仍可写入快照；
# 生产环境应改用命名卷或预先 chown。
RUN mkdir -p /app/state && chmod 0777 /app/state

# 以非 root 运行
RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

CMD ["python", "-m", "src", "--help"]
