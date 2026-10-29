-- A：查询入口。所有查询共享同一个“研究日期”，不让调用者忘记过滤。
-- 日期含义：该自然日日终。交易指标另取不晚于研究日的最后交易日；
-- 周末公布的财报仍可在周末研究中看到，不把研究日整体回退到星期五。
CREATE OR REPLACE FUNCTION pit.as_of() RETURNS date LANGUAGE plpgsql STABLE AS $$
DECLARE v_value text := current_setting('app.as_of_date', true);
BEGIN
    IF v_value IS NULL OR v_value='' THEN RAISE EXCEPTION '请先设置 app.as_of_date 研究日期'; END IF;
    RETURN v_value::date;
END $$;
CREATE OR REPLACE FUNCTION pit.snapshot_at() RETURNS timestamptz LANGUAGE sql STABLE AS $$
    SELECT nullif(current_setting('app.data_snapshot_at', true), '')::timestamptz
$$;
CREATE OR REPLACE FUNCTION pit.clip(p_range daterange) RETURNS daterange LANGUAGE sql STABLE AS $$
    SELECT p_range * daterange(NULL, pit.as_of()+1, '[)')
$$;
CREATE OR REPLACE FUNCTION pit.market_date() RETURNS date
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,ref,pit AS $$
    SELECT max(cal_date) FROM ref.trading_calendar WHERE is_open AND cal_date<=pit.as_of()
$$;

-- security_barrier 防止外层查询条件被提前推入原始数据过滤之前。
CREATE OR REPLACE VIEW pit.security WITH (security_barrier=true) AS
SELECT s.stock_code,s.exchange,s.board,s.list_date,
       CASE WHEN s.delist_date<=pit.as_of() THEN s.delist_date END AS delist_date,
       n.name
FROM ref.security s LEFT JOIN ref.security_name n
  ON n.stock_code=s.stock_code AND n.valid @> pit.as_of()
WHERE s.list_date<=pit.as_of();
CREATE OR REPLACE VIEW pit.index_member WITH (security_barrier=true) AS
SELECT index_code, stock_code, pit.clip(valid) AS valid
FROM ref.index_member WHERE lower(valid)<=pit.as_of();
CREATE OR REPLACE VIEW pit.security_status WITH (security_barrier=true) AS
SELECT stock_code,status,pit.clip(valid) AS valid
FROM ref.security_status WHERE lower(valid)<=pit.as_of();
CREATE OR REPLACE VIEW pit.industry_member WITH (security_barrier=true) AS
SELECT stock_code,industry_code,pit.clip(valid) AS valid
FROM ref.industry_member WHERE lower(valid)<=pit.as_of();
CREATE OR REPLACE VIEW pit.industry WITH (security_barrier=true) AS
SELECT DISTINCT i.industry_code,i.name,i.parent_code
FROM ref.industry i JOIN pit.industry_member m USING(industry_code);

CREATE OR REPLACE VIEW pit.daily_bar_hfq WITH (security_barrier=true) AS
SELECT * FROM mkt.v_bar_hfq WHERE trade_date<=pit.as_of();
CREATE OR REPLACE VIEW pit.daily_bar_raw WITH (security_barrier=true) AS
SELECT * FROM mkt.daily_bar WHERE trade_date<=pit.as_of();
CREATE OR REPLACE VIEW pit.daily_bar_qfq WITH (security_barrier=true) AS
SELECT h.stock_code,h.trade_date,
       h.open/a.back_factor AS open,h.high/a.back_factor AS high,
       h.low/a.back_factor AS low,h.close/a.back_factor AS close,
       h.volume,h.amount,h.is_suspended
FROM mkt.v_bar_hfq h JOIN mkt.adj_factor a
  ON a.stock_code=h.stock_code AND a.valid @> pit.as_of()
WHERE h.trade_date<=pit.as_of();
CREATE OR REPLACE VIEW pit.indicator_daily WITH (security_barrier=true) AS
SELECT * FROM mkt.mv_indicator_daily WHERE trade_date<=pit.as_of();

-- 先恢复指定的“本地数据库版本”，再选研究日已公开的最后一次披露。
-- 不暴露 known_range、is_restated、sys_period 等可能描述未来变化的字段。
CREATE OR REPLACE VIEW pit.fin_report WITH (security_barrier=true) AS
SELECT DISTINCT ON (stock_code,report_period)
       stock_code,report_period,announce_date,available_date,
       revenue,net_profit,total_assets,total_equity,eps,roe,
       period_basis,profit_basis,eps_basis,source,source_url,source_kind,notes
FROM (
    SELECT * FROM fin.report WHERE pit.snapshot_at() IS NULL OR sys_period @> pit.snapshot_at()
    UNION ALL
    SELECT * FROM fin.report_history WHERE pit.snapshot_at() IS NOT NULL AND sys_period @> pit.snapshot_at()
) r
WHERE announce_date<=pit.as_of() AND available_date<=pit.as_of()
ORDER BY stock_code,report_period,available_date DESC,announce_date DESC;
CREATE OR REPLACE VIEW pit.fin_latest WITH (security_barrier=true) AS
SELECT DISTINCT ON (stock_code) * FROM pit.fin_report
ORDER BY stock_code,report_period DESC;

CREATE OR REPLACE FUNCTION pit.fn_screen(
    p_index_code text,p_filters jsonb DEFAULT '[]',
    p_order_by text DEFAULT 'ret_20d',p_limit int DEFAULT 20
) RETURNS TABLE(stock_code varchar,name text,roe numeric,eps numeric,ret_20d numeric,vol_20d numeric)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_fields text[] := ARRAY['roe','eps','ret_20d','ret_60d','vol_20d','amount_20d'];
    v_filter record;
BEGIN
    PERFORM pit.as_of();
    IF p_limit IS NULL OR p_limit<1 OR p_limit>100 THEN RAISE EXCEPTION '条数须为 1 至 100'; END IF;
    IF p_order_by IS NULL OR NOT p_order_by=ANY(v_fields) THEN RAISE EXCEPTION '不支持的排序字段'; END IF;
    IF p_filters IS NULL OR jsonb_typeof(p_filters)<>'array' THEN RAISE EXCEPTION '筛选条件必须是 JSON 数组'; END IF;
    IF jsonb_array_length(p_filters)>10 THEN RAISE EXCEPTION '最多十个筛选条件'; END IF;
    FOR v_filter IN SELECT * FROM jsonb_to_recordset(p_filters) AS f(field text,op text,value numeric) LOOP
        IF v_filter.field IS NULL OR NOT v_filter.field=ANY(v_fields)
           OR v_filter.op IS NULL OR v_filter.op NOT IN ('>','>=','<','<=')
           OR v_filter.value IS NULL THEN RAISE EXCEPTION '筛选字段、比较符或数值不合法'; END IF;
    END LOOP;
    -- 显式 CASE 比拼接 SQL 更容易读，也没有把用户文本作为 SQL 执行。
    RETURN QUERY
    WITH candidates AS (
        SELECT s.stock_code,s.name,f.roe,f.eps,i.ret_20d,i.ret_60d,i.vol_20d,i.amount_20d
        FROM pit.index_member m JOIN pit.security s ON s.stock_code=m.stock_code
        JOIN pit.indicator_daily i ON i.stock_code=s.stock_code AND i.trade_date=pit.market_date()
        LEFT JOIN pit.fin_latest f ON f.stock_code=s.stock_code
        WHERE m.index_code=p_index_code AND m.valid @> pit.as_of() AND NOT i.is_suspended
          AND (s.delist_date IS NULL OR s.delist_date>pit.as_of())
    )
    SELECT c.stock_code,c.name,c.roe,c.eps,c.ret_20d,c.vol_20d FROM candidates c
    WHERE NOT EXISTS (
        SELECT 1 FROM jsonb_to_recordset(p_filters) AS f(field text,op text,value numeric)
        CROSS JOIN LATERAL (SELECT CASE f.field
            WHEN 'roe' THEN c.roe WHEN 'eps' THEN c.eps WHEN 'ret_20d' THEN c.ret_20d
            WHEN 'ret_60d' THEN c.ret_60d WHEN 'vol_20d' THEN c.vol_20d
            WHEN 'amount_20d' THEN c.amount_20d END AS actual) v
        WHERE NOT coalesce(CASE f.op WHEN '>' THEN v.actual>f.value WHEN '>=' THEN v.actual>=f.value
                   WHEN '<' THEN v.actual<f.value WHEN '<=' THEN v.actual<=f.value END,false)
    )
    ORDER BY CASE p_order_by WHEN 'roe' THEN c.roe WHEN 'eps' THEN c.eps
        WHEN 'ret_20d' THEN c.ret_20d WHEN 'ret_60d' THEN c.ret_60d
        WHEN 'vol_20d' THEN c.vol_20d WHEN 'amount_20d' THEN c.amount_20d END DESC NULLS LAST,
        c.stock_code
    LIMIT p_limit;
END $$;

CREATE OR REPLACE FUNCTION pit.fn_price_history(p_code varchar,p_start date,p_end date,p_adj text DEFAULT 'hfq')
RETURNS TABLE(stock_code varchar,trade_date date,open numeric,high numeric,low numeric,close numeric,
              volume bigint,amount numeric,is_suspended boolean)
LANGUAGE plpgsql STABLE AS $$
BEGIN
    PERFORM pit.as_of();
    IF p_start IS NULL OR p_end IS NULL OR p_end<p_start THEN RAISE EXCEPTION '价格查询日期范围不合法'; END IF;
    IF p_adj='raw' THEN
        RETURN QUERY SELECT b.stock_code,b.trade_date,b.open,b.high,b.low,b.close,b.volume,b.amount,b.is_suspended
          FROM pit.daily_bar_raw b WHERE b.stock_code=p_code AND b.trade_date BETWEEN p_start AND p_end ORDER BY b.trade_date;
    ELSIF p_adj='hfq' THEN
        RETURN QUERY SELECT b.stock_code,b.trade_date,b.open,b.high,b.low,b.close,b.volume,b.amount,b.is_suspended
          FROM pit.daily_bar_hfq b WHERE b.stock_code=p_code AND b.trade_date BETWEEN p_start AND p_end ORDER BY b.trade_date;
    ELSIF p_adj='qfq' THEN
        RETURN QUERY SELECT b.stock_code,b.trade_date,b.open,b.high,b.low,b.close,b.volume,b.amount,b.is_suspended
          FROM pit.daily_bar_qfq b WHERE b.stock_code=p_code AND b.trade_date BETWEEN p_start AND p_end ORDER BY b.trade_date;
    ELSE RAISE EXCEPTION '复权方式只支持 raw、hfq、qfq'; END IF;
END $$;
CREATE OR REPLACE FUNCTION pit.fn_fundamentals(p_code varchar,p_n_periods int DEFAULT 4)
RETURNS SETOF pit.fin_report LANGUAGE plpgsql STABLE AS $$
BEGIN
    PERFORM pit.as_of();
    IF p_n_periods IS NULL OR p_n_periods<1 OR p_n_periods>40 THEN RAISE EXCEPTION '报告期数量须为 1 至 40'; END IF;
    RETURN QUERY SELECT * FROM pit.fin_report WHERE stock_code=p_code ORDER BY report_period DESC LIMIT p_n_periods;
END $$;
CREATE OR REPLACE FUNCTION pit.fn_index_members(p_index_code text,p_date date)
RETURNS TABLE(stock_code varchar,name text) LANGUAGE plpgsql STABLE AS $$
BEGIN
    IF p_date IS DISTINCT FROM pit.as_of() THEN RAISE EXCEPTION '成分股日期须与当前研究日期一致'; END IF;
    RETURN QUERY SELECT s.stock_code,s.name FROM pit.index_member m
    JOIN pit.security s ON s.stock_code=m.stock_code
    WHERE m.index_code=p_index_code AND m.valid @> p_date
      AND (s.delist_date IS NULL OR s.delist_date>p_date) ORDER BY s.stock_code;
END $$;
CREATE OR REPLACE FUNCTION pit.fn_industry_peers(p_code varchar)
RETURNS TABLE(stock_code varchar,name text,industry_code varchar,industry_name text)
LANGUAGE sql STABLE AS $$
    SELECT s.stock_code,s.name,m.industry_code,i.name
    FROM pit.industry_member own JOIN pit.industry_member m USING(industry_code)
    JOIN pit.security s ON s.stock_code=m.stock_code
    JOIN pit.industry i ON i.industry_code=m.industry_code
    WHERE own.stock_code=p_code AND own.valid @> pit.as_of() AND m.valid @> pit.as_of()
      AND m.stock_code<>p_code AND (s.delist_date IS NULL OR s.delist_date>pit.as_of())
    ORDER BY s.stock_code
$$;

COMMENT ON FUNCTION pit.as_of() IS '研究日期，按自然日日终解释；缺少日期直接报错。';
COMMENT ON FUNCTION pit.snapshot_at() IS '可选的本地财务快照时间；仅恢复财务，不承诺行情或规则的完整回测快照。';
COMMENT ON FUNCTION pit.clip(daterange) IS '截断区间上界，避免暴露未来退出日期。';
COMMENT ON FUNCTION pit.market_date() IS '截至研究日最近的交易日；保留周末研究日期的财报可见性。';
COMMENT ON FUNCTION pit.fn_screen(text,jsonb,text,int) IS '按历史成员、历史指标、已知财报筛选；六列输出，最多百条。';
COMMENT ON FUNCTION pit.fn_price_history(varchar,date,date,text) IS '查询截止研究日的原始/后复权/按研究日前复权行情。';
COMMENT ON FUNCTION pit.fn_fundamentals(varchar,int) IS '返回指定公司最近若干期当时可见的财报及口径来源。';
COMMENT ON FUNCTION pit.fn_index_members(text,date) IS '查询当前研究日期的指数成员。';
COMMENT ON FUNCTION pit.fn_industry_peers(varchar) IS '查询当前时点同一叶子行业的其他公司。';

-- 逐个归属，避免修改 B/C/D 的对象。Agent 只获得 PIT 的读和函数执行权限。
ALTER FUNCTION pit.as_of() OWNER TO quant_owner;
ALTER FUNCTION pit.snapshot_at() OWNER TO quant_owner;
ALTER FUNCTION pit.clip(daterange) OWNER TO quant_owner;
ALTER FUNCTION pit.market_date() OWNER TO quant_owner;
ALTER FUNCTION pit.fn_screen(text,jsonb,text,int) OWNER TO quant_owner;
ALTER FUNCTION pit.fn_price_history(varchar,date,date,text) OWNER TO quant_owner;
ALTER FUNCTION pit.fn_fundamentals(varchar,int) OWNER TO quant_owner;
ALTER FUNCTION pit.fn_index_members(text,date) OWNER TO quant_owner;
ALTER FUNCTION pit.fn_industry_peers(varchar) OWNER TO quant_owner;
ALTER VIEW pit.security OWNER TO quant_owner;
ALTER VIEW pit.index_member OWNER TO quant_owner;
ALTER VIEW pit.security_status OWNER TO quant_owner;
ALTER VIEW pit.industry_member OWNER TO quant_owner;
ALTER VIEW pit.industry OWNER TO quant_owner;
ALTER VIEW pit.daily_bar_raw OWNER TO quant_owner;
ALTER VIEW pit.daily_bar_hfq OWNER TO quant_owner;
ALTER VIEW pit.daily_bar_qfq OWNER TO quant_owner;
ALTER VIEW pit.indicator_daily OWNER TO quant_owner;
ALTER VIEW pit.fin_report OWNER TO quant_owner;
ALTER VIEW pit.fin_latest OWNER TO quant_owner;
REVOKE ALL ON SCHEMA ref,fin,mkt FROM agent_reader;
REVOKE ALL ON ALL TABLES IN SCHEMA ref,fin,mkt FROM agent_reader;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA pit FROM PUBLIC;
GRANT USAGE ON SCHEMA pit TO agent_reader,app_user;
GRANT SELECT ON ALL TABLES IN SCHEMA pit TO agent_reader,app_user;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA pit TO agent_reader,app_user;
ALTER ROLE agent_reader SET default_transaction_read_only=on;
ALTER ROLE agent_reader SET statement_timeout='5s';

-- 字段说明，便于在数据库工具里直接查看。
COMMENT ON COLUMN pit.daily_bar_hfq.stock_code IS '股票代码，格式为 sh/sz/bj.六位数字。';
COMMENT ON COLUMN pit.daily_bar_hfq.trade_date IS '交易日期。';
COMMENT ON COLUMN pit.daily_bar_hfq.open IS '当日开盘价；复权口径由视图名称确定。';
COMMENT ON COLUMN pit.daily_bar_hfq.high IS '当日最高价；复权口径由视图名称确定。';
COMMENT ON COLUMN pit.daily_bar_hfq.low IS '当日最低价；复权口径由视图名称确定。';
COMMENT ON COLUMN pit.daily_bar_hfq.close IS '当日收盘价；复权口径由视图名称确定。';
COMMENT ON COLUMN pit.daily_bar_hfq.volume IS '成交量，股。';
COMMENT ON COLUMN pit.daily_bar_hfq.amount IS '成交额，人民币元。';
COMMENT ON COLUMN pit.daily_bar_hfq.is_suspended IS '是否停牌。';
COMMENT ON COLUMN pit.daily_bar_hfq.back_factor IS '该交易日有效的后复权因子。';
COMMENT ON COLUMN pit.daily_bar_qfq.stock_code IS '股票代码，格式为 sh/sz/bj.六位数字。';
COMMENT ON COLUMN pit.daily_bar_qfq.trade_date IS '交易日期。';
COMMENT ON COLUMN pit.daily_bar_qfq.open IS '当日开盘价；复权口径由视图名称确定。';
COMMENT ON COLUMN pit.daily_bar_qfq.high IS '当日最高价；复权口径由视图名称确定。';
COMMENT ON COLUMN pit.daily_bar_qfq.low IS '当日最低价；复权口径由视图名称确定。';
COMMENT ON COLUMN pit.daily_bar_qfq.close IS '当日收盘价；复权口径由视图名称确定。';
COMMENT ON COLUMN pit.daily_bar_qfq.volume IS '成交量，股。';
COMMENT ON COLUMN pit.daily_bar_qfq.amount IS '成交额，人民币元。';
COMMENT ON COLUMN pit.daily_bar_qfq.is_suspended IS '是否停牌。';
COMMENT ON COLUMN pit.daily_bar_raw.stock_code IS '股票代码，格式为 sh/sz/bj.六位数字。';
COMMENT ON COLUMN pit.daily_bar_raw.trade_date IS '交易日期。';
COMMENT ON COLUMN pit.daily_bar_raw.open IS '当日开盘价；复权口径由视图名称确定。';
COMMENT ON COLUMN pit.daily_bar_raw.high IS '当日最高价；复权口径由视图名称确定。';
COMMENT ON COLUMN pit.daily_bar_raw.low IS '当日最低价；复权口径由视图名称确定。';
COMMENT ON COLUMN pit.daily_bar_raw.close IS '当日收盘价；复权口径由视图名称确定。';
COMMENT ON COLUMN pit.daily_bar_raw.pre_close IS '前收盘价，人民币元。';
COMMENT ON COLUMN pit.daily_bar_raw.volume IS '成交量，股。';
COMMENT ON COLUMN pit.daily_bar_raw.amount IS '成交额，人民币元。';
COMMENT ON COLUMN pit.daily_bar_raw.is_suspended IS '是否停牌。';
COMMENT ON COLUMN pit.fin_latest.stock_code IS '股票代码，格式为 sh/sz/bj.六位数字。';
COMMENT ON COLUMN pit.fin_latest.report_period IS '财报统计截止日，季度末。';
COMMENT ON COLUMN pit.fin_latest.announce_date IS '这一次披露的公告日，以公开日期为准。';
COMMENT ON COLUMN pit.fin_latest.available_date IS '允许用于研究的最早日期：核对公告用公告日，供应商快照用采集日。';
COMMENT ON COLUMN pit.fin_latest.revenue IS '人民币收入，具体业务口径见 notes。';
COMMENT ON COLUMN pit.fin_latest.net_profit IS '人民币净利润；归母或合并口径见 profit_basis。';
COMMENT ON COLUMN pit.fin_latest.total_assets IS '合并总资产，人民币元。';
COMMENT ON COLUMN pit.fin_latest.total_equity IS '合并所有者权益总额，人民币元。';
COMMENT ON COLUMN pit.fin_latest.eps IS '每股收益，元/股；BASIC 或 TTM 见 eps_basis。';
COMMENT ON COLUMN pit.fin_latest.roe IS '收益率小数；0.15 表示 15%。';
COMMENT ON COLUMN pit.fin_latest.period_basis IS 'YTD 年初累计、QUARTER 单季、ANNUAL 全年。';
COMMENT ON COLUMN pit.fin_latest.profit_basis IS 'PARENT 归母净利润，CONSOLIDATED 合并净利润。';
COMMENT ON COLUMN pit.fin_latest.eps_basis IS 'BASIC 基本每股收益，TTM 滚动每股收益。';
COMMENT ON COLUMN pit.fin_latest.source IS '数据或规则来源；教学数据明确标记 SYNTHETIC。';
COMMENT ON COLUMN pit.fin_latest.source_url IS '原公告、供应商文档或教学案例标识。';
COMMENT ON COLUMN pit.fin_latest.source_kind IS 'DISCLOSURE 核对公告、PROVIDER_SNAPSHOT 采集快照、SYNTHETIC 合成。';
COMMENT ON COLUMN pit.fin_report.stock_code IS '股票代码，格式为 sh/sz/bj.六位数字。';
COMMENT ON COLUMN pit.fin_report.report_period IS '财报统计截止日，季度末。';
COMMENT ON COLUMN pit.fin_report.announce_date IS '这一次披露的公告日，以公开日期为准。';
COMMENT ON COLUMN pit.fin_report.available_date IS '允许用于研究的最早日期：核对公告用公告日，供应商快照用采集日。';
COMMENT ON COLUMN pit.fin_report.revenue IS '人民币收入，具体业务口径见 notes。';
COMMENT ON COLUMN pit.fin_report.net_profit IS '人民币净利润；归母或合并口径见 profit_basis。';
COMMENT ON COLUMN pit.fin_report.total_assets IS '合并总资产，人民币元。';
COMMENT ON COLUMN pit.fin_report.total_equity IS '合并所有者权益总额，人民币元。';
COMMENT ON COLUMN pit.fin_report.eps IS '每股收益，元/股；BASIC 或 TTM 见 eps_basis。';
COMMENT ON COLUMN pit.fin_report.roe IS '收益率小数；0.15 表示 15%。';
COMMENT ON COLUMN pit.fin_report.period_basis IS 'YTD 年初累计、QUARTER 单季、ANNUAL 全年。';
COMMENT ON COLUMN pit.fin_report.profit_basis IS 'PARENT 归母净利润，CONSOLIDATED 合并净利润。';
COMMENT ON COLUMN pit.fin_report.eps_basis IS 'BASIC 基本每股收益，TTM 滚动每股收益。';
COMMENT ON COLUMN pit.fin_report.source IS '数据或规则来源；教学数据明确标记 SYNTHETIC。';
COMMENT ON COLUMN pit.fin_report.source_url IS '原公告、供应商文档或教学案例标识。';
COMMENT ON COLUMN pit.fin_report.source_kind IS 'DISCLOSURE 核对公告、PROVIDER_SNAPSHOT 采集快照、SYNTHETIC 合成。';
COMMENT ON COLUMN pit.index_member.index_code IS '指数代码；SYNTH300 是教学指数。';
COMMENT ON COLUMN pit.index_member.stock_code IS '股票代码，格式为 sh/sz/bj.六位数字。';
COMMENT ON COLUMN pit.index_member.valid IS '业务有效区间，左闭右开；PIT 中上界最多为研究日次日。';
COMMENT ON COLUMN pit.indicator_daily.stock_code IS '股票代码，格式为 sh/sz/bj.六位数字。';
COMMENT ON COLUMN pit.indicator_daily.trade_date IS '交易日期。';
COMMENT ON COLUMN pit.indicator_daily.is_suspended IS '是否停牌。';
COMMENT ON COLUMN pit.indicator_daily.close_hfq IS '后复权收盘价。';
COMMENT ON COLUMN pit.indicator_daily.ma20 IS '最近 20 个交易日收盘均价。';
COMMENT ON COLUMN pit.indicator_daily.ma60 IS '最近 60 个交易日收盘均价。';
COMMENT ON COLUMN pit.indicator_daily.ema12 IS '12 日指数加权收盘均价。';
COMMENT ON COLUMN pit.indicator_daily.ema26 IS '26 日指数加权收盘均价。';
COMMENT ON COLUMN pit.indicator_daily.ret_20d IS '20 日后复权收盘收益率。';
COMMENT ON COLUMN pit.indicator_daily.ret_60d IS '60 日后复权收盘收益率。';
COMMENT ON COLUMN pit.indicator_daily.vol_20d IS '最近 20 个日对数收益的样本标准差年化（sqrt(252)）。';
COMMENT ON COLUMN pit.indicator_daily.amount_20d IS '20 日平均成交额，沿用 B 的指标定义。';
COMMENT ON COLUMN pit.industry.industry_code IS '单一分类体系中的行业代码。';
COMMENT ON COLUMN pit.industry.name IS '当条记录对应的名称。';
COMMENT ON COLUMN pit.industry.parent_code IS '上级行业代码；根行业为空。';
COMMENT ON COLUMN pit.industry_member.stock_code IS '股票代码，格式为 sh/sz/bj.六位数字。';
COMMENT ON COLUMN pit.industry_member.industry_code IS '单一分类体系中的行业代码。';
COMMENT ON COLUMN pit.industry_member.valid IS '业务有效区间，左闭右开；PIT 中上界最多为研究日次日。';
COMMENT ON COLUMN pit.security.stock_code IS '股票代码，格式为 sh/sz/bj.六位数字。';
COMMENT ON COLUMN pit.security.exchange IS '交易所：SH/SZ/BJ。';
COMMENT ON COLUMN pit.security.board IS '板块：MAIN/GEM/STAR/BSE。';
COMMENT ON COLUMN pit.security.list_date IS '上市日期，包含当天。';
COMMENT ON COLUMN pit.security.delist_date IS '摘牌日期，PIT 隐藏尚未发生的摘牌日期。';
COMMENT ON COLUMN pit.security.name IS '当条记录对应的名称。';
COMMENT ON COLUMN pit.security_status.stock_code IS '股票代码，格式为 sh/sz/bj.六位数字。';
COMMENT ON COLUMN pit.security_status.status IS '证券状态：NORMAL/ST/*ST。';
COMMENT ON COLUMN pit.security_status.valid IS '业务有效区间，左闭右开；PIT 中上界最多为研究日次日。';
COMMENT ON VIEW pit.security IS '截至研究日上市的证券和当时名称。';
COMMENT ON VIEW pit.index_member IS '已发生的成员区间，隐藏未来退出上界。';
COMMENT ON VIEW pit.security_status IS '已发生的状态区间，隐藏未来上界。';
COMMENT ON VIEW pit.industry_member IS '已发生的行业区间，隐藏未来上界。';
COMMENT ON VIEW pit.industry IS '截至研究日已有成员记录的行业。';
COMMENT ON VIEW pit.daily_bar_hfq IS '截止研究日的后复权行情。';
COMMENT ON VIEW pit.daily_bar_raw IS '截止研究日的原始行情。';
COMMENT ON VIEW pit.daily_bar_qfq IS '以研究日有效因子为锚的前复权行情。';
COMMENT ON VIEW pit.indicator_daily IS '截止研究日的 B 行情指标。';
COMMENT ON VIEW pit.fin_report IS '每个报告期在研究日可见的最新披露。';
COMMENT ON VIEW pit.fin_latest IS '每家公司在研究日可见的最近报告期。';
COMMENT ON COLUMN pit.fin_report.notes IS '口径、来源范围及未核对字段说明。';
COMMENT ON COLUMN pit.fin_latest.notes IS '口径、来源范围及未核对字段说明。';
