# SQL_management_system

“知时”数据库课程大作业：按历史可见日期查询金融资料，进行因子研究和模拟交易。

| 模块 | 说明 |
|---|---|
| A：历史资料与财报 | [README_A.md](README_A.md) |
| B：行情与导入 | [README_B_1025.md](README_B_1025.md) |
| C：交易与回测 | [README_C.md](README_C.md)、[交接](README_C_HANDOFF.md) |
| D：统一入口、因子、费用与 AI | [README_D.md](README_D.md)、[交接](README_D_HANDOFF.md) |

当前 D 交付在 `d/factor-research-agent`，基于包含 A/B 的 C 分支。正式页面为 `app/ui/main.py`；先按 D 使用说明准备数据库和配置。原设计与实际实现的新增、调整及边界见 [当前交付与方案差异](docs/D_当前交付与方案差异.md)。系统不连接真实证券账户。
