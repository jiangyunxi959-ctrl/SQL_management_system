DO $$
DECLARE v_profit numeric; v_count int; v_upper date;
BEGIN
    PERFORM set_config('app.as_of_date','',true);
    BEGIN PERFORM pit.as_of(); RAISE EXCEPTION 'missing date was accepted' USING ERRCODE='23514';
    EXCEPTION WHEN raise_exception THEN NULL; END;
    PERFORM set_config('app.as_of_date','2025-04-27',true);
    IF EXISTS (SELECT 1 FROM pit.fin_report) THEN RAISE EXCEPTION '公告前看到了财报'; END IF;
    PERFORM set_config('app.as_of_date','2025-05-01',true);
    SELECT net_profit INTO v_profit FROM pit.fin_latest WHERE stock_code='sh.600001';
    IF v_profit IS DISTINCT FROM 100000000 THEN RAISE EXCEPTION '原始值错误 %',v_profit; END IF;
    IF EXISTS (SELECT 1 FROM pit.daily_bar_hfq WHERE trade_date>date '2025-05-01') THEN
        RAISE EXCEPTION '行情泄漏'; END IF;
    SELECT upper(valid) INTO v_upper FROM pit.index_member WHERE stock_code='sh.600001';
    IF v_upper<>date '2025-05-02' THEN RAISE EXCEPTION '未来剔除日期泄漏 %',v_upper; END IF;
    IF EXISTS (SELECT 1 FROM pit.index_member WHERE stock_code='sh.600002') THEN
        RAISE EXCEPTION '未来加入的成员泄漏'; END IF;
    SELECT count(*) INTO v_count FROM pit.fn_index_members('SYNTH300','2025-05-01');
    IF v_count<>19 THEN RAISE EXCEPTION '历史成员数量错误 %',v_count; END IF;
    SELECT count(*) INTO v_count FROM pit.fn_screen('SYNTH300','[{"field":"roe","op":">=","value":0.10}]','ret_20d',5);
    IF v_count<>5 THEN RAISE EXCEPTION '筛选工具或返回列错误 %',v_count; END IF;
    BEGIN
        PERFORM * FROM pit.fn_screen('SYNTH300','[]','roe; DROP TABLE fin.report',5);
        RAISE EXCEPTION '非法排序被接受' USING ERRCODE='23514';
    EXCEPTION WHEN raise_exception THEN NULL; END;
    PERFORM set_config('app.as_of_date','2025-08-21',true);
    SELECT net_profit INTO v_profit FROM pit.fin_latest WHERE stock_code='sh.600001';
    IF v_profit IS DISTINCT FROM 80000000 THEN RAISE EXCEPTION '更正版错误 %',v_profit; END IF;
    -- 8 月 30 日是周六：最新行情来自周五，财报可用周六新公告。
    PERFORM set_config('app.as_of_date','2025-08-30',true);
    SELECT net_profit INTO v_profit FROM pit.fin_latest WHERE stock_code='sh.600001';
    IF v_profit IS DISTINCT FROM 160000000 OR pit.market_date()<>date '2025-08-29' THEN
        RAISE EXCEPTION '周末研究时间被错误回退'; END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='pit' AND table_name='fin_report'
               AND column_name IN ('known_range','is_restated','sys_period')) THEN
        RAISE EXCEPTION 'PIT 暴露版本内部信息'; END IF;
    -- 虚构未来上市证券，仅在本次回滚测试中存在。
    INSERT INTO ref.security VALUES ('sh.699999','SH','MAIN','2026-01-01',NULL);
    IF EXISTS (SELECT 1 FROM pit.security WHERE stock_code='sh.699999') THEN RAISE EXCEPTION '未来上市泄漏'; END IF;
    PERFORM set_config('app.as_of_date','2025-05-01',true);
    IF EXISTS (SELECT 1 FROM pit.fn_price_history('sh.600001','2025-01-01','2025-12-31','qfq')
               WHERE trade_date>date '2025-05-01') THEN RAISE EXCEPTION '工具未来日期泄漏'; END IF;
    IF (SELECT close FROM pit.daily_bar_qfq WHERE stock_code='sh.600001' AND trade_date='2025-04-30')
       IS DISTINCT FROM (SELECT close FROM mkt.daily_bar WHERE stock_code='sh.600001' AND trade_date='2025-04-30') THEN
        RAISE EXCEPTION '前复权误用了六月以后的因子'; END IF;
END $$;
