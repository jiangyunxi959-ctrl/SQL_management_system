DO $$
DECLARE u bigint; rid bigint; dd numeric;
BEGIN
 SELECT user_id INTO u FROM core.app_user WHERE username='demo_user';
 PERFORM set_config('app.user_id',u::text,true);
 rid:=research.fn_run_momentum(u,'{"top_n":3,"initial_cash":100000}','SYNTH300','2025-03-03','2025-03-14');
 IF (SELECT status FROM research.backtest_run WHERE run_id=rid)<>'COMPLETED' THEN RAISE EXCEPTION 'SQL 回测未完成'; END IF;
 IF (SELECT count(*) FROM research.backtest_nav WHERE run_id=rid)<>10 THEN RAISE EXCEPTION 'SQL 回测交易日数量错误'; END IF;
 IF EXISTS(SELECT 1 FROM research.backtest_nav WHERE run_id=rid AND (nav<0 OR drawdown>0)) THEN RAISE EXCEPTION '净值或回撤越界'; END IF;
 SELECT -min(drawdown) INTO dd FROM research.backtest_nav WHERE run_id=rid;
 IF dd IS DISTINCT FROM (SELECT max_drawdown FROM research.backtest_run WHERE run_id=rid) THEN RAISE EXCEPTION '最大回撤不一致'; END IF;
 IF EXISTS(SELECT 1 FROM research.backtest_trade t JOIN sim.orders o USING(order_id) WHERE t.run_id=rid AND o.exec_date<=o.submitted_date) THEN
   RAISE EXCEPTION '策略使用日终信息在当日成交'; END IF;
END $$;
