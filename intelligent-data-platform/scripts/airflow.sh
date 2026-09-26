#!/usr/bin/env bash
# ============================================================
# scripts/airflow.sh — 包装 Airflow CLI（自动加载正确的配置）
# ============================================================
#
# 用法（服务器上，仓库根目录）：
#   bash scripts/airflow.sh dags list
#   bash scripts/airflow.sh dags list-import-errors
#   bash scripts/airflow.sh tasks list offline_lakehouse_pipeline
#   bash scripts/airflow.sh dags trigger offline_lakehouse_pipeline
#   bash scripts/airflow.sh dags list-runs -d offline_lakehouse_pipeline
#
# !! 为什么必须有这个包装 !!
#   Airflow 的全部配置都在 /opt/data-platform/airflow/airflow.env 里
#   （AIRFLOW__SECTION__KEY 形式）。systemd 单元用 EnvironmentFile 加载它，
#   所以服务本身没问题。
#   但**人工直接敲 `airflow ...` 时不会加载那个文件** —— 于是
#   sql_alchemy_conn 退回默认值 sqlite:////<AIRFLOW_HOME>/airflow.db，
#   命令会对着一个空的 SQLite 文件执行，报出这种极难理解的错：
#       [error] Database migration required. Please run `airflow db migrate`.
#       sqlite3.OperationalError: no such table: dag_bundle
#   明明元数据库迁移得好好的（MySQL 里 58 张表），却被告知"需要迁移" ——
#   这种提示会把人带向完全错误的方向（重跑迁移、甚至怀疑 MySQL 配错了）。
#
#   所以：在这个仓库里，请一律用本脚本而不是裸 `airflow`。
#   这与 scripts/spark-sql.sh 是同一个思路：把"正确的调用方式"固化成脚本，
#   而不是靠人记住该先 source 哪个文件。
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

AIRFLOW_VENV="${REPO_ROOT}/.venv-airflow"
AIRFLOW_ENV_FILE="${REPO_ROOT}/airflow/airflow.env"

if [ ! -x "${AIRFLOW_VENV}/bin/airflow" ]; then
    log_error "找不到 ${AIRFLOW_VENV}/bin/airflow"
    log_error "请先执行： bash scripts/install-airflow.sh"
    exit 1
fi

if [ ! -f "${AIRFLOW_ENV_FILE}" ]; then
    log_error "找不到 ${AIRFLOW_ENV_FILE}"
    log_error "请先执行： bash scripts/deploy-airflow.sh"
    exit 1
fi

# set -a 让文件里的变量自动导出（Airflow 靠环境变量读配置）
set -a
# shellcheck disable=SC1090
. "${AIRFLOW_ENV_FILE}"
set +a

exec "${AIRFLOW_VENV}/bin/airflow" "$@"
