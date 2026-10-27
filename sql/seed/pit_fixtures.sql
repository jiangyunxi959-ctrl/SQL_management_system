-- A 的合成测试数据。只用于 B 的 zhishi 合成库，绝不表示真实公司财报或指数成员。
-- 本文件可重复执行；来源固定为 SYNTHETIC-A，真实披露另用 CSV 导入。
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM etl.load_batch WHERE source='SAMPLE') THEN
        RAISE EXCEPTION '请先建立 B 的合成样本库；禁止向真实数据库混入 A 合成案例';
    END IF;
END $$;
INSERT INTO ref.security_name(stock_code,name,valid,source)
SELECT stock_code,'演示公司-'||stock_code,daterange(list_date,NULL,'[)'),'SYNTHETIC-A'
FROM ref.security ON CONFLICT DO NOTHING;
INSERT INTO ref.industry VALUES ('DEMO','教学行业',NULL),('DEMO_TECH','教学科技','DEMO')
ON CONFLICT DO NOTHING;
INSERT INTO ref.industry_member(stock_code,industry_code,valid,source)
SELECT stock_code,'DEMO_TECH',daterange(list_date,NULL,'[)'),'SYNTHETIC-A'
FROM ref.security ON CONFLICT DO NOTHING;

-- B 的逐日 isST -> 连续区间。仅覆盖实际观察的日期，不推断更早或更晚的状态。
WITH daily AS (
    SELECT DISTINCT ON (code,date) code,date::date AS d,
           CASE WHEN isST='1' THEN 'ST' ELSE 'NORMAL' END AS status
    FROM staging.daily_bar_raw WHERE isST IN ('0','1')
    ORDER BY code,date,batch_id DESC,raw_id DESC
), marked AS (
    SELECT *,CASE WHEN status IS DISTINCT FROM lag(status) OVER (PARTITION BY code ORDER BY d)
                  THEN 1 ELSE 0 END AS changed FROM daily
), grouped AS (
    SELECT *,sum(changed) OVER (PARTITION BY code ORDER BY d) AS segment FROM marked
), periods AS (
    SELECT code,status,segment,min(d) AS first_day,max(d) AS last_day FROM grouped
    GROUP BY code,status,segment
)
INSERT INTO ref.security_status(stock_code,status,valid,source)
SELECT code,status,daterange(first_day,
       coalesce(lead(first_day) OVER (PARTITION BY code ORDER BY first_day),last_day+1),'[)'),
       'SYNTHETIC-A'
FROM periods ON CONFLICT DO NOTHING;

INSERT INTO ref.index_info VALUES ('SYNTH300','教学指数（合成，并非沪深300）') ON CONFLICT DO NOTHING;
-- 一号股票到 2025-07-01 才退出；二号到该日才加入。其余始终在样本指数中。
INSERT INTO ref.index_member(index_code,stock_code,valid,source)
SELECT 'SYNTH300',stock_code,
    CASE WHEN stock_code='sh.600001' THEN daterange('2024-01-02','2025-07-01','[)')
         WHEN stock_code='sh.600002' THEN daterange('2025-07-01',NULL,'[)')
         ELSE daterange('2024-01-02',NULL,'[)') END,'SYNTHETIC-A'
FROM ref.security ON CONFLICT DO NOTHING;

-- 2025 一季报原值；ROE 是小数，净利润为归母、年初至今口径，EPS 是基本每股收益。
INSERT INTO fin.report(stock_code,report_period,announce_date,available_date,is_restated,
    revenue,net_profit,total_assets,total_equity,eps,roe,source,source_url,source_kind,
    period_basis,profit_basis,eps_basis,notes)
SELECT stock_code,'2025-03-31','2025-04-28','2025-04-28',false,
       1000000000,100000000,2000000000,1000000000,0.5,0.10,
       'SYNTHETIC-A','synthetic://quarter/original','SYNTHETIC','YTD','PARENT','BASIC','教学合成案例'
FROM ref.security ON CONFLICT DO NOTHING;
INSERT INTO fin.report(stock_code,report_period,announce_date,available_date,is_restated,
    revenue,net_profit,eps,roe,source,source_url,source_kind,period_basis,profit_basis,notes)
VALUES ('sh.600001','2025-03-31','2025-08-20','2025-08-20',true,
        900000000,80000000,0.4,0.08,'SYNTHETIC-A','synthetic://quarter/corrected','SYNTHETIC','YTD','PARENT','教学合成更正'),
       ('sh.600001','2025-06-30','2025-08-30','2025-08-30',false,
        1800000000,160000000,0.8,0.16,'SYNTHETIC-A','synthetic://half-year','SYNTHETIC','YTD','PARENT','周六公告测试')
ON CONFLICT DO NOTHING;
