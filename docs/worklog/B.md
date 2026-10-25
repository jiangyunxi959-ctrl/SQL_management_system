# B 工作日志：行情与计算层

## 2026-10-25：环境、数据与计算模块开发

- 环境：PostgreSQL 16、Python 3.11、虚拟环境；准备 `requirements.txt`、`.env.example`、`.gitignore` 与重建/测试脚本。
- SQL：`00_init.sql` 建立扩展、schema、角色、`core.app_user`；`20_mkt.sql` 建立 staging/etl、年度分区行情、复权因子及区间触发器、后复权视图、EMA 自定义聚合、指标物化视图及质量巡检。
- 合成样本：20 只证券、2024–2025 年共 10,440 条行情，含模拟复权、停牌和 ST 标记；`sql/dev/10_ref_stub.sql` 仅用于解除 A 的正式参考数据表尚未完成时的开发依赖。
- ETL：BaoStock 真实 CSV 下载及字段对照；按批次记录输入、实际变更和拒绝数量；通过重复导入验证幂等性。
- 修复：`rebuild_db.py` 中 `ALTER ROLE ... PASSWORD` 不能使用 `$1` 绑定参数，改为安全的 `psycopg.sql.Literal` 生成 SQL 字面量。

## 2026-10-26：本地执行与验收

### 1. 合成样本数据库 `zhishi`

- 重建成功：`python scripts/rebuild_db.py --yes`。
- 数量：证券 20、原始行情 10,440、复权因子 21、指标 10,440。
- 年度分区：2024 年和 2025 年各 5,220 行；针对 2024 年范围的查询执行计划只扫描 `daily_bar_2024`（分区裁剪功能验证；不是完整 E3 性能对比实验）。
- 样本复权因子在 2025-06-16 从 1 变为 1.08，后复权公式全量核对 10,440 行、0 不一致。
- 自动化测试：`test_etl_idempotency.sql`、`test_mkt_basic.sql`、`test_mkt_indicator.sql` 全部通过；EMA12 最大误差 3.55e-15，EMA26 最大误差 5.33e-15；质量巡检 DQ=(0,0,0)。

### 2. 真实数据测试库 `zhishi_real`

- BaoStock 下载：20 只 A 股，731 个自然日（485 个交易日）、9,700 条不复权日线、459 条复权事件；无重复行情键和重复事件键，日线 `adjustflag=3`。
- 初始化：在 `zhishi_real` 中执行 `00_init.sql`、开发用 `10_ref_stub.sql`、`20_mkt.sql`，导入真实证券 20 只、日历 731 天。Windows 下 `psql` 通过设置 `PGCLIENTENCODING=UTF8` 解决 SQL 中文编码问题。
- 行情批次 1：`rows_in/upsert/reject=(9700,9700,0)`；批次 2 重复导入：`(9700,0,0)`，最终 9,700 条行情。
- 因子批次 3：`rows_in/upsert/reject=(459,460,0)`（含初始化/事件合并的实际变更次数）；最终 459 条因子；批次 4 重复导入 `(459,0,0)`。
- 复权视图 9,700 行；缺失行情、非交易日行情、缺失因子三项质量巡检均为 0。
- 刷新物化视图成功；共 9,700 条指标，MA20 非空 9,320、MA60 非空 8,520、EMA12/EMA26 均非空 9,700。
- `scripts/verify_real_indicators.py`：SQL 与 pandas 核对 9 项指标，全部通过，0 不一致记录。EMA12 最大误差 5.684e-14，EMA26 最大误差 8.527e-14；其余指标亦通过。
- 复权区间检查：459 条因子覆盖 20 只证券，区间起点错误 0、衔接错误 0、空区间 0、最终无上界区间 20；故意制造重叠区间的事务被 GiST 排他约束拒绝，随后 `ROLLBACK`。

### 3. 备份

- 使用 PostgreSQL 16.15 `pg_dump -Fc` 生成：`data/dumps/zhishi.dump`（671,710 bytes）、`data/dumps/zhishi_real.dump`（1,293,104 bytes）。
- 两个归档均能通过 `pg_restore -l` 读取目录；**尚未进行完整的恢复演练**，不能把此项记为完成。

### 4. 协作、未完成项与限制

- 正式 `ref.security`、交易日历及历史时态参考数据归 A 负责。当前两库的 `ref` stub/导入工具为 DEV-ONLY；等正式 `sql/10_ref.sql` 合并后需要再做集成测试。
- 真实数据样本范围只有 20 只证券的 2024–2025 年日线，尚未覆盖设计文档目标的长期全量数据规模；数据因子未逐条对照官方除权公告。
- 未完成：数据质量展示页、E1–E4 与 E8 的完整性能实验、完整备份恢复演练、Git 仓库提交与 PR。
- 下一步：检查 `.gitignore`，提交 B 负责的源码、SQL、测试与文档；与 A 合并正式 `ref`；开展后续实验并归档执行计划和耗时。
