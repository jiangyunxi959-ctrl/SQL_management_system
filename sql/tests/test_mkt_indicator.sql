DO $$
DECLARE
    v_n int;
    v_factor_before numeric;
    v_factor_after numeric;
    v_raw numeric;
    v_hfq numeric;
BEGIN
    -- 第 20 条应有 MA20；第 60 条应有 MA60；ret_20d 从第 21 条开始。
    SELECT count(*) INTO v_n
    FROM (
        SELECT row_number() OVER (ORDER BY trade_date) AS rn, *
        FROM mkt.mv_indicator_daily
        WHERE stock_code='sh.600001'
    ) t
    WHERE (rn < 20 AND ma20 IS NOT NULL)
       OR (rn = 20 AND ma20 IS NULL)
       OR (rn < 60 AND ma60 IS NOT NULL)
       OR (rn = 60 AND ma60 IS NULL)
       OR (rn <= 20 AND ret_20d IS NOT NULL)
       OR (rn = 21 AND ret_20d IS NULL);
    IF v_n <> 0 THEN
        RAISE EXCEPTION 'MA/return warm-up semantics failed, % bad rows', v_n;
    END IF;

    SELECT back_factor INTO v_factor_before
    FROM mkt.v_bar_hfq
    WHERE stock_code='sh.600001' AND trade_date=date '2025-06-13';
    SELECT back_factor INTO v_factor_after
    FROM mkt.v_bar_hfq
    WHERE stock_code='sh.600001' AND trade_date=date '2025-06-16';
    IF v_factor_before <> 1 OR v_factor_after <> 1.08 THEN
        RAISE EXCEPTION 'adjust factor interval wrong: before %, after %', v_factor_before, v_factor_after;
    END IF;

    SELECT b.close, h.close INTO v_raw, v_hfq
    FROM mkt.daily_bar b
    JOIN mkt.v_bar_hfq h USING(stock_code, trade_date)
    WHERE b.stock_code='sh.600001' AND b.trade_date=date '2025-06-16';
    IF abs(v_hfq - v_raw * 1.08) > 0.000001 THEN
        RAISE EXCEPTION 'HFQ formula wrong: raw %, hfq %', v_raw, v_hfq;
    END IF;
END $$;
