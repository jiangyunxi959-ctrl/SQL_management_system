DO $$
DECLARE
    v_src_batch bigint;
    v_new_batch bigint;
    v_changed int;
    v_reported int;
BEGIN
    SELECT batch_id INTO v_src_batch
    FROM etl.load_batch
    WHERE source='SAMPLE' AND target='mkt.daily_bar'
    ORDER BY batch_id DESC LIMIT 1;

    INSERT INTO etl.load_batch(source, target, params)
    VALUES ('TEST', 'mkt.daily_bar', '{"case":"idempotency"}'::jsonb)
    RETURNING batch_id INTO v_new_batch;

    INSERT INTO staging.daily_bar_raw(
        batch_id, date, code, open, high, low, close, preclose, volume, amount,
        adjustflag, turn, tradestatus, pctChg, peTTM, pbMRQ, psTTM, pcfNcfTTM, isST
    )
    SELECT v_new_batch, date, code, open, high, low, close, preclose, volume, amount,
           adjustflag, turn, tradestatus, pctChg, peTTM, pbMRQ, psTTM, pcfNcfTTM, isST
    FROM staging.daily_bar_raw
    WHERE batch_id = v_src_batch;

    SELECT etl.fn_merge_daily_bar(v_new_batch) INTO v_changed;
    SELECT rows_upsert INTO v_reported FROM etl.load_batch WHERE batch_id=v_new_batch;

    IF v_changed <> 0 OR v_reported <> 0 THEN
        RAISE EXCEPTION 'ETL is not idempotent: returned %, recorded %', v_changed, v_reported;
    END IF;
END $$;
