# 测试怎么跑（本仓库的实际约束）

这份文档解决一个具体问题：**照直觉跑 `pytest tests/` 会失败**，而失败原因与本项目代码无关。
把踩过的坑写在前面，比事后逐个解释便宜。

## 1. 两套 `app` 包同名，不能在同一次 pytest 里跑

`services/api/app/` 与 `services/agent/app/` 是两个独立服务，但顶层包名都叫 `app`。
Python 的模块解析只会命中 `sys.path` 上排在前面的那一个，于是：

```text
$ PYTHONPATH=".;.tmp/pylibs;services/api;services/agent" python -m pytest tests/ -q
E   ModuleNotFoundError: No module named 'app.scope'
ERROR tests/test_agent_scope.py
```

`tests/test_agent_scope.py` 要的是 `services/agent/app/scope.py`，但 `services/api` 排在前面，
`app` 已经是 API 那个包，里面没有 `scope` 模块。

**正确做法是按归属分两批跑**（本机 Python 3.12，运行时 python 未必装了 pytest，见第 2 节）：

```bash
# API 侧纯函数
PYTHONPATH=".;.tmp/pylibs;services/api"  python -m pytest \
  tests/test_reconcile_state.py tests/test_traffic_reconcile_state.py \
  tests/test_preflight_rules.py tests/test_archive_criteria.py -q
# → 36 passed

# Agent 侧纯函数
PYTHONPATH=".;.tmp/pylibs;services/agent" python -m pytest tests/test_agent_scope.py -q
# → 17 passed
```

> 长期看应该把两个服务的包名改掉（例如 `platform_api` / `platform_agent`），
> 但那是跨目录改名，会牵动 systemd 的 `--app-dir`、Dockerfile 与全部 import，
> 不属于"顺手改"的范围，先按文档约定跑。

## 2. 运行时 Python 没装 pytest，缺依赖要用 `--target` 补

本机运行时 python（DSH 自带）只有标准库。为了让测试能跑，依赖装在仓库内的临时目录：

```bash
python -m pip install --target .tmp/pylibs pytest mysql-connector-python
```

`mysql-connector-python` 是必须的：`services/api/app/repository.py` 在 import 期就要用它，
否则 `from app.repository import ...` 直接 `ModuleNotFoundError`。

## 3. 这些测试覆盖的是什么

| 文件 | 覆盖 | 为什么值得单独测 |
|---|---|---|
| `test_reconcile_state.py` | `classify_reconcile_state()` 四态 | 判"落后"还是"不一致"决定看板给人什么结论，错一次就会把重放期说成通过 |
| `test_traffic_reconcile_state.py` | `classify_traffic_state()` 四态 | 同上，流量域比的是窗口数与 PV |
| `test_preflight_rules.py` | 金额精度正则、表头、时间单调、缺文件 | 预检误报会让人不敢装载，漏报会让人带着脏数据继续 |
| `test_archive_criteria.py` | 归档判据（含 `kafka_count == 0` 分支） | 旧判据用"整表行数 == 本次消息数"，topic 被 retention 清空后恒假，导致每晚失败 |
| `test_agent_scope.py` | 能力边界与措辞规范化 | 越界问题不该执行任何 SQL；回答里的绝对化表述不该出现 |

## 4. 不要去跑的东西

- `tests/` 里带 `@pytest.mark.smoke` 的用例需要真实服务（Doris/Kafka/Flink/Agent），
  本机没有运行中的依赖，跑了只会得到连接错误，不是代码缺陷。
- 全量 `scripts/verify-sprint-*.sh` 也是线上验收脚本，必须连服务器跑，不要在本地跑。

## 5. 上次全量回归的结论

2026-11-16 05:51，在上述两批命令下：**API 侧 36 passed、Agent 侧 17 passed，合计 53 passed，0 failed**。
