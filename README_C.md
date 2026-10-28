# C 模块：模拟交易、回测与并发控制

本模块基于 `origin/b/market-etl` 的 `1a137eb`，已包含 A 的时态/PIT 实现。`main` 当前只有初始 README，不能据此判断小组没有开发进展。C 开发分支为 `c/trading-backtest`，A/B 的已有文件保持原样。

**交接入口：[C 模块交接 README](README_C_HANDOFF.md)**。该文档说明交付范围、独立分支、从零验收、已有库接入和 D 的集成清单；本文解释实现逻辑。

## 1. 你负责的内容与阅读顺序

你是 C，核心问题是：账户只有一份钱和股票，多笔订单同时进来时，数据库如何保证不多花钱、不多卖股票；策略读取历史数据时，又如何避免使用未来信息。

按以下顺序阅读和演示：

1. **基础交易**：`sql/50_sim.sql`。先看 account/position/orders，再看四个公开交易函数。
2. **规则验证**：`sql/tests/test_sim_*.sql`。每个故意失败的案例对应一条业务规则。
3. **并发实验**：`experiments/c_concurrency/README.md` 和结果，理解先查后改为何存在竞争。
4. **策略回测**：`sql/60_research.sql`、`app/backtest/__init__.py`，理解信号日与成交日的区别。
5. **实际操作**：`app/ui/pages/c_trading.py`、`c_backtest.py`，最后看自动验收与交接记录。

PPT 后续再整理；本次先交付可运行代码、实验结果和说明。

## 2. 交易逻辑

日终 `as_of_date` 是已经知道行情的最后一天。例如 5 月 13 日收盘后提交订单，`fn_advance_day` 推进到 5 月 14 日，先日初结算，再按开盘、收盘顺序处理订单，最后把账户日期推进到 14 日日终。

```text
开户 → 追加初始入金流水
下单 → 锁账户 → 验证归属/参数 → 冻结资金或股票 → PENDING
推进 → 锁账户 → 日初解锁昨天的持仓 → OPEN/CLOSE 撮合
     → 成交：释放冻结、更新余额和持仓、追加成交及流水 → FILLED
     → 拒单：释放冻结 → REJECTED
撤单 → 锁账户 → 释放冻结 → CANCELLED
```

- `cash` 含冻结部分，可用资金为 `cash-frozen_cash`。买单按已知收盘价的涨停上界预估冻结；限价买单按限价冻结；同时预留费用。
- 所有写入口先 `SELECT ... FOR UPDATE` 锁同一个账户，随后再操作订单和持仓。其他并发请求等到前一个事务完成，再读更新后的可用金额。
- 订单只允许 `PENDING → FILLED/CANCELLED/REJECTED`，终态不能再改；成交和资金流水的 UPDATE/DELETE 被触发器拒绝。
- `CHECK` 保证现金非负、冻结不超过余额、持仓非负、数量为整手等；真实应用账号没有直接写表权限。
- `v_cash_reconcile` 检查资金余额是否等于流水合计，以及冻结现金是否等于待执行买单冻结额，正常应为空。
- **T+1**：同一执行日预先提交的买单不能给同日卖单提供持仓。当天成交买入后，`sellable_qty` 仍不包含这笔数量；第二天日初解锁。因为本系统的下单入口位于日终，所以今天买入、今天日终提交的卖单在明天执行，是允许的，不应额外锁一天。
- 只在指定 OPEN/CLOSE 点判断限价，不根据日内高低价猜成交顺序；未满足的订单当日拒绝，不自动挂到后天。涨停不买、跌停不卖，采用保守规则，不模拟排队和部分成交。

## 3. 回测逻辑

三个模板：`momentum` 按 20 日动量排序；`ma_cross` 先要求 MA20>MA60 再按动量排序；`quality` 按当时已可见的最新财报 ROE 排序。相同分数按股票代码升序，保证结果确定。

每个交易日从 A 的 PIT 候选池读信号，选择前 N 只。仍入选的持仓保留，不入选持仓提交卖单；新股票按目标等权预算补仓。只花已经到账的现金，不预支次日卖出所得，因此换仓可能分两天完成，组合也不保证每天严格等权。100 股整手、冻结缓冲和费用都会产生现金余额。

订单在下一交易日开盘执行，使用原始价格和实际股数。后复权指标只用于选股，不把复权价格直接当作真实成交价。收盘净值为 `(现金+原始收盘价持仓市值)/初始本金`；停牌有报价时沿用原始报价，否则取最新已知收盘价。日回撤为 `NAV/历史最高 NAV-1`，最大回撤为日回撤最小值的绝对值。

SQL 动量基准在数据库内完成循环和排序；Python 独立实现三个模板的筛选排序，复用同一撮合器。此对照验证策略实现一致性；独立会计验证另使用 backtrader 的买入持有案例。

每次运行记录参数、引擎版本、财报系统快照及行情/规则摘要。执行期间对基础表加共享锁，结束再次核对摘要；请把 ETL/指标刷新安排在回测之外。`replay_backtest` 恢复原财报快照，验证策略参数和行情规则未变。摘要变化会拒绝重放，需要先恢复原数据备份；MD5 在此只做内容变化检查，不用于安全审计。

## 4. 安装与运行

需要 Python 3.11+ 和 PostgreSQL 16，不要求使用 Docker。依赖为：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-c.txt
```

配置 `.env` 时，`PGADMIN_*` 用于建库，`PGHOST/PGPORT` 用于实际应用连接，两组必须指向同一实例。应用服务使用 `app_user` 和 `APP_USER_PASSWORD`，不以 postgres 身份处理交易。不要覆盖已有 `.env`。

**本机已准备好**：独立库 `zhishi_c_sample`，本机端口 `55432`；连接密码仅保存在 Git 忽略的 `.env`。便携 PostgreSQL 数据和程序位于系统临时目录 `D:\temp\zhishi_c_pg_20261028`，没有安装 Windows 服务。系统清理临时目录前请备份，正式环境仍按组内数据库方案部署。

若该便携实例在重启电脑后尚未运行：

```powershell
& 'D:\temp\zhishi_c_pg_20261028\runtime\bin\pg_ctl.exe' -D 'D:\temp\zhishi_c_pg_20261028\data' -l 'D:\temp\zhishi_c_pg_20261028\postgres.log' -o '-p 55432 -h 127.0.0.1' -w start
```

从仓库根目录执行。下面的重建命令会删除 `.env` 的 `PGDATABASE` 所指数据库，因此只用于专门准备的样本库，不能指向已有真实数据：

```powershell
.venv\Scripts\python.exe scripts/rebuild_db.py --yes
.venv\Scripts\python.exe scripts/setup_a_sample.py
.venv\Scripts\python.exe scripts/run_sql_tests.py
.venv\Scripts\python.exe -m pytest tests/c -q
.venv\Scripts\python.exe experiments/c_concurrency/run.py
.venv\Scripts\python.exe experiments/c_backtest/run.py
```

B 的重建器会自动发现 `50_sim.sql / 60_research.sql`，不需要修改 B 的文件。A 的规则和 PIT 合成案例仍由 `setup_a_sample.py` 补齐。`pytest` 要求数据库名以 `_sample` 结尾，会创建独立测试用户，结束只清理这些用户的 C 业务行，不清空整库。

若 A/B 数据库已经准备好，使用下面的命令只安装 C。两个 SQL 在同一事务中执行，不删除数据库，不导入合成案例。当前版本可以重复执行；未来若修改已有表结构，应另外提供显式迁移。

```powershell
.venv\Scripts\python.exe -m app.trading.setup
```

两个页面分别启动：

```powershell
.venv\Scripts\python.exe -m streamlit run app/ui/pages/c_trading.py
.venv\Scripts\python.exe -m streamlit run app/ui/pages/c_backtest.py
```

页面使用演示用户 ID 选择身份，样本 `demo_user` 通常为 2。D 集成后应从可信登录会话取身份，不把用户输入 ID 当作正式身份认证。

## 5. 给 D 的接入方式

```python
from datetime import date
from app.trading import create_account, place_order, cancel_order, advance_day
from app.backtest import run_backtest, replay_backtest

account_id = create_account(owner_id, '练习账户', date(2025, 5, 13), 100000)
order_id = place_order(owner_id, account_id, 'sh.600003', 'B', 'OPEN', 100)
results = advance_day(owner_id, account_id)  # [(order_id, status, reason), ...]

run_id = run_backtest(owner_id, 'momentum', {'top_n': 3},
                      'SYNTH300', date(2025, 3, 3), date(2025, 5, 30))
new_run_id = replay_backtest(owner_id, run_id)
```

原分工的四个交易 SQL 函数签名、`run_backtest` 签名均保留；表的字段细化以 SQL 和数据库 COMMENT 为准。

仓库当前还没有 D 的 `app/db.py`。C 的 `app/trading/db.py` 是临时适配器：若已有 `app.db.transaction` 就自动委托，否则按同一参数约定建立 `app_user` 连接并使用事务级 `set_config`。没有创建或覆盖 D 的文件。D 的事务助手应返回 psycopg Connection，支持 `execute/cursor`，进入时设置可信用户上下文。

C 已对账户、订单、成交、流水、回测和净值启用 RLS，并对公开写函数检查所属用户；D 统一权限时保留这些边界。Agent 不应拿到账户写函数权限。`app.user_id` 自定义变量可被连接持有者修改，因此安全边界仍包括可信网关与登录，不能将数据库凭据或任意 SQL 能力给模型。

## 6. 已知边界与验收范围

- 样本是 A/B 的合成数据，不代表实盘收益或真实交易日历。
- 尚无公司行动明细，持仓跨除权事件会报错；已退市持仓缺少结算价格时也报错。缺少数据时不悄悄制造收益。补充真实现金分红、送转和退市结算数据后，才能扩展该部分。
- 上市前五个交易日暂不撮合；普通交易日费用和涨跌幅调用 A 的历史规则。当前规则覆盖 2024–2025 年，不能直接模拟 2026 年交易。
- 当前是日频、100 股整手、全量成交模型；不支持逐笔行情、融券、滑点、成交量参与率与部分成交。
- E11 在财报版本历史保留、行情规则未变的条件下已验证；原行情规则变化后，必须恢复备份，不能仅靠财报快照恢复全部输入。
- 本地已实现和测试的内容见 `docs/worklog/C.md`；需要其他成员审核并合并主干后，才满足分工文档的正式完成定义。
