-- C：SQL 基准与 Python 策略共用同一撮合规则，避免将模型差异误认为计算错误。
CREATE TABLE IF NOT EXISTS research.strategy (
    strategy_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    template text NOT NULL UNIQUE CHECK(template IN ('momentum','ma_cross','quality')),
    description text NOT NULL
);
INSERT INTO research.strategy(template,description) VALUES
 ('momentum','历史指数成分股内按 20 日动量降序'),
 ('ma_cross','MA20 大于 MA60，再按 20 日动量排序'),
 ('quality','按研究日已可见的最近财报 ROE 排序') ON CONFLICT(template) DO NOTHING;
CREATE TABLE IF NOT EXISTS research.backtest_run (
    run_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id bigint NOT NULL REFERENCES core.app_user,
    strategy_id bigint NOT NULL REFERENCES research.strategy,
    account_id bigint NOT NULL UNIQUE REFERENCES sim.account,
    engine text NOT NULL CHECK(engine IN ('SQL','PYTHON')),
    universe text NOT NULL,
    start_date date NOT NULL,
    end_date date NOT NULL CHECK(end_date>start_date),
    params jsonb NOT NULL,
    data_snapshot_at timestamptz NOT NULL,
    data_fingerprint text NOT NULL,
    engine_version text NOT NULL DEFAULT 'c-v1',
    status text NOT NULL DEFAULT 'RUNNING' CHECK(status IN ('RUNNING','COMPLETED')),
    max_drawdown numeric,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS research.backtest_nav (
    run_id bigint NOT NULL REFERENCES research.backtest_run,
    trade_date date NOT NULL,
    equity numeric(20,2) NOT NULL CHECK(equity>=0),
    nav numeric NOT NULL CHECK(nav>=0),
    drawdown numeric,
    PRIMARY KEY(run_id,trade_date)
);
CREATE TABLE IF NOT EXISTS research.backtest_trade (
    run_id bigint NOT NULL REFERENCES research.backtest_run,
    order_id bigint NOT NULL UNIQUE REFERENCES sim.orders,
    PRIMARY KEY(run_id,order_id)
);

CREATE OR REPLACE FUNCTION research.dataset_fingerprint() RETURNS text
LANGUAGE sql STABLE SET search_path=pg_catalog,ref,mkt AS $$
 SELECT md5(jsonb_build_array(
  (SELECT jsonb_agg(to_jsonb(x) ORDER BY stock_code,trade_date) FROM mkt.daily_bar x),
  (SELECT jsonb_agg(to_jsonb(x) ORDER BY stock_code,ex_date) FROM mkt.adj_factor x),
  (SELECT jsonb_agg(to_jsonb(x) ORDER BY stock_code,trade_date) FROM mkt.mv_indicator_daily x),
  (SELECT jsonb_agg(to_jsonb(x) ORDER BY stock_code) FROM ref.security x),
  (SELECT jsonb_agg(to_jsonb(x) ORDER BY stock_code,valid) FROM ref.security_status x),
  (SELECT jsonb_agg(to_jsonb(x) ORDER BY index_code,stock_code,valid) FROM ref.index_member x),
  (SELECT jsonb_agg(to_jsonb(x) ORDER BY cal_date) FROM ref.trading_calendar x),
  (SELECT jsonb_agg(to_jsonb(x) ORDER BY rule_id) FROM ref.fee_rule x),
  (SELECT jsonb_agg(to_jsonb(x) ORDER BY rule_id) FROM ref.price_limit_rule x)
 )::text)
$$;

CREATE OR REPLACE FUNCTION research.fn_candidates(p_universe text)
RETURNS TABLE(stock_code varchar,close_price numeric,ret_20d numeric,ma20 numeric,ma60 numeric,roe numeric)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pit,ref AS $$
 SELECT s.stock_code,b.close,i.ret_20d,i.ma20,i.ma60,f.roe
 FROM pit.security s JOIN pit.daily_bar_raw b ON b.stock_code=s.stock_code AND b.trade_date=pit.as_of()
 JOIN pit.indicator_daily i ON i.stock_code=s.stock_code AND i.trade_date=b.trade_date
 JOIN pit.index_member m ON m.stock_code=s.stock_code AND m.index_code=p_universe AND m.valid @> pit.as_of()
 LEFT JOIN pit.fin_latest f ON f.stock_code=s.stock_code
 WHERE NOT b.is_suspended AND b.close>0 AND (s.delist_date IS NULL OR s.delist_date>pit.as_of())
$$;
CREATE OR REPLACE FUNCTION research.fn_begin(p_owner bigint,p_template text,p_params jsonb,p_universe text,
 p_start date,p_end date,p_engine text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,research,sim,ref AS $$
DECLARE aid bigint; rid bigint; sid bigint; cash numeric; n int; fingerprint text; original research.backtest_run; snapshot timestamptz;
BEGIN
 IF p_owner IS DISTINCT FROM sim.require_user() THEN RAISE EXCEPTION '回测用户不匹配' USING ERRCODE='42501'; END IF;
 IF p_params IS NULL OR jsonb_typeof(p_params)<>'object' THEN RAISE EXCEPTION '参数必须为 JSON 对象'; END IF;
 IF EXISTS(SELECT 1 FROM jsonb_object_keys(p_params) k WHERE k NOT IN ('top_n','initial_cash','data_snapshot_at','replay_of')) THEN
   RAISE EXCEPTION '未知回测参数'; END IF;
 n:=coalesce((p_params->>'top_n')::int,5); cash:=coalesce((p_params->>'initial_cash')::numeric,100000);
 IF n<1 OR n>20 OR cash<=0 OR p_start IS NULL OR p_end IS NULL OR p_start>=p_end THEN RAISE EXCEPTION '回测参数范围不合法'; END IF;
 IF NOT EXISTS(SELECT 1 FROM ref.trading_calendar WHERE is_open AND cal_date=p_end) THEN RAISE EXCEPTION '结束日必须是已知交易日'; END IF;
 IF NOT EXISTS(SELECT 1 FROM ref.index_info WHERE index_code=p_universe) THEN RAISE EXCEPTION '未知指数股票池'; END IF;
 SELECT strategy_id INTO sid FROM research.strategy WHERE template=p_template;
 IF sid IS NULL THEN RAISE EXCEPTION '未知策略模板'; END IF;
 -- 整次回测阻止基础数据写入；市场数据和财报 ETL 在回测之外执行。
 LOCK TABLE ref.security,ref.security_status,ref.index_member,ref.trading_calendar,ref.fee_rule,ref.price_limit_rule,
            mkt.daily_bar,mkt.adj_factor,fin.report,fin.report_history IN SHARE MODE;
 fingerprint:=research.dataset_fingerprint();
 snapshot:=coalesce((p_params->>'data_snapshot_at')::timestamptz,clock_timestamp());
 IF p_params->>'replay_of' IS NOT NULL THEN
   SELECT * INTO original FROM research.backtest_run WHERE run_id=(p_params->>'replay_of')::bigint AND owner_id=p_owner;
   IF NOT FOUND OR original.status<>'COMPLETED' THEN RAISE EXCEPTION '原回测不存在、未完成或无权访问'; END IF;
   IF original.data_fingerprint<>fingerprint THEN RAISE EXCEPTION '行情或规则已变化，请先恢复原数据备份再重放'; END IF;
   IF original.strategy_id<>sid OR original.universe<>p_universe OR original.start_date<>p_start OR original.end_date<>p_end
     OR coalesce((original.params->>'top_n')::int,5)<>n OR coalesce((original.params->>'initial_cash')::numeric,100000)<>cash
     OR original.engine_version<>'c-v1' THEN RAISE EXCEPTION '重放的策略、参数或引擎版本不一致'; END IF;
   snapshot:=original.data_snapshot_at;
 END IF;
 aid:=sim.fn_create_account(p_owner,'回测-'||p_template,p_start,cash);
 INSERT INTO research.backtest_run(owner_id,strategy_id,account_id,engine,universe,start_date,end_date,params,data_snapshot_at,data_fingerprint)
 VALUES(p_owner,sid,aid,p_engine,p_universe,p_start,p_end,p_params,
        snapshot,fingerprint) RETURNING run_id INTO rid;
 INSERT INTO research.backtest_nav(run_id,trade_date,equity,nav) VALUES(rid,p_start,cash,1);
 RETURN rid;
END $$;
CREATE OR REPLACE FUNCTION research.require_run(p_run bigint) RETURNS research.backtest_run
LANGUAGE plpgsql SET search_path=pg_catalog,research,sim AS $$
DECLARE r research.backtest_run;
BEGIN
 SELECT * INTO r FROM research.backtest_run WHERE run_id=p_run AND owner_id=sim.require_user();
 IF NOT FOUND THEN RAISE EXCEPTION '回测不存在或无权访问' USING ERRCODE='42501'; END IF;
 PERFORM sim.lock_account(r.account_id);
 IF r.status<>'RUNNING' THEN RAISE EXCEPTION '回测已经结束'; END IF;
 RETURN r;
END $$;
CREATE OR REPLACE FUNCTION research.fn_prepare_day(p_run bigint) RETURNS date
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,research,sim AS $$
DECLARE r research.backtest_run; d date;
BEGIN
 r:=research.require_run(p_run);
 SELECT as_of_date INTO d FROM sim.account WHERE account_id=r.account_id;
 PERFORM set_config('app.as_of_date',d::text,true);
 PERFORM set_config('app.data_snapshot_at',r.data_snapshot_at::text,true);
 RETURN d;
END $$;
CREATE OR REPLACE FUNCTION research.fn_step(p_run bigint,p_codes varchar[]) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,research,sim,ref,mkt AS $$
DECLARE r research.backtest_run; a sim.account; pos record; code varchar; price numeric; wanted int; have int;
        budget numeric; unit numeric; free_cash numeric; n int; value numeric;
BEGIN
 r:=research.require_run(p_run); a:=sim.lock_account(r.account_id);
 IF a.as_of_date>=r.end_date THEN RAISE EXCEPTION '回测日期已结束'; END IF;
 IF cardinality(p_codes)>coalesce((r.params->>'top_n')::int,5)
   OR cardinality(p_codes)<>(SELECT count(DISTINCT x) FROM unnest(p_codes) x) THEN RAISE EXCEPTION '选股列表重复或超过数量'; END IF;
 PERFORM research.fn_prepare_day(p_run);
 IF EXISTS(SELECT 1 FROM unnest(p_codes) c WHERE NOT EXISTS(SELECT 1 FROM research.fn_candidates(r.universe) x WHERE x.stock_code=c)) THEN
   RAISE EXCEPTION '选股不属于当日 PIT 候选池'; END IF;
 -- 不预支未成交卖单的收入，卖出回款下一次决策才能投入；已持有且仍入选的股票保留。
 FOR pos IN SELECT * FROM sim.position WHERE account_id=r.account_id AND qty>0 AND NOT(stock_code=ANY(p_codes)) ORDER BY stock_code LOOP
   IF EXISTS(SELECT 1 FROM mkt.daily_bar WHERE stock_code=pos.stock_code AND trade_date=a.as_of_date AND NOT is_suspended) THEN
     PERFORM sim.fn_place_order(r.account_id,pos.stock_code,'S','OPEN',pos.qty,NULL);
   END IF;
 END LOOP;
 SELECT a.cash+coalesce(sum(p.qty*b.close),0) INTO value FROM sim.position p
 LEFT JOIN mkt.daily_bar b ON b.stock_code=p.stock_code AND b.trade_date=a.as_of_date WHERE p.account_id=r.account_id;
 budget:=value/greatest(cardinality(p_codes),1);
 FOREACH code IN ARRAY p_codes LOOP
   SELECT close INTO price FROM mkt.daily_bar WHERE stock_code=code AND trade_date=a.as_of_date;
   SELECT coalesce(sum(qty),0) INTO have FROM sim.position WHERE account_id=r.account_id AND stock_code=code;
   SELECT cash-frozen_cash INTO free_cash FROM sim.account WHERE account_id=r.account_id;
   -- 留出普通涨停上界及费用，避免用未来开盘价决定今天买多少。
   unit:=round(price*(1+ref.fn_limit_pct(code,a.as_of_date)),2);
   wanted:=greatest(0,floor((budget/price-have)/100)::int*100);
   n:=least(wanted,greatest(0,floor((free_cash-10)/(unit*100*1.002))::int*100));
   WHILE n>0 AND unit*n+ref.fn_fee(a.as_of_date,'B',unit*n)>free_cash LOOP n:=n-100; END LOOP;
   IF n>0 THEN PERFORM sim.fn_place_order(r.account_id,code,'B','OPEN',n,NULL); END IF;
 END LOOP;
 PERFORM sim.fn_advance_day(r.account_id);
 SELECT * INTO a FROM sim.account WHERE account_id=r.account_id;
 -- 缺失持仓报价不能悄悄按零估值。停牌沿用最新已知收盘价。
 IF EXISTS(SELECT 1 FROM sim.position p WHERE p.account_id=r.account_id AND p.qty>0
    AND NOT EXISTS(SELECT 1 FROM mkt.daily_bar b WHERE b.stock_code=p.stock_code AND b.trade_date<=a.as_of_date)) THEN
    RAISE EXCEPTION '持仓估值行情缺失'; END IF;
 SELECT a.cash+coalesce(sum(p.qty*b.close),0) INTO value FROM sim.position p
 LEFT JOIN LATERAL (SELECT close FROM mkt.daily_bar WHERE stock_code=p.stock_code AND trade_date<=a.as_of_date ORDER BY trade_date DESC LIMIT 1) b ON true
 WHERE p.account_id=r.account_id;
 INSERT INTO research.backtest_nav(run_id,trade_date,equity,nav) VALUES(p_run,a.as_of_date,value,value/a.initial_cash);
 INSERT INTO research.backtest_trade SELECT p_run,o.order_id FROM sim.orders o WHERE o.account_id=r.account_id ON CONFLICT DO NOTHING;
END $$;
CREATE OR REPLACE FUNCTION research.fn_finish(p_run bigint) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,research,sim AS $$
DECLARE r research.backtest_run;
BEGIN
 r:=research.require_run(p_run);
 IF (SELECT as_of_date FROM sim.account WHERE account_id=r.account_id)<>r.end_date THEN RAISE EXCEPTION '尚未到达结束日'; END IF;
 IF research.dataset_fingerprint()<>r.data_fingerprint THEN
   RAISE EXCEPTION '回测期间基础数据或指标发生变化，本次结果已回滚，请停止 ETL 后重试'; END IF;
 WITH dd AS (SELECT trade_date,nav/nullif(max(nav) OVER(ORDER BY trade_date),0)-1 AS d
             FROM research.backtest_nav WHERE run_id=p_run)
 UPDATE research.backtest_nav n SET drawdown=dd.d FROM dd WHERE n.run_id=p_run AND n.trade_date=dd.trade_date;
 UPDATE research.backtest_run SET status='COMPLETED',max_drawdown=(SELECT -min(drawdown) FROM research.backtest_nav WHERE run_id=p_run) WHERE run_id=p_run;
 IF EXISTS(SELECT 1 FROM sim.v_cash_reconcile WHERE account_id=r.account_id) THEN RAISE EXCEPTION '回测资金对账失败'; END IF;
END $$;
CREATE OR REPLACE FUNCTION research.fn_run_momentum(p_owner bigint,p_params jsonb,p_universe text,p_start date,p_end date)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,research,sim AS $$
DECLARE rid bigint; d date; codes varchar[]; n int:=coalesce((p_params->>'top_n')::int,5);
BEGIN
 rid:=research.fn_begin(p_owner,'momentum',p_params,p_universe,p_start,p_end,'SQL');
 LOOP
   d:=research.fn_prepare_day(rid); EXIT WHEN d>=p_end;
   SELECT coalesce(array_agg(stock_code ORDER BY ret_20d DESC,stock_code),ARRAY[]::varchar[]) INTO codes FROM
     (SELECT stock_code,ret_20d FROM research.fn_candidates(p_universe) WHERE ret_20d IS NOT NULL ORDER BY ret_20d DESC,stock_code LIMIT n) s;
   PERFORM research.fn_step(rid,codes);
 END LOOP;
 PERFORM research.fn_finish(rid);
 RETURN rid;
END $$;

DO $$ DECLARE t text; f record; c record; BEGIN
 FOREACH t IN ARRAY ARRAY['strategy','backtest_run','backtest_nav','backtest_trade'] LOOP
   EXECUTE format('ALTER TABLE research.%I OWNER TO quant_owner',t);
   EXECUTE format('COMMENT ON TABLE research.%I IS %L',t,'C 回测对象：'||t||'；见 README_C.md');
 END LOOP;
 FOR f IN SELECT p.oid::regprocedure sig FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='research' LOOP
   EXECUTE format('ALTER FUNCTION %s OWNER TO quant_owner',f.sig);
   EXECUTE format('COMMENT ON FUNCTION %s IS %L',f.sig,'C 回测函数；参数化 SQL、PIT 信号、次日开盘撮合，见 README_C.md');
 END LOOP;
 FOR c IN SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='research' LOOP
   EXECUTE format('COMMENT ON COLUMN research.%I.%I IS %L',c.table_name,c.column_name,'C 回测字段 '||c.column_name||'，口径见 README_C.md');
 END LOOP;
END $$;
ALTER TABLE research.backtest_run ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.backtest_nav ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.backtest_trade ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS own_run ON research.backtest_run;
CREATE POLICY own_run ON research.backtest_run USING(owner_id=sim.require_user());
DROP POLICY IF EXISTS own_nav ON research.backtest_nav;
CREATE POLICY own_nav ON research.backtest_nav USING(run_id IN(SELECT run_id FROM research.backtest_run));
DROP POLICY IF EXISTS own_trade ON research.backtest_trade;
CREATE POLICY own_trade ON research.backtest_trade USING(run_id IN(SELECT run_id FROM research.backtest_run));
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA research FROM PUBLIC;
GRANT USAGE ON SCHEMA research TO app_user;
GRANT SELECT ON research.strategy,research.backtest_run,research.backtest_nav,research.backtest_trade TO app_user;
GRANT EXECUTE ON FUNCTION research.fn_candidates(text),research.fn_begin(bigint,text,jsonb,text,date,date,text),
 research.fn_prepare_day(bigint),research.fn_step(bigint,varchar[]),research.fn_finish(bigint),
 research.fn_run_momentum(bigint,jsonb,text,date,date) TO app_user;

-- 面向数据库客户端的逐字段口径。
COMMENT ON COLUMN research.strategy.strategy_id IS '策略模板主键。';
COMMENT ON COLUMN research.strategy.template IS '模板名称：momentum 动量、ma_cross 均线、quality 财报 ROE。';
COMMENT ON COLUMN research.strategy.description IS '模板选股规则说明。';
COMMENT ON COLUMN research.backtest_run.run_id IS '一次回测运行的主键。';
COMMENT ON COLUMN research.backtest_run.owner_id IS '所属应用用户；必须匹配事务内 app.user_id。';
COMMENT ON COLUMN research.backtest_run.strategy_id IS '策略模板主键。';
COMMENT ON COLUMN research.backtest_run.account_id IS '模拟账户主键；所有账户写操作先取得该账户行锁。';
COMMENT ON COLUMN research.backtest_run.engine IS '信号排序实现：SQL 或 PYTHON；二者共用撮合与会计规则。';
COMMENT ON COLUMN research.backtest_run.universe IS '历史指数股票池代码，不使用当前成分股倒推历史。';
COMMENT ON COLUMN research.backtest_run.start_date IS '回测起点（日终），此时只持有现金。';
COMMENT ON COLUMN research.backtest_run.end_date IS '回测终点（日终），必须是已知交易日。';
COMMENT ON COLUMN research.backtest_run.params IS '参数 JSON：top_n、initial_cash、可选财报快照和 replay_of。';
COMMENT ON COLUMN research.backtest_run.data_snapshot_at IS '财报系统版本快照时间；不作为行情备份使用。';
COMMENT ON COLUMN research.backtest_run.data_fingerprint IS '行情、指标、日历、证券状态、指数成员和费用规则的 MD5 内容摘要；仅用于检测重放输入变化。';
COMMENT ON COLUMN research.backtest_run.engine_version IS '撮合和策略执行口径版本；当前 c-v1。';
COMMENT ON COLUMN research.backtest_run.status IS '记录状态；合法取值由 CHECK 和状态机约束。';
COMMENT ON COLUMN research.backtest_run.max_drawdown IS '最大回撤的非负小数，如 0.12 表示 12%。';
COMMENT ON COLUMN research.backtest_run.created_at IS '记录创建时间，含时区。';
COMMENT ON COLUMN research.backtest_nav.run_id IS '一次回测运行的主键。';
COMMENT ON COLUMN research.backtest_nav.trade_date IS '交易日；净值表为该日日终估值。';
COMMENT ON COLUMN research.backtest_nav.equity IS '现金加原始收盘价估算的持仓市值，人民币元。';
COMMENT ON COLUMN research.backtest_nav.nav IS '日终总资产除以开户本金的单位净值。';
COMMENT ON COLUMN research.backtest_nav.drawdown IS '当前净值除以截至当日的历史最高净值再减一，取值不大于零。';
COMMENT ON COLUMN research.backtest_trade.run_id IS '一次回测运行的主键。';
COMMENT ON COLUMN research.backtest_trade.order_id IS '订单主键；成交和非开户资金流水关联到该订单。';
