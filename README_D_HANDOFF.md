# D 交接说明

## 当前交付（2026-10-29）

分支为 `d/factor-research-agent`，基线为 C 的 `07f0de6`，包含 A/B。按 A/B/C 的习惯交付独立分支，本次不合并主干。

接手顺序：[当前使用说明](README_D.md) → [原方案与实际差异](docs/D_当前交付与方案差异.md) → [本次检查](docs/D_分支交付检查_20261029.md) → [最新前端实测](docs/D3_最新前端实测_20261029.md)。原设计、计划、prompt 和失败记录作为历史依据保留，不自动代表当前状态。

| 文件 | 作用 |
|---|---|
| `app/factors/`、`sql/70_factor.sql` | 表达式、单位、窗口、公共/私有版本、数据库计算 |
| `app/auth/`、`app/audit/`、`app/db.py`、`sql/80_security_audit.sql` | 登录、事务身份和日期、权限与请求/业务记录 |
| `app/agent/`、`sql/90_agent_gateway.sql`、`sql/migrations/d2/` | 真实模型适配、LangGraph、受限工具、对话、依据及取消 |
| `app/factor_research/`、`app/backtest/factor.py`、`app/costs/`、`sql/migrations/d3/` | 因子评价、个人成本与策略、独立模拟回测、输入版本 |
| `app/ui/main.py`、`app/ui/pages/d_*.py`、`app/ui/charts.py` | 正式统一入口、统计、研究、费用、AI 与图表 |
| `sql/seed/d_public_factors.json`、`docs/prompts/` | 50 项公共定义、实际系统提示词和个人偏好示例 |
| `tests/d/`、`sql/tests/test_d_permissions.sql`、`experiments/d_factors/` | 权限、数值、日期、取消、发布和页面等检查 |

已有完整 ABC 库按 `python -m app.setup_d`、`python -m app.setup_ai`、`python -m app.setup_d3` 顺序安装。不能只扫描编号 SQL，D2/D3 在迁移子目录；旧安装也按完整顺序刷新面板与规则。全新库按 README 创建专用教学库。角色密码各机器自行配置，管理员仅安装，页面不得使用管理员绕过权限。备份不含集群角色，换实例先准备角色。

作者机器最近完整演示是 8517 与 `zhishi_d3_web_20261029_sample`；其他端口可能连接别的副本。共享部署不依赖这些库名、地址或 `.audit`。系统信任持有数据库凭据的应用服务，事务变量本身不等于独立登录验证。

`.env`、`.audit/`、虚拟环境、原始数据、备份、账号密码、完整模型轨迹不交付。仓库交付源码、安装脚本和精选教学证据。个人费用尚未接入 C 原流水，未完成完整除权账务、逐行输入冻结/重放及性能对比，详见差异说明。

## 原交接与阶段增量（历史）

下文是原日期的交接记录，出现的旧分支、端口、10 项目录、“尚未推送”和当时模型失败均保留时间语境；当前分支与交付状态以上文及本次分支检查为准。

交接日期：2026-10-28。本地分支 `audit/abc-acceptance`，基线提交 `07f0de6`。D 的代码和文档还没有提交，远程也没有推送。

## 先保留什么

不要 `reset`、不要删除未跟踪文件、不要覆盖 `.env`，也不要重建 `zhishi_abc_fixed_sample`。这个库现在已经装上 D，行情、账户和回测数量与安装前一致。

本机数据库如果没在运行，用 `.audit/pgdata` 在 `127.0.0.1:55436` 启动。程序在 `.audit/runtime/pgsql`。`.audit`、`.env` 和备份不要提交。

## 库

| 库 | 用途 |
|---|---|
| `zhishi_abc_fixed_sample` | 当前 `.env` 指向的样本。已非破坏安装 D |
| `zhishi_d_sample` | 从上面复制后开发和验收的专用库 |
| `zhishi_d_restore_sample` | 由 `zhishi_d_sample` 的备份恢复，用来确认恢复后还能算 |
| `zhishi_abc_real20_*` 等 | 原有真实行情库，本次没有重建或改写 |

安装入口是 `app/setup_d.py` 的 `install()`。它先确认 A/B/C 的表还在，再在一个事务里执行 `sql/70_factor.sql`、`sql/80_security_audit.sql`、`sql/90_agent_gateway.sql`，然后注册 10 个公共公式。

新的数据库角色包括 `auth_login`、`audit_writer` 和D2的 `checkpoint_writer`。密码保存在本机 `.env`；D2安装会生成 `CHECKPOINT_WRITER_PASSWORD` 并创建受限的检查点角色。备份文件不包含集群角色，换机器要先准备角色；运行时不能使用管理员连接。

## 运行

使用说明见 [README_D.md](README_D.md)。验收结论见 [docs/D_交付验收.md](docs/D_交付验收.md)。实现过程见 [docs/worklog/D.md](docs/worklog/D.md)。

正式页面：

```powershell
.\.venv\Scripts\python.exe -m streamlit run app/ui/main.py --server.address 127.0.0.1 --server.port 8510
```

共享事务函数是 `app.db.transaction`。C 原来的调用方式不用改。

## D2

2026-10-29 增加了 AI 工作流和 40 个新公共公式。安装命令是 `python -m app.setup_ai` 或 `scripts/check_d2.py`。迁移在 `sql/migrations/d2/001_ai_workflow.sql`，不会被 `rebuild_db.py` 的 00–90 扫描漏掉，因为重建脚本末尾会调用 `app.setup_ai`。

验收库是 `zhishi_d2_sample`，恢复库是 `zhishi_d2_restore_sample`。正式 `.env` 指向的 `zhishi_abc_fixed_sample` 也已做非破坏安装，行情合计没有变化。结论见 [docs/D2_实现验收.md](docs/D2_实现验收.md)。

## D3

2026-10-29 增加因子评价、私有单因子策略、个人费用版本和毛净/零成本对照。安装命令是 `python -m app.setup_d3`。迁移在 `sql/migrations/d3/001_research_cost.sql`。`rebuild_db.py` 在 `setup_ai` 之后调用 `setup_d3`，不依赖 `00`–`90` 扫描。

验收副本是 `zhishi_d3_review_20261029_sample`。恢复副本是 `zhishi_d3_restore_20261029_sample`。外部财报在 `zhishi_d3_real_fin_20261029_sample`，没有写进教学库。正式样本已非破坏安装，行情合计仍是 193028.6732。结论见 [docs/D3_实现验收.md](docs/D3_实现验收.md)。

## 不要当成已完成的事

- D2 的真实 DeepSeek 六场景已经做过。D3 又做了评价、比较、草稿、费用、小样本和越权。其中小样本的最终文字被百分比检查拒绝。旧网关的 `UnconfiguredModel/ScriptedModel` 仍只用于兼容测试。本机密钥不得提交或写进交接文本。
- 供应商财报是 `PROVIDER_SNAPSHOT`，不是可以回填公告日的 `DISCLOSURE`。
- 没有做 Phase 9 的结果复用和耗时比较。
- 本次没有推送远程，也没有开合并请求。

2026-10-29 独立复核修复了真实接口消息、提示词越权写入、检查点元数据丢失、多轮上下文和页面取消等问题。完整结果与数据库副本用途见 [D2实现验收](docs/D2_实现验收.md)。正式页面仍是8510，另有验收甲/乙测试账号用于浏览器隔离检查，不是用户真实账户。
