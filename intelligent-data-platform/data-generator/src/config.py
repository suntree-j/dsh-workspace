"""数据生成器配置。

所有可调参数集中在此，并优先从环境变量 / .env 读取，
不在代码中硬编码任何真实凭据。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# ------------------------------------------------------------
# 加载 .env
#
# 查找顺序（第一个存在的生效）：
#   1. 显式环境变量 DATA_GENERATOR_ENV_FILE
#   2. 仓库根目录 .env            （宿主机运行时使用）
#   3. data-generator/.env
#
# 容器内运行时不需要 .env —— docker-compose.yml 会显式注入环境变量。
# ------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]

_ENV_CANDIDATES = [
    os.environ.get("DATA_GENERATOR_ENV_FILE"),
    str(_REPO_ROOT / ".env"),
    str(Path(__file__).resolve().parents[1] / ".env"),
]

for _candidate in _ENV_CANDIDATES:
    if _candidate and Path(_candidate).is_file():
        load_dotenv(_candidate, override=False)
        break


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"环境变量 {name} 必须是整数，当前值：{raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"环境变量 {name} 必须是浮点数，当前值：{raw!r}") from exc


@dataclass(frozen=True)
class MySQLConfig:
    """MySQL 连接配置。

    默认值面向 **容器内** 运行（使用服务名 mysql）。
    在宿主机直接运行时，.env 中应设置 MYSQL_HOST=localhost。
    """

    host: str = field(default_factory=lambda: _env("MYSQL_HOST", "mysql"))
    port: int = field(default_factory=lambda: _env_int("MYSQL_PORT", 3306))
    database: str = field(default_factory=lambda: _env("MYSQL_DATABASE", "ecommerce"))
    user: str = field(default_factory=lambda: _env("MYSQL_USER", "app"))
    password: str = field(default_factory=lambda: _env("MYSQL_PASSWORD", ""))
    connect_timeout: int = field(default_factory=lambda: _env_int("MYSQL_CONNECT_TIMEOUT", 10))

    def dsn(self) -> str:
        """用于日志输出的安全连接串（不含口令）。"""
        return f"mysql://{self.user}@{self.host}:{self.port}/{self.database}"


@dataclass(frozen=True)
class KafkaConfig:
    """Kafka 连接配置。

    容器内使用 kafka:9092；宿主机使用 localhost:19092。
    详见 docs/development-environment.md。
    """

    bootstrap_servers: str = field(
        default_factory=lambda: _env("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
    )
    client_id: str = field(default_factory=lambda: _env("KAFKA_CLIENT_ID", "data-generator"))
    # 单机单副本环境，1 即可；过大反而会因为 ISR 不足阻塞
    acks: str = field(default_factory=lambda: _env("KAFKA_ACKS", "1"))
    linger_ms: int = field(default_factory=lambda: _env_int("KAFKA_LINGER_MS", 50))
    # 发送失败重试次数
    message_timeout_ms: int = field(
        default_factory=lambda: _env_int("KAFKA_MESSAGE_TIMEOUT_MS", 30000)
    )


@dataclass(frozen=True)
class GenerateConfig:
    """生成规模与分布配置。"""

    # --- 规模（Sprint 0 要求：用户 1000+、商品 500+、订单 5000+、支付 5000+）---
    user_count: int = field(default_factory=lambda: _env_int("GEN_USER_COUNT", 1200))
    product_count: int = field(default_factory=lambda: _env_int("GEN_PRODUCT_COUNT", 600))
    order_count: int = field(default_factory=lambda: _env_int("GEN_ORDER_COUNT", 6000))
    behavior_event_count: int = field(
        default_factory=lambda: _env_int("GEN_BEHAVIOR_EVENT_COUNT", 20000)
    )

    # --- 业务比例 ---
    # 订单中成功支付的比例
    pay_ratio: float = field(default_factory=lambda: _env_float("GEN_PAY_RATIO", 0.92))
    # 已支付订单中出现退款的比例（“少量退款”）
    refund_ratio: float = field(default_factory=lambda: _env_float("GEN_REFUND_RATIO", 0.05))
    # 存在优惠（导致 amount != price*quantity）的订单比例
    discount_ratio: float = field(default_factory=lambda: _env_float("GEN_DISCOUNT_RATIO", 0.15))
    # 单订单最大商品件数
    max_quantity: int = field(default_factory=lambda: _env_int("GEN_MAX_QUANTITY", 5))

    # --- 行为漏斗分布 ---
    # 每个行为阶段独立的生成权重；生成器保证最终数量逐级收窄
    behavior_funnel: dict[str, float] = field(
        default_factory=lambda: {
            "VIEW": 1.00,
            "CLICK": 0.55,
            "CART": 0.20,
            "FAVORITE": 0.10,
            "BUY": 0.06,
        }
    )

    # --- 可复现性 ---
    random_seed: int = field(default_factory=lambda: _env_int("GEN_RANDOM_SEED", 20260926))

    # --- 批量写入 ---
    batch_size: int = field(default_factory=lambda: _env_int("GEN_BATCH_SIZE", 1000))


@dataclass(frozen=True)
class Config:
    mysql: MySQLConfig = field(default_factory=MySQLConfig)
    kafka: KafkaConfig = field(default_factory=KafkaConfig)
    generate: GenerateConfig = field(default_factory=GenerateConfig)


def load_config() -> Config:
    """加载并校验配置。"""
    cfg = Config()
    if not cfg.mysql.password:
        raise RuntimeError(
            "MYSQL_PASSWORD 未设置。\n"
            "请在仓库根目录执行 `cp .env.example .env` 并填写口令，"
            "或通过环境变量注入。禁止在代码中硬编码密码。"
        )

    total = sum(cfg.generate.behavior_funnel.values())
    if total <= 0:
        raise RuntimeError("GEN behavior_funnel 权重之和必须大于 0")
    return cfg
