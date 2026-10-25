DO $$
DECLARE
    v_stocks int;
    v_bars bigint;
    v_ind bigint;
    v_susp int;
    v_missing bigint;
    v_closed bigint;
    v_no_factor bigint;
BEGIN
    SELECT count(*) INTO v_stocks FROM ref.security;
    IF v_stocks <> 20 THEN
        RAISE EXCEPTION 'expected 20 securities, got %', v_stocks;
    END IF;

    SELECT count(*) INTO v_bars FROM mkt.daily_bar;
    IF v_bars < 10000 THEN
        RAISE EXCEPTION 'expected >10000 daily bars, got %', v_bars;
    END IF;

    SELECT count(*) INTO v_ind FROM mkt.mv_indicator_daily;
    IF v_ind <> v_bars THEN
        RAISE EXCEPTION 'indicator row count % != daily bar row count %', v_ind, v_bars;
    END IF;

    SELECT count(*) INTO v_susp
    FROM mkt.daily_bar
    WHERE stock_code='sh.600001' AND trade_date=date '2025-05-15' AND is_suspended;
    IF v_susp <> 1 THEN
        RAISE EXCEPTION 'suspension fixture missing';
    END IF;

    SELECT count(*) INTO v_missing FROM etl.dq_missing_bar;
    SELECT count(*) INTO v_closed FROM etl.dq_bar_on_closed_day;
    SELECT count(*) INTO v_no_factor FROM etl.dq_bar_without_factor;
    IF v_missing <> 0 OR v_closed <> 0 OR v_no_factor <> 0 THEN
        RAISE EXCEPTION 'DQ failed: missing %, closed %, no_factor %', v_missing, v_closed, v_no_factor;
    END IF;
END $$;
