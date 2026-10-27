DO $$
DECLARE v_snapshot timestamptz; v_profit numeric; v_count int;
BEGIN
    v_snapshot := clock_timestamp();
    UPDATE fin.report SET net_profit=99000000 WHERE stock_code='sh.600001'
        AND report_period='2025-03-31' AND announce_date='2025-04-28';
    PERFORM set_config('app.as_of_date','2025-05-01',true);
    SELECT net_profit INTO v_profit FROM pit.fin_latest WHERE stock_code='sh.600001';
    IF v_profit IS DISTINCT FROM 99000000 THEN RAISE EXCEPTION '本地更正未生效'; END IF;
    PERFORM set_config('app.data_snapshot_at',v_snapshot::text,true);
    SELECT net_profit INTO v_profit FROM pit.fin_latest WHERE stock_code='sh.600001';
    IF v_profit IS DISTINCT FROM 100000000 THEN RAISE EXCEPTION 'PIT 未读取系统历史版本'; END IF;
    DELETE FROM fin.report WHERE stock_code='sh.600001' AND report_period='2025-03-31' AND announce_date='2025-04-28';
    SELECT count(*) INTO v_count FROM fin.fn_report_as_recorded(v_snapshot)
        WHERE stock_code='sh.600001' AND report_period='2025-03-31' AND announce_date='2025-04-28';
    IF v_count<>1 THEN RAISE EXCEPTION '删除后旧快照不能唯一恢复'; END IF;
    SELECT net_profit INTO v_profit FROM pit.fin_latest WHERE stock_code='sh.600001';
    IF v_profit IS DISTINCT FROM 100000000 THEN RAISE EXCEPTION '删除后 PIT 历史快照错误'; END IF;
    PERFORM set_config('app.data_snapshot_at','',true);
    INSERT INTO fin.report(stock_code,report_period,announce_date,available_date,net_profit,
      source,source_url,source_kind,period_basis,profit_basis)
    VALUES ('sh.600001','2024-12-31','2025-04-20','2026-10-26',123,
      'TEST','https://example.test/provider','PROVIDER_SNAPSHOT','ANNUAL','CONSOLIDATED');
    IF EXISTS (SELECT 1 FROM pit.fin_report WHERE stock_code='sh.600001' AND report_period='2024-12-31') THEN
        RAISE EXCEPTION '供应商最新快照被错误回填到历史'; END IF;
END $$;
