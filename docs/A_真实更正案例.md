# 三组真实报告期更正案例

文件：`etl/temporal/restatement_examples.csv`。共两家公司、三个报告期、六条披露记录。金额均为人民币元，净利润为归母口径；季度报告采用年初累计值。空白字段表示本次未核对，不补成零。

| 公司与报告期 | 原公告日 | 更正公告日 | 原归母净利润 | 更正后归母净利润 |
|---|---|---|---:|---:|
| 科创信息 2023 半年度 | 2023-08-19 | 2024-04-27 | -17,770,558.17 | -68,702,895.64 |
| 科创信息 2023 前三季度 | 2023-10-28 | 2024-04-27 | -8,880,399.15 | -121,030,500.50 |
| 东尼电子 2022 年度 | 2023-03-11 | 2024-04-27 | 108,049,291.94 | 78,967,390.19 |

科创信息因特定项目回款存在重大不确定性而调减收入、计提坏账；东尼电子调整研发半成品、原材料等相关会计处理。两者说明：报告期结束日和更正信息公开日相隔很久，旧日期的研究应继续看到旧值。

每条记录分别指向对应原报告或更正公告，公告正文由新浪承载，发布主体为上市公司。本案例收录截至 2024-04-27 的版本；科创信息此后还有更正，扩展研究日期时需要继续补齐公告。

## 原始证据

- [科创信息原半年报](https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=9418093&stockid=300730)：合并利润表。
- [科创信息原三季报](https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=9600179&stockid=300730)：年初至报告期末合并利润表。
- [科创信息 2024-021 更正公告](https://money.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=10119771&stockid=300730)：第 1、3 项合并报表。
- [东尼电子原年报](https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=8877345&stockid=603595)：主要会计数据、合并利润表。
- [东尼电子 2024-018 更正公告](https://money.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=10121896&stockid=603595)：主要会计数据更正前后。
- [科创信息上市公告](https://money.finance.sina.com.cn/corp/view/vISSUE_MarketBulletinDetail.php?id=3910507&stockid=300730)、[东尼电子上市公告](https://money.finance.sina.com.cn/corp/view/vISSUE_MarketBulletinDetail.php?id=3551966&stockid=603595)：用于证券主表上市日期。

## 使用

将 `.env` 的 `PGDATABASE` 改为独立的 `zhishi_a_cases`，确认该库可重建后执行：

```powershell
python scripts/rebuild_db.py --yes --no-seed
python etl/temporal/load_restatement_examples.py
```

第二次导入应显示“实际变化 0”。在事务内设置 `app.as_of_date='2024-04-26'` 与 `'2024-04-27'`，分别查询 `pit.fn_fundamentals('sz.300730',4)`，可以直接观察前后两组值。此独立库没有行情，不用于运行 B 的合成行情测试。
