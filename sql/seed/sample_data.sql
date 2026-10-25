-- B 的开发样本：20 只股票 × 2024-01-02 至 2025-12-31。
-- 明确标注为演示数据，不代表真实价格。
-- 包含：1 次复权事件（模拟分红）、1 个停牌日、1 段 ST 标记。

INSERT INTO core.app_user(username, display_name, role) VALUES
    ('demo_admin', '演示管理员', 'ADMIN'),
    ('demo_user',  '演示学习者', 'LEARNER')
ON CONFLICT (username) DO NOTHING;

INSERT INTO ref.trading_calendar(cal_date, is_open)
SELECT d::date, extract(isodow FROM d) BETWEEN 1 AND 5
FROM generate_series(date '2024-01-01', date '2025-12-31', interval '1 day') AS g(d)
ON CONFLICT (cal_date) DO UPDATE SET is_open = EXCLUDED.is_open;

WITH codes AS (
    SELECT i,
           CASE WHEN i <= 10
                THEN 'sh.' || lpad((600000 + i)::text, 6, '0')
                ELSE 'sz.' || lpad((i - 10)::text, 6, '0')
           END AS stock_code
    FROM generate_series(1,20) AS g(i)
)
INSERT INTO ref.security(stock_code, exchange, board, list_date, delist_date)
SELECT stock_code,
       CASE WHEN stock_code LIKE 'sh.%' THEN 'SH' ELSE 'SZ' END,
       'MAIN',
       date '2024-01-02',
       NULL
FROM codes
ON CONFLICT (stock_code) DO NOTHING;

DO $$
DECLARE
    v_batch_id bigint;
    v_changed  int;
BEGIN
    INSERT INTO etl.load_batch(source, target, params)
    VALUES ('SAMPLE', 'mkt.daily_bar', '{"kind":"synthetic","stocks":20,"start":"2024-01-02","end":"2025-12-31"}'::jsonb)
    RETURNING batch_id INTO v_batch_id;

    WITH codes AS (
        SELECT i,
               CASE WHEN i <= 10
                    THEN 'sh.' || lpad((600000 + i)::text, 6, '0')
                    ELSE 'sz.' || lpad((i - 10)::text, 6, '0')
               END AS stock_code
        FROM generate_series(1,20) AS g(i)
    ), days AS (
        SELECT cal_date AS d,
               row_number() OVER (ORDER BY cal_date) AS ord
        FROM ref.trading_calendar
        WHERE is_open AND cal_date BETWEEN date '2024-01-02' AND date '2025-12-31'
    ), price0 AS (
        SELECT c.i,
               c.stock_code,
               d.d,
               d.ord,
               round(((8 + c.i * 0.7 + d.ord * 0.012 + sin(d.ord::double precision / 13.0) * 0.35)
                     / CASE WHEN c.stock_code = 'sh.600001' AND d.d >= date '2025-06-16' THEN 1.08 ELSE 1 END)::numeric, 4) AS close0
        FROM codes c CROSS JOIN days d
    ), priced AS (
        SELECT *,
               coalesce(lag(close0) OVER (PARTITION BY stock_code ORDER BY d), close0) AS pre0
        FROM price0
    )
    INSERT INTO staging.daily_bar_raw(
        batch_id, date, code, open, high, low, close, preclose,
        volume, amount, adjustflag, turn, tradestatus, pctChg,
        peTTM, pbMRQ, psTTM, pcfNcfTTM, isST
    )
    SELECT v_batch_id,
           to_char(d, 'YYYY-MM-DD'),
           stock_code,
           round((close0 - 0.05)::numeric, 4)::text,
           round((close0 + 0.20)::numeric, 4)::text,
           round((close0 - 0.20)::numeric, 4)::text,
           close0::text,
           pre0::text,
           CASE WHEN stock_code = 'sh.600001' AND d = date '2025-05-15'
                THEN '0'
                ELSE (100000 + i * 5000 + ord * 100)::bigint::text
           END,
           CASE WHEN stock_code = 'sh.600001' AND d = date '2025-05-15'
                THEN '0'
                ELSE round((close0 * (100000 + i * 5000 + ord * 100))::numeric, 2)::text
           END,
           '3',
           round((0.5 + i * 0.03)::numeric, 6)::text,
           CASE WHEN stock_code = 'sh.600001' AND d = date '2025-05-15' THEN '0' ELSE '1' END,
           CASE WHEN pre0 = 0 THEN '0'
                ELSE round(((close0 / pre0 - 1) * 100)::numeric, 6)::text END,
           '', '', '', '',
           CASE WHEN stock_code = 'sz.000001'
                     AND d BETWEEN date '2025-03-03' AND date '2025-03-14'
                THEN '1' ELSE '0' END
    FROM priced;

    SELECT etl.fn_merge_daily_bar(v_batch_id) INTO v_changed;
    RAISE NOTICE 'sample daily_bar merged: % rows changed', v_changed;
END $$;

DO $$
DECLARE
    v_batch_id bigint;
    v_changed  int;
BEGIN
    INSERT INTO etl.load_batch(source, target, params)
    VALUES ('SAMPLE', 'mkt.adj_factor', '{"kind":"synthetic","event":"one dividend-like adjustment"}'::jsonb)
    RETURNING batch_id INTO v_batch_id;

    -- 一次模拟分红/除权事件：2025-06-16 起累计后复权因子变为 1.08。
    INSERT INTO staging.adjust_factor_raw(
        batch_id, code, "dividOperateDate", "foreAdjustFactor", "backAdjustFactor", "adjustFactor"
    ) VALUES (
        v_batch_id, 'sh.600001', '2025-06-16', '', '1.08000000', '1.08000000'
    );

    SELECT etl.fn_merge_adj_factor(v_batch_id) INTO v_changed;
    RAISE NOTICE 'sample adj_factor merged: % rows changed', v_changed;
END $$;

REFRESH MATERIALIZED VIEW mkt.mv_indicator_daily;
