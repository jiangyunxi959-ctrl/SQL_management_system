# A 与 B/C/D 的接口

## 数据流

```text
B 的 stock_basic / trade_dates CSV → A 的 ref 证券和日历
B 的逐日行情 → staging → B 校验入库 → A 的 ST 连续区间
核对过的财报 CSV → staging.financial_raw → fin.report
历史指数/行业区间 CSV → ref.index_member / industry_member
以上数据 + app.as_of_date → pit 视图和五个工具 → C / D
```

已有 B 数据库时执行 `python scripts/apply_a.py` 接入，整个接入操作在一个事务内完成。若 B 数据库是合成样本，再运行 `python scripts/setup_a_sample.py`。重新从零建库仍用 B 的 `rebuild_db.py`，它会自动采用正式 `10_ref.sql`。

## 五个工具

| 工具 | 参数与返回 |
|---|---|
| `pit.fn_screen` | `(text,jsonb,text,int)` → `stock_code,name,roe,eps,ret_20d,vol_20d` |
| `pit.fn_price_history` | `(varchar,date,date,text)`；复权 raw/hfq/qfq → 代码、日期、OHLC、成交量额、停牌标记 |
| `pit.fn_fundamentals` | `(varchar,int)` → `pit.fin_report`；报告期数 1–40 |
| `pit.fn_index_members` | `(text,date)` → 代码、名称；日期必须等于研究日期 |
| `pit.fn_industry_peers` | `(varchar)` → 同叶子行业的其他公司及行业名称 |

筛选字段仅支持 `roe,eps,ret_20d,ret_60d,vol_20d,amount_20d`，比较符为 `>,>=,<,<=`，至多十个条件。条件格式：`[{"field":"roe","op":">=","value":0.09}]`。排序降序、空值最后，条数为 1–100；名称未知时返回 NULL。

`fin_report` 保留报告期、公告日、可用日、财务值，并增加 `period_basis/profit_basis/eps_basis/source/source_url/source_kind/notes` 解释口径。缺失值为 NULL，不填零。PIT 不公开 `known_range/is_restated/sys_period`。

数据范围：当前名称从观察日起生效；ST 从逐日观察推导；行业与成员区间由外部历史证据输入。当前行业、当前指数成员不能自动还原多年前的历史。

## D：事务与权限

```python
# 使用 agent_reader 连接，并且让日期只在这个事务生效。
with connection.transaction():
    cursor.execute("SET TRANSACTION READ ONLY")
    cursor.execute("SELECT set_config('app.as_of_date', %s, true)", [as_of.isoformat()])
    cursor.execute("SELECT * FROM pit.fn_fundamentals(%s, %s)", [code, 4])
```

`agent_reader` 被撤销原始 `ref/fin/mkt` 的 schema 和表访问权，只能读 PIT。价格截断与财务截断在视图中统一完成。SQL 参数不直接拼接用户文本，选股采用显式 CASE 白名单。

`app.as_of_date` 是可信程序的上下文，而非不可修改的数据库锁。D 不把任意 SQL、改会话变量的工具暴露给模型；连接池也不能使用会话级 `SET` 让上个请求日期残留。安全审计、用户 RLS 和日志归 D。

## C：撮合规则与系统快照

费率与涨跌幅配置覆盖 2024–2025 年普通交易日，来源在 `reference_rules.sql` 每行。佣金明确假设万三、最低 5 元；印花税卖方万五；过户费双向十万分之一。手续费按每一类先四舍五入到分再加总，缺配置直接报错。

主板 ST 在本样本期间为 5%；2025 年的调整征求意见不是生效日，正式调整在 2026-07-06。无涨跌幅限制的新股初期等特殊日期应由 C 在调用前识别。本函数返回普通交易日规则，不承担完整撮合判断。

系统版本示例：ETL 批次提交后记录 `SELECT clock_timestamp()`，写入 C 的 `data_snapshot_at`；重放时通过 `set_config('app.data_snapshot_at', timestamp_text, true)` 设置。该值为空则查询当前库内版本。快照采集与财报写入串行，避免采集到尚未提交事务的中间时刻。

E11 的 A 侧脚本 `experiments/a_bias/e11_replay.py` 在回滚事务里修正一条 ROE：原始选股 19 只，当前值选股 18 只，恢复快照后重新选中原 19 只；固定价格下的原始与重放净值完全一致。C 还需固定行情、规则、策略版本、费用、成交参数与账户初值，才能完成整体回测复现。

## 验收方式

| 验证 | 覆盖 |
|---|---|
| B 原 SQL 测试与 pandas 对照 | 行情幂等、指标、EMA 一致性 |
| `test_ref_temporal.sql` | 六类区间表重叠拒绝、空区间、ST 边界、真实费率值 |
| `test_pit_leakage.sql` | 未设置日期、公告前、更正前、未来上市、未来成员及结束日、周末、前复权、非法筛选字段 |
| `test_fin_snapshot.sql` | 更新/删除后恢复旧财报；供应商快照不能倒填 |
| `test_pit_permissions.sql` | 受限角色可读工具、不能读原表或写入 |
| `check_a_python.py` | ST 连续段、板块映射、快照日期、缺失与零、成员采样缺口 |
| `check_a_integration.py` | 真正走 B CSV → A/B 入库；重复导入零变化；财务错误文件整批回滚 |
| Streamlit `AppTest` | 受限账号登录、页面加载、日期切换后的更正值 |

本轮测试使用 PostgreSQL 16.14、Python 3.12.14、pandas 2.3.3；合成样本有 20 只股票、10,440 条行情，B 的三项数据质量检查均为空。

## 与原设计的细化

增加 `available_date` 区分公告数据与供应商采集快照；增加财务口径和来源字段；增加可选 `app.data_snapshot_at`。原约定的五个函数名称、参数和选股六列输出保持一致。财报函数按完整 `pit.fin_report` 返回，D 应按列名取值。

研究日期保持自然日日终，行情另取最近交易日；未来才剔除的成员在剔除前仍保留，仅隐藏其未来结束日期。指数采样空档保留未知，拒绝把周采样伪装成精确历史变化日。
