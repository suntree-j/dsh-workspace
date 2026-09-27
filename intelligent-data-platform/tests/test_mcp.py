"""MCP 服务与 Agent 侧 MCP 客户端的单元测试（Sprint 10）。

## 这一组测试要锁住什么

Sprint 10 的核心主张是"**MCP 暴露的是最小必要能力，且它绕不过 `sqlguard`**"。
主张本身不是代码，能被测试锁住的只有下面四条：

1. **能力面就是那 4 个工具**，且与 Agent 声明的取数工具**逐名相等**。
   多一个工具就是多一份要审的权限面，而"多了一个没人注意到的工具"
   恰恰是这类服务最现实的失效方式。
2. **MCP 进程里没有凭据**：配置只读 4 个非敏感变量；源码里不出现
   任何数据库口令/驱动/连接串。这条用**源码扫描**断言 ——
   因为"没写"这件事没有别的可观测形态。
3. **取数路径的分流是真的**：`data_path=mcp` 时工具确实走 MCP，
   `data_path=http` 时**完全不建** MCP 连接（默认路径不该多出后台线程）。
4. **两条路径的结果形状一致**：MCP 返回 `{"tables": [...]}`、
   `{"latest_batch": ...}`，工具层必须把它们**复原成直连的形状**，
   否则图里读 `row_count` / `tables` 的地方会静默拿到空值。

依据：`AGENTS.md` 第 8.2 节（单元测试不得依赖外部服务）。
**这些用例不建任何 MCP 连接、不调数据服务**：用替身注入。
真实端到端一致性由 `scripts/verify-sprint-10.sh` 第 5 步在服务器上实测。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings, load_settings
from app.mcp_client import McpToolClient, McpToolError, McpUnavailable, _unwrap
from app.tools import _MCP_TOOL_NAMES, ToolBox, extract_tables

REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_DIR = REPO_ROOT / "services" / "mcp"
MCP_SERVER_SRC = MCP_DIR / "app" / "server.py"
MCP_CLIENT_SRC = MCP_DIR / "app" / "client.py"
MCP_CONFIG_SRC = MCP_DIR / "app" / "config.py"
MCP_UNIT = MCP_DIR / "deploy" / "data-platform-mcp.service"

pytestmark = pytest.mark.unit


def _settings(**overrides: object) -> Settings:
    base = load_settings()
    return Settings(**{**base.__dict__, **overrides})


# ============================================================
# 替身：一个记录调用的 MCP 客户端
# ============================================================
class _FakeMcp:
    """按剧本返回结果的 MCP 客户端替身（记录调用入参）。"""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_with: Exception | None = None

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((name, dict(arguments or {})))
        if self.fail_with is not None:
            raise self.fail_with
        return self.responses.get(name, {"ok": True})


# ============================================================
# 1. 能力面最小化
# ============================================================
MCP_TOOL_SOURCE = MCP_SERVER_SRC.read_text(encoding="utf-8")

# 工具声明的抓取正则。
#
# !! 这个正则被实测修过两次，两次都不是小事 !!
#   1) 最初写 `@server\.tool\([^)]*\)` —— 遇到带参数的装饰器
#      （`@server.tool(structured_output=True)`）时，`[^)]*` 只能吃到第一个 `)`，
#      于是**一个工具都抓不到**，表现为"工具集合变成了空列表"，
#      看起来像产品缺陷，实际是测试自己的正则太窄。
#   2) 改成 `\(.*?\)\s*\ndef` 后**仍然抓不到**：工具是定义在 `build_server()`
#      **函数体内部**的，`def` 前面有 4 个空格，而 `\s*\ndef` 要求 `def`
#      紧跟在换行之后。修法是 `\s*def`。
#   结论：凡"从源码文本抓结构"的断言，都必须先有一条**证明扫描器有效**的用例
#   （见 `test_tool_declaration_scanner_is_not_silently_empty`），
#   否则扫描器失效会伪装成产品缺陷。
_TOOL_DECL_RE = re.compile(r"@server\.tool\(.*?\)\s*def\s+(\w+)", re.S)


def _declared_mcp_tools() -> list[str]:
    return _TOOL_DECL_RE.findall(MCP_TOOL_SOURCE)


def test_tool_declaration_scanner_is_not_silently_empty() -> None:
    """先证明扫描器本身有效（非空守卫）。

    为什么单列一条：下面几条用例都建立在"扫描器能抓到工具名"之上。
    万一源码换了装饰器写法而正则没跟上，那些用例会以
    "工具集合为空"的形式失败 —— 看起来像产品缺陷。这条用例把
    "扫描器失效"与"产品缺陷"区分开。
    """
    assert _declared_mcp_tools() != [], "扫描器没抓到任何 @server.tool() 声明，正则需要更新"


def test_mcp_exposes_exactly_the_four_readonly_tools() -> None:
    """MCP 服务只声明 4 个工具，一个不多。

    断言的是**服务源码里 `@server.tool()` 的个数与名字**，
    而不是"文档里说有几个" —— 后者可以随手改。
    """
    declared = _declared_mcp_tools()
    assert declared == [
        "metrics_lookup",
        "tables_lookup",
        "sql_query",
        "reconciliation",
    ], f"MCP 暴露的工具集合变了：{declared}"


def test_mcp_tool_set_matches_agent_routing_set() -> None:
    """Agent 认为"走 MCP 的工具"必须与 MCP 实际暴露的**逐名相等**。

    两边任一单独改动都会在这里失败：少一个 = 有一路能力白白空跑；
    多一个 = Agent 会去调用一个不存在的工具（运行期才炸）。
    """
    assert set(_declared_mcp_tools()) == set(_MCP_TOOL_NAMES)


def test_mcp_source_has_no_write_or_admin_tool() -> None:
    """能力面里不许出现写操作/管理类工具名（权限最小化的下限）。"""
    forbidden = (
        "def insert_", "def update_", "def delete_", "def drop_", "def execute_ddl",
        "def execute_dml", "def run_shell", "def read_file", "def write_file",
        "def http_request", "def fetch_url", "def admin_",
    )
    hit = [name for name in forbidden if name in MCP_TOOL_SOURCE]
    assert hit == [], f"MCP 出现了不该有的能力：{hit}"


# ============================================================
# 2. MCP 进程里没有数据库凭据
# ============================================================
def test_mcp_config_reads_only_non_sensitive_env() -> None:
    """MCP 配置读的全是非敏感键，一个凭据类键都没有。

    分两步，**第 2 步不能省**：
      1) 从源码文本里抓所有环境变量名，断言没有凭据类键（安全判据）；
      2) 真的把 `services/mcp/app/config.py` 装起来跑一次 `load_settings()`，
         断言必需项确实生效（有效性判据）。
    只做第 1 步会在"扫描器抓不全"时空洞通过 —— 那正是本项目反复踩的坑。
    """
    source = MCP_CONFIG_SRC.read_text(encoding="utf-8")
    keys = set(re.findall(r'"([A-Z][A-Z0-9_]{2,})"', source))
    assert keys, "没解析到任何配置键 —— 扫描规则可能失效了"
    suspicious = [k for k in keys if any(w in k for w in ("PASSWORD", "SECRET", "TOKEN", "KEY"))]
    assert suspicious == [], f"MCP 配置里出现了凭据类键：{suspicious}"

    # !! 必须用 importlib 按**文件路径**加载，且**先注册进 sys.modules** !!
    #   两个坑叠在一起：
    #   a) `services/mcp/app` 与 `services/agent/app` 都叫 `app`，而
    #      tests/conftest.py 已把 agent 目录放进 sys.path —— `import app.config`
    #      会拿到 **Agent 的**配置类（没有 mcp_* 字段），断言会以一种与 MCP
    #      无关的方式失败；
    #   b) 该模块用了 `from __future__ import annotations`，`@dataclass` 在
    #      解析注解时会 `sys.modules.get(cls.__module__)` —— 如果模块没先注册，
    #      取到 None，直接 `AttributeError: 'NoneType' object has no attribute '__dict__'`。
    #      这个报错与"配置有问题"毫无关系，极易被误读。
    import importlib.util
    import sys as _sys

    module_name = "_mcp_config_under_test"
    spec = importlib.util.spec_from_file_location(module_name, MCP_DIR / "app" / "config.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    _sys.modules[module_name] = module
    spec.loader.exec_module(module)

    mcp_settings = module.load_settings()
    assert mcp_settings.data_api_base.startswith("http://127.0.0.1:")
    assert mcp_settings.host == "127.0.0.1"
    assert mcp_settings.port == 8200
    # 凭据类字段**根本不存在**（不是"值为空"）
    for banned in ("password", "secret", "token", "api_key", "doris", "mysql"):
        assert not hasattr(mcp_settings, banned), f"MCP 配置里出现了凭据类字段：{banned}"


def test_mcp_source_never_touches_database_credentials() -> None:
    """MCP 源码里不许出现数据库连接信息或驱动。

    为什么用**源码扫描**而不是运行期断言：
        "这个进程没有凭据"在运行期无法自证（它当然可以说自己没读）。
        唯一可核对的是"代码里没有这条路径"。
    """
    for path in (MCP_SERVER_SRC, MCP_CLIENT_SRC, MCP_CONFIG_SRC):
        source = path.read_text(encoding="utf-8")
        for needle in (
            "DORIS_ROOT_PASSWORD", "MYSQL_ROOT_PASSWORD", "MYSQL_PASSWORD",
            "import pymysql", "import mysql.connector", "mysql-connector",
            "s3a.secret.key", "MINIO_ROOT_PASSWORD",
        ):
            assert needle not in source, f"{path.name} 里出现了凭据/驱动：{needle}"


def test_mcp_systemd_unit_does_not_read_env_file() -> None:
    """systemd 单元**不得**注入 `.env`。

    `.env` 含数据层口令（`root:dpapi 0640`）。最小权限不是"少读几个键"，
    而是"这个进程根本碰不到那个文件"——所以这一条是硬断言，不是建议。

    !! 判据要按**行首的指令**匹配，不能整篇 `not in` !!
        第一版写的是 `assert "EnvironmentFile=" not in unit`，
        结果被单元文件里那句**注释**（`# ⚠️ 刻意**没有** EnvironmentFile=...`）
        判成失败 —— 一句解释"我们为什么不读它"的注释，把断言弄红了。
        这类"断言匹配到了注释"的假失败很容易被顺手放宽成
        `check_not_contains`，那就把真正的防线一起丢了。
        因此这里用 `^\\s*EnvironmentFile=`（只认指令行）。
    """
    unit = MCP_UNIT.read_text(encoding="utf-8")
    active = [
        line for line in unit.splitlines()
        if re.match(r"^\s*EnvironmentFile\s*=", line)
    ]
    assert active == [], f"MCP 单元不得读 .env，实际有：{active}"
    assert "MCP_DATA_API_BASE=" in unit, "必须显式注入唯一必需项（数据服务地址）"
    # 只监听回环：MCP 端口不对外暴露
    assert "MCP_HOST=127.0.0.1" in unit
    # 专用非 root 用户
    assert re.search(r"^User=(?!root)\w+", unit, re.M), "MCP 必须用专用用户运行，不能用 root"


def test_mcp_client_only_uses_the_readonly_http_api() -> None:
    """MCP 的数据出口只有那三个只读接口，没有第四个。"""
    source = MCP_CLIENT_SRC.read_text(encoding="utf-8")
    paths = re.findall(r'self\._(?:get|post)\(\s*"([^"]+)"', source)
    assert sorted(set(paths)) == ["/batch/reconcile", "/meta/metrics", "/meta/tables", "/query"], (
        f"MCP 的数据出口变了：{sorted(set(paths))}"
    )
    assert 'self._post("/query"' in source, "唯一取数通道必须是 POST /query"


# ============================================================
# 3. 取数路径分流
# ============================================================
def test_default_data_path_is_http() -> None:
    """默认路径仍是直连（换默认值等于改变已验收过的行为）。"""
    assert load_settings().data_path == "http"


def test_invalid_data_path_fails_loudly() -> None:
    """拼错的取数路径必须启动即失败，不能静默回落。

    静默回落的后果：验收脚本全绿，而"经 MCP 取数"一次都没发生。
    """
    from app.config import ConfigError, _env_choice

    with pytest.raises(ConfigError):
        _env_choice({"AGENT_DATA_PATH": "Mcp"}, "AGENT_DATA_PATH", "http", ("http", "mcp"))


def test_http_path_never_creates_mcp_client() -> None:
    """`data_path=http` 时工具箱**不建** MCP 连接。

    为什么值得断言：默认路径上多一个后台线程/事件循环，
    在 16 GB 的机器上是实打实的开销，而且它是"看不见"的。
    """
    box = ToolBox(_settings(data_path="http"))
    assert box._mcp is None
    assert box.data_path == "http"


def test_mcp_path_routes_sql_query_through_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    """`data_path=mcp` 时 sql_query 确实经 MCP（而不是悄悄直连）。"""
    fake = _FakeMcp(
        {
            "sql_query": {
                "ok": True,
                "executed_sql": "SELECT COUNT(*) FROM ecommerce.ads_realtime_trade_1m LIMIT 200",
                "row_count": 1,
                "elapsed_ms": 7,
                "rows": [{"cnt": 42}],
            }
        }
    )
    box = ToolBox(_settings(data_path="mcp"))
    monkeypatch.setattr(box, "_mcp", fake)

    result = box.call("sql_query", {"sql": "SELECT COUNT(*) FROM ecommerce.ads_realtime_trade_1m"})

    assert result.ok is True
    assert [name for name, _ in fake.calls] == ["sql_query"]
    assert fake.calls[0][1] == {"sql": "SELECT COUNT(*) FROM ecommerce.ads_realtime_trade_1m"}


def test_non_fetch_tools_never_go_through_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    """检索与规划协议**不走** MCP：它们本来就不是取数能力。"""
    fake = _FakeMcp()
    box = ToolBox(_settings(data_path="mcp", retrieval_enabled=False))
    monkeypatch.setattr(box, "_mcp", fake)

    box.call("propose_sql", {"reasoning": "x", "steps": []})
    assert fake.calls == [], "propose_sql 是规划协议，不应该产生 MCP 调用"

    result = box.call("retrieve_docs", {"query": "gmv"})
    assert fake.calls == [], "retrieve_docs 查的是 Agent 自己的语料，不走 MCP"
    assert result.ok in (True, False), "检索结果仍应是正常的 ToolResult"


def test_mcp_failure_does_not_fall_back_to_direct_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """MCP 不可达时**失败即失败**，不许偷偷改用直连。

    若允许静默回退，"MCP 路径到底有没有在工作"将永远无法验收。
    """
    fake = _FakeMcp()
    fake.fail_with = McpUnavailable("连接被拒绝")
    box = ToolBox(_settings(data_path="mcp"))
    monkeypatch.setattr(box, "_mcp", fake)

    # 直连实现会真发 HTTP；一旦被调用就会抛 URLError/超时并变成 ok=False 的
    # "网络错误"，而我们要的是"MCP 失败"这一条明确原因。
    result = box.call("sql_query", {"sql": "SELECT 1 FROM ecommerce.ads_realtime_trade_1m"})
    assert result.ok is False
    assert "MCP" in result.content
    assert "AGENT_DATA_PATH" in result.content, "必须明确告诉人怎么切回直连（显式动作）"


# ============================================================
# 4. 两条路径的结果形状必须一致
# ============================================================
def test_tables_lookup_via_mcp_resumes_direct_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """MCP 的 `{"tables": [...]}` 必须被复原成直连的 rows 形状 + tables 血缘。"""
    rows = [{"table": "ads_realtime_trade_1m", "column": "gmv", "type": "decimal(18,2)"}]
    fake = _FakeMcp({"tables_lookup": {"ok": True, "found": True, "tables": rows, "tables_total": 1}})
    box = ToolBox(_settings(data_path="mcp"))
    monkeypatch.setattr(box, "_mcp", fake)

    result = box.call("tables_lookup", {"table": "ads_realtime_trade_1m"})
    assert result.ok is True
    assert result.tables == ["ads_realtime_trade_1m"], "血缘里的表名来自返回行，必须复原"
    assert "gmv" in result.content


def test_tables_lookup_via_mcp_reports_missing_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """查不到的表必须报失败并列出可用表 —— 不能返回空结果让模型以为"表存在但没列"。"""
    fake = _FakeMcp(
        {
            "tables_lookup": {
                "ok": True,
                "found": False,
                "available_tables": ["ads_batch_trade_1d", "dwd_trade_order_detail"],
            }
        }
    )
    box = ToolBox(_settings(data_path="mcp"))
    monkeypatch.setattr(box, "_mcp", fake)

    result = box.call("tables_lookup", {"table": "dwd_user_profile"})
    assert result.ok is False
    assert "dwd_user_profile" in result.content
    assert "ads_batch_trade_1d" in result.content


def test_reconciliation_via_mcp_uses_latest_batch_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """MCP 的 `latest_batch` 必须映射回图的校验节点认识的键。"""
    fake = _FakeMcp(
        {
            "reconciliation": {
                "ok": True,
                "latest_batch": {"batch_id": "b1", "mismatch_count": 0},
                "deltas": {"gmv": "0.00"},
                "totals": {"realtime": {"gmv": "51890375.77"}},
                "mismatch_count": 0,
            }
        }
    )
    box = ToolBox(_settings(data_path="mcp"))
    monkeypatch.setattr(box, "_mcp", fake)

    result = box.call("reconciliation", {})
    assert result.ok is True
    assert "latest_batch" in result.content
    assert "51890375.77" in result.content


def test_extract_tables_matches_sqlguard_rules() -> None:
    """Agent 侧的 SQL 表名提取必须与 `sqlguard.extract_tables` 逐例一致。

    两处实现是刻意分开的（跨部署单元，见 tools.py 的说明），
    所以这条用例就是它们的**防漂移锚点**：一旦某一边改了规则，这里立刻失败。
    """
    from services.api.app.sqlguard import extract_tables as guard_extract

    samples = [
        "SELECT gmv FROM ecommerce.ads_realtime_trade_1m",
        "SELECT a.gmv FROM `ecommerce`.`ads_realtime_trade_1m` a",
        "SELECT * FROM [ads_batch_trade_1d] LIMIT 5",
        "SELECT 1 FROM lakehouse_ads.ads_batch_trade_1d JOIN ecommerce.dwd_trade_order_detail d ON 1=1",
        "SELECT order_id FROM dwd_trade_order_detail ORDER BY order_id LIMIT 10",
        "SELECT 1",
    ]
    for sql in samples:
        assert extract_tables(sql) == guard_extract(sql), f"表名提取规则漂移：{sql}"


def test_mcp_sql_query_血缘_from_executed_sql(monkeypatch: pytest.MonkeyPatch) -> None:
    """血缘取自 `executed_sql`（可核对），而不是让 MCP 自报表名。"""
    fake = _FakeMcp(
        {
            "sql_query": {
                "ok": True,
                "executed_sql": "SELECT COUNT(*) FROM ecommerce.ads_realtime_trade_1m LIMIT 7",
                "row_count": 1,
                "elapsed_ms": 3,
                "rows": [{"c": 1}],
            }
        }
    )
    box = ToolBox(_settings(data_path="mcp"))
    monkeypatch.setattr(box, "_mcp", fake)

    result = box.call("sql_query", {"sql": "SELECT COUNT(*) FROM ecommerce.ads_realtime_trade_1m"})
    assert result.tables == ["ads_realtime_trade_1m"]


# ============================================================
# 5. 结果解包（MCP SDK 返回形状的适配层）
# ============================================================
class _Block:
    def __init__(self, text: str) -> None:
        self.text = text


class _Result:
    """`CallToolResult` 的最小替身。"""

    def __init__(self, content: str = "", structured: Any = None, is_error: bool = False) -> None:
        self.content = [_Block(content)] if content else []
        self.structured_content = structured
        self.is_error = is_error


def test_unwrap_parses_json_text_content() -> None:
    """SDK 把"返回对象"的工具结果放在 content[0].text 里（实测 v2.2.0）。"""
    payload = {"ok": True, "row_count": 3, "rows": [{"a": 1}]}
    import json

    out = _unwrap(_Result(content=json.dumps(payload)), "sql_query")
    assert out == payload


def test_unwrap_parses_structured_content() -> None:
    out = _unwrap(_Result(structured={"result": '{"ok": true, "n": 1}'}), "x")
    assert out == {"ok": True, "n": 1}


def test_unwrap_turns_business_rejection_into_mcp_tool_error() -> None:
    """守卫拒绝是**业务反馈**，必须带 code 抛出来（模型要据此改写 SQL）。"""
    import json

    payload = {"ok": False, "error": {"code": "NOT_SELECT", "message": "只允许 SELECT 查询"}}
    with pytest.raises(McpToolError) as excinfo:
        _unwrap(_Result(content=json.dumps(payload)), "sql_query")
    assert excinfo.value.code == "NOT_SELECT"
    assert "SELECT" in excinfo.value.message


def test_unwrap_raises_on_transport_level_error() -> None:
    with pytest.raises(McpToolError):
        _unwrap(_Result(content="boom", is_error=True), "sql_query")


def test_unwrap_rejects_unparseable_payload() -> None:
    """解析不了就报错，**绝不**返回空字典假装查到了空结果。"""
    with pytest.raises(McpToolError):
        _unwrap(_Result(content="not json at all"), "sql_query")


# ============================================================
# 6. 配置与文档一致性
# ============================================================
def test_mcp_requirements_pins_exact_version() -> None:
    """MCP SDK 必须**钉死**版本（禁止 latest / 区间）。

    MCP 的 v1 → v2 是破坏性重构（`FastMCP` → `MCPServer`，
    `ClientSession` → `Client`）。给区间等于允许在一次部署里换掉整套 API 形状。

    !! 判据是"有且只有一个精确钉住的版本行"，不是"文本里不出现 latest" !!
        第一版写的是 `assert "latest" not in text.lower()`，被文件里
        **解释版本来历的注释**（"PyPI ... latest = 2.2.0"）判成失败。
        真正要保证的事是"依赖行被钉住"，所以直接对 `^mcp==x.y.z$` 计数。
    """
    text = (MCP_DIR / "requirements.txt").read_text(encoding="utf-8")
    pins = re.findall(r"^mcp==(\d+\.\d+\.\d+)$", text, re.M)
    assert len(pins) == 1, f"services/mcp/requirements.txt 必须恰好钉一个 mcp==x.y.z：{pins}"
    # 依赖行（非注释）里不许出现区间约束
    deps = [
        line.strip() for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert deps == [f"mcp=={pins[0]}"], f"依赖行只能是那一行精确钉版：{deps}"


def test_agent_and_mcp_pin_the_same_mcp_version() -> None:
    """服务端与客户端钉的必须是同一个版本（同一个包的两个角色）。"""
    server_pin = re.findall(
        r"^mcp==(\S+)$", (MCP_DIR / "requirements.txt").read_text(encoding="utf-8"), re.M
    )
    agent_pin = re.findall(
        r"^mcp==(\S+)$", (REPO_ROOT / "services" / "agent" / "requirements.txt").read_text(encoding="utf-8"), re.M
    )
    assert server_pin == agent_pin, f"MCP 版本不一致：server={server_pin} agent={agent_pin}"


def test_mcp_client_defaults_point_at_loopback() -> None:
    """默认只连回环 MCP 端点（不指向任何外部主机）。"""
    settings = load_settings()
    assert settings.mcp_server_url.startswith("http://127.0.0.1:")
    assert settings.mcp_transport in ("streamable-http", "stdio")
    # 超时必须大于单跳超时，否则会把"MCP 链路更长"误报成"不可用"
    assert settings.mcp_timeout > 30.0


def test_mcp_client_exposes_call_and_list() -> None:
    """客户端门面必须同时提供"调用"与"列能力"两件事。"""
    assert callable(McpToolClient.call_tool)
    assert callable(McpToolClient.list_tools)
