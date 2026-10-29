-- D3：因子评价、私有策略和个人费用。重复执行安全，不改行情。

DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint WHERE conrelid = 'research.strategy'::regclass AND contype = 'c' LOOP
        EXECUTE format('ALTER TABLE research.strategy DROP CONSTRAINT %I', r.conname);
    END LOOP;
END $$;
ALTER TABLE research.strategy ADD CONSTRAINT strategy_template_check
    CHECK (template IN ('momentum', 'ma_cross', 'quality', 'factor_rank'));
INSERT INTO research.strategy(template, description)
VALUES ('factor_rank', '本人固定公式版本的单因子排序，下一交易日开盘执行')
ON CONFLICT (template) DO NOTHING;

CREATE TABLE IF NOT EXISTS research.factor_eval_run (
    eval_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id bigint NOT NULL REFERENCES core.app_user(user_id),
    factor_id bigint NOT NULL,
    version_no integer NOT NULL,
    universe varchar(32) NOT NULL,
    start_date date NOT NULL,
    end_date date NOT NULL,
    horizons integer[] NOT NULL,
    idempotency_key text NOT NULL,
    status varchar(16) NOT NULL CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED', 'INTERRUPTED')),
    snapshot text NOT NULL,
    error_class text,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    UNIQUE (owner_id, idempotency_key),
    UNIQUE (eval_id, owner_id),
    FOREIGN KEY (factor_id, version_no) REFERENCES factor.version(factor_id, version_no),
    CHECK (end_date >= start_date),
    CHECK (cardinality(horizons) BETWEEN 1 AND 3)
);
ALTER TABLE research.factor_eval_run OWNER TO quant_owner;

CREATE TABLE IF NOT EXISTS research.factor_eval_value (
    eval_id bigint NOT NULL,
    owner_id bigint NOT NULL,
    signal_date date NOT NULL,
    stock_code varchar(12) NOT NULL,
    horizon integer NOT NULL,
    factor_value numeric,
    entry_date date,
    exit_date date,
    label numeric,
    valid boolean NOT NULL,
    reason_code text,
    PRIMARY KEY (eval_id, signal_date, stock_code, horizon),
    FOREIGN KEY (eval_id, owner_id) REFERENCES research.factor_eval_run(eval_id, owner_id)
);
ALTER TABLE research.factor_eval_value OWNER TO quant_owner;

CREATE TABLE IF NOT EXISTS research.factor_eval_daily (
    eval_id bigint NOT NULL,
    owner_id bigint NOT NULL,
    signal_date date NOT NULL,
    horizon integer NOT NULL,
    candidate_count integer NOT NULL,
    valid_count integer NOT NULL,
    ic numeric,
    rank_ic numeric,
    reason_code text,
    group_count integer NOT NULL DEFAULT 0,
    PRIMARY KEY (eval_id, signal_date, horizon),
    FOREIGN KEY (eval_id, owner_id) REFERENCES research.factor_eval_run(eval_id, owner_id)
);
ALTER TABLE research.factor_eval_daily OWNER TO quant_owner;

CREATE TABLE IF NOT EXISTS research.factor_eval_group (
    eval_id bigint NOT NULL,
    owner_id bigint NOT NULL,
    signal_date date NOT NULL,
    horizon integer NOT NULL,
    group_no integer NOT NULL,
    lower_value numeric,
    upper_value numeric,
    member_count integer NOT NULL,
    avg_label numeric,
    reason_code text,
    PRIMARY KEY (eval_id, signal_date, horizon, group_no),
    FOREIGN KEY (eval_id, owner_id) REFERENCES research.factor_eval_run(eval_id, owner_id)
);
ALTER TABLE research.factor_eval_group OWNER TO quant_owner;

CREATE TABLE IF NOT EXISTS research.factor_strategy (
    strategy_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id bigint NOT NULL REFERENCES core.app_user(user_id),
    name varchar(128) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (strategy_id, owner_id),
    UNIQUE (owner_id, name)
);
ALTER TABLE research.factor_strategy OWNER TO quant_owner;

CREATE TABLE IF NOT EXISTS research.factor_strategy_version (
    strategy_id bigint NOT NULL,
    owner_id bigint NOT NULL,
    version_no integer NOT NULL CHECK (version_no >= 1),
    factor_id bigint NOT NULL,
    factor_version integer NOT NULL,
    direction varchar(8) NOT NULL CHECK (direction IN ('HIGH', 'LOW')),
    top_n integer CHECK (top_n BETWEEN 1 AND 20),
    top_pct numeric CHECK (top_pct > 0 AND top_pct <= 1),
    rebalance varchar(8) NOT NULL CHECK (rebalance IN ('DAY', 'WEEK', 'MONTH')),
    digest text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (strategy_id, version_no),
    UNIQUE (strategy_id, owner_id, version_no),
    FOREIGN KEY (strategy_id, owner_id) REFERENCES research.factor_strategy(strategy_id, owner_id),
    FOREIGN KEY (factor_id, factor_version) REFERENCES factor.version(factor_id, version_no),
    CHECK ((top_n IS NOT NULL) <> (top_pct IS NOT NULL))
);
ALTER TABLE research.factor_strategy_version OWNER TO quant_owner;

CREATE TABLE IF NOT EXISTS sim.cost_profile (
    profile_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id bigint NOT NULL REFERENCES core.app_user(user_id),
    name varchar(128) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (profile_id, owner_id),
    UNIQUE (owner_id, name)
);
ALTER TABLE sim.cost_profile OWNER TO quant_owner;

CREATE TABLE IF NOT EXISTS sim.cost_version (
    profile_id bigint NOT NULL,
    owner_id bigint NOT NULL,
    version_no integer NOT NULL CHECK (version_no >= 1),
    mode varchar(24) NOT NULL CHECK (mode IN ('CN_A', 'CUSTOM_LIQUIDITY')),
    commission_rate numeric NOT NULL CHECK (commission_rate >= 0 AND commission_rate <= 0.1),
    min_commission numeric NOT NULL CHECK (min_commission >= 0 AND min_commission <= 1000),
    transfer_rate numeric NOT NULL DEFAULT 0 CHECK (transfer_rate >= 0 AND transfer_rate <= 0.1),
    stamp_rate numeric NOT NULL DEFAULT 0 CHECK (stamp_rate >= 0 AND stamp_rate <= 0.1),
    maker_rate numeric NOT NULL DEFAULT 0 CHECK (maker_rate >= 0 AND maker_rate <= 0.1),
    taker_rate numeric NOT NULL DEFAULT 0 CHECK (taker_rate >= 0 AND taker_rate <= 0.1),
    liquidity varchar(16) NOT NULL DEFAULT 'ALL_TAKER' CHECK (liquidity IN ('ALL_TAKER', 'ALL_MAKER')),
    slippage_bps numeric NOT NULL DEFAULT 0 CHECK (slippage_bps >= 0 AND slippage_bps <= 1000),
    digest text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (profile_id, version_no),
    UNIQUE (profile_id, owner_id, version_no),
    FOREIGN KEY (profile_id, owner_id) REFERENCES sim.cost_profile(profile_id, owner_id)
);
ALTER TABLE sim.cost_version OWNER TO quant_owner;

CREATE TABLE IF NOT EXISTS research.factor_bt_run (
    bt_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id bigint NOT NULL REFERENCES core.app_user(user_id),
    strategy_id bigint NOT NULL,
    strategy_version integer NOT NULL,
    profile_id bigint,
    cost_version integer,
    zero_cost boolean NOT NULL DEFAULT false,
    universe varchar(32) NOT NULL,
    start_date date NOT NULL,
    end_date date NOT NULL,
    initial_cash numeric(20,2) NOT NULL CHECK (initial_cash > 0),
    status varchar(16) NOT NULL CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED', 'INTERRUPTED')),
    idempotency_key text NOT NULL,
    paired_bt_id bigint,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (owner_id, idempotency_key),
    UNIQUE (bt_id, owner_id),
    FOREIGN KEY (strategy_id, owner_id, strategy_version)
        REFERENCES research.factor_strategy_version(strategy_id, owner_id, version_no),
    FOREIGN KEY (profile_id, owner_id, cost_version)
        REFERENCES sim.cost_version(profile_id, owner_id, version_no)
);
ALTER TABLE research.factor_bt_run OWNER TO quant_owner;

CREATE TABLE IF NOT EXISTS research.factor_trade (
    trade_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    bt_id bigint NOT NULL,
    owner_id bigint NOT NULL,
    signal_date date NOT NULL,
    trade_date date NOT NULL CHECK (trade_date > signal_date),
    stock_code varchar(12) NOT NULL,
    side char(1) NOT NULL CHECK (side IN ('B', 'S')),
    qty integer NOT NULL CHECK (qty > 0 AND qty % 100 = 0),
    raw_price numeric(14,4) NOT NULL,
    price numeric(14,2) NOT NULL,
    amount numeric(20,2) NOT NULL,
    fee numeric(20,2) NOT NULL CHECK (fee >= 0),
    status varchar(12) NOT NULL CHECK (status IN ('FILLED', 'REJECTED')),
    reason text,
    FOREIGN KEY (bt_id, owner_id) REFERENCES research.factor_bt_run(bt_id, owner_id)
);
ALTER TABLE research.factor_trade OWNER TO quant_owner;

CREATE TABLE IF NOT EXISTS research.factor_trade_cost (
    trade_id bigint NOT NULL REFERENCES research.factor_trade(trade_id),
    owner_id bigint NOT NULL,
    component varchar(16) NOT NULL CHECK (component IN ('COMMISSION', 'TRANSFER', 'STAMP', 'OTHER', 'LEGACY_UNSPLIT')),
    amount numeric(20,2) NOT NULL,
    PRIMARY KEY (trade_id, component)
);
ALTER TABLE research.factor_trade_cost OWNER TO quant_owner;

CREATE TABLE IF NOT EXISTS research.backtest_pnl (
    bt_id bigint NOT NULL,
    owner_id bigint NOT NULL,
    trade_date date NOT NULL,
    net_equity numeric(20,2) NOT NULL,
    gross_same_fills numeric(20,2) NOT NULL,
    cumulative_fees numeric(20,2) NOT NULL,
    basis varchar(24) NOT NULL,
    PRIMARY KEY (bt_id, trade_date),
    FOREIGN KEY (bt_id, owner_id) REFERENCES research.factor_bt_run(bt_id, owner_id)
);
ALTER TABLE research.backtest_pnl OWNER TO quant_owner;

COMMENT ON TABLE research.factor_eval_run IS '本人的因子评价作业。参数和公式版本固定。';
COMMENT ON TABLE research.factor_eval_value IS '信号日的因子值与随后收益标签。缺失不记为 0。';
COMMENT ON TABLE research.factor_eval_daily IS '每个信号日和持有期的 IC、覆盖与分组数。';
COMMENT ON TABLE research.factor_eval_group IS '分组的人数和等权平均标签。这不是可卖空组合。';
COMMENT ON TABLE research.factor_strategy IS '本人的单因子策略名称。';
COMMENT ON TABLE research.factor_strategy_version IS '不可变策略版本。旧回测继续指向原版本。';
COMMENT ON TABLE sim.cost_profile IS '本人的费用方案。';
COMMENT ON TABLE sim.cost_version IS '不可变费率。滑点用基点，费率用小数。';
COMMENT ON TABLE research.factor_bt_run IS '一次因子策略回测，绑定策略版本和费用版本。';
COMMENT ON TABLE research.factor_trade IS '信号日之后的成交或拒单。成交日必须晚于信号日。';
COMMENT ON TABLE research.factor_trade_cost IS '成交费用分项。拒单不写费用。';
COMMENT ON TABLE research.backtest_pnl IS '每日净权益、同成交毛权益和累计现金费用。';

ALTER TABLE research.factor_eval_run ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.factor_eval_run FORCE ROW LEVEL SECURITY;
ALTER TABLE research.factor_eval_value ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.factor_eval_value FORCE ROW LEVEL SECURITY;
ALTER TABLE research.factor_eval_daily ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.factor_eval_daily FORCE ROW LEVEL SECURITY;
ALTER TABLE research.factor_eval_group ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.factor_eval_group FORCE ROW LEVEL SECURITY;
ALTER TABLE research.factor_strategy ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.factor_strategy FORCE ROW LEVEL SECURITY;
ALTER TABLE research.factor_strategy_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.factor_strategy_version FORCE ROW LEVEL SECURITY;
ALTER TABLE sim.cost_profile ENABLE ROW LEVEL SECURITY;
ALTER TABLE sim.cost_profile FORCE ROW LEVEL SECURITY;
ALTER TABLE sim.cost_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE sim.cost_version FORCE ROW LEVEL SECURITY;
ALTER TABLE research.factor_bt_run ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.factor_bt_run FORCE ROW LEVEL SECURITY;
ALTER TABLE research.factor_trade ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.factor_trade FORCE ROW LEVEL SECURITY;
ALTER TABLE research.factor_trade_cost ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.factor_trade_cost FORCE ROW LEVEL SECURITY;
ALTER TABLE research.backtest_pnl ENABLE ROW LEVEL SECURITY;
ALTER TABLE research.backtest_pnl FORCE ROW LEVEL SECURITY;

DO $$
DECLARE item text;
BEGIN
    FOREACH item IN ARRAY ARRAY[
        'research.factor_eval_run','research.factor_eval_value','research.factor_eval_daily','research.factor_eval_group',
        'research.factor_strategy','research.factor_strategy_version','sim.cost_profile','sim.cost_version',
        'research.factor_bt_run','research.factor_trade','research.factor_trade_cost','research.backtest_pnl'
    ] LOOP
        EXECUTE format('DROP POLICY IF EXISTS own_all ON %s', item);
        EXECUTE format(
            'CREATE POLICY own_all ON %s FOR ALL TO app_user USING (owner_id = factor.app_user_id()) WITH CHECK (owner_id = factor.app_user_id())',
            item
        );
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %s TO app_user', item);
    END LOOP;
END $$;
GRANT USAGE ON SCHEMA research, sim TO app_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA research, sim TO app_user;
GRANT USAGE ON SCHEMA research, sim, factor TO quant_owner;
GRANT SELECT ON factor.definition, factor.version TO quant_owner;

ALTER TABLE sim.cost_version ADD COLUMN IF NOT EXISTS buy_commission_rate numeric;
ALTER TABLE sim.cost_version ADD COLUMN IF NOT EXISTS sell_commission_rate numeric;
ALTER TABLE sim.cost_version ADD COLUMN IF NOT EXISTS min_includes_fees boolean NOT NULL DEFAULT false;
ALTER TABLE research.factor_bt_run ADD COLUMN IF NOT EXISTS rule_snapshot jsonb;
ALTER TABLE research.factor_bt_run ADD COLUMN IF NOT EXISTS worker_id text;
ALTER TABLE research.factor_eval_run ADD COLUMN IF NOT EXISTS worker_id text;
DROP TRIGGER IF EXISTS freeze_cost_version ON sim.cost_version;
UPDATE sim.cost_version
SET buy_commission_rate = commission_rate
WHERE buy_commission_rate IS NULL;
UPDATE sim.cost_version
SET sell_commission_rate = commission_rate
WHERE sell_commission_rate IS NULL;

CREATE OR REPLACE FUNCTION research.fn_reject_version_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '已保存的版本不能修改或删除';
END $$;
ALTER FUNCTION research.fn_reject_version_change() OWNER TO quant_owner;

CREATE TRIGGER freeze_cost_version
    BEFORE UPDATE OR DELETE ON sim.cost_version
    FOR EACH ROW EXECUTE FUNCTION research.fn_reject_version_change();
DROP TRIGGER IF EXISTS freeze_strategy_version ON research.factor_strategy_version;
CREATE TRIGGER freeze_strategy_version
    BEFORE UPDATE OR DELETE ON research.factor_strategy_version
    FOR EACH ROW EXECUTE FUNCTION research.fn_reject_version_change();

REVOKE UPDATE, DELETE ON sim.cost_version, research.factor_strategy_version FROM app_user;

ALTER TABLE research.factor_eval_run ADD COLUMN IF NOT EXISTS engine_version text;
ALTER TABLE research.factor_eval_run ADD COLUMN IF NOT EXISTS data_fingerprint text;
ALTER TABLE research.factor_eval_run ADD COLUMN IF NOT EXISTS as_of_date date;
ALTER TABLE research.factor_bt_run ADD COLUMN IF NOT EXISTS engine_version text;
ALTER TABLE research.factor_bt_run ADD COLUMN IF NOT EXISTS data_fingerprint text;
ALTER TABLE research.factor_bt_run ADD COLUMN IF NOT EXISTS error_class text;

CREATE TABLE IF NOT EXISTS research.data_revision (
    revision_id integer PRIMARY KEY CHECK (revision_id = 1),
    version bigint NOT NULL
);
ALTER TABLE research.data_revision OWNER TO quant_owner;
INSERT INTO research.data_revision(revision_id, version) VALUES (1, 1) ON CONFLICT DO NOTHING;

CREATE OR REPLACE FUNCTION research.fn_bump_data_revision() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,research AS $$
BEGIN
    UPDATE research.data_revision SET version = version + 1 WHERE revision_id = 1;
    RETURN NULL;
END $$;
ALTER FUNCTION research.fn_bump_data_revision() OWNER TO quant_owner;

CREATE OR REPLACE FUNCTION research.fn_data_version() RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,research AS $$
    SELECT version::text FROM research.data_revision WHERE revision_id = 1
$$;
ALTER FUNCTION research.fn_data_version() OWNER TO quant_owner;
REVOKE ALL ON FUNCTION research.fn_data_version() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION research.fn_data_version() TO app_user;

DO $$
DECLARE item text;
BEGIN
    FOREACH item IN ARRAY ARRAY[
        'mkt.daily_bar','mkt.adj_factor',
        'ref.security','ref.security_status','ref.index_member','ref.trading_calendar',
        'ref.fee_rule','ref.price_limit_rule'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS bump_data_revision ON %s', item);
        EXECUTE format(
            'CREATE TRIGGER bump_data_revision AFTER INSERT OR UPDATE OR DELETE ON %s FOR EACH STATEMENT EXECUTE FUNCTION research.fn_bump_data_revision()',
            item
        );
    END LOOP;
END $$;

CREATE OR REPLACE FUNCTION research.fn_execution_block(
    p_code varchar, p_date date, p_side char(1), p_price numeric, p_signal date
) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,ref,mkt AS $$
DECLARE
    listed date;
    sessions integer;
    pct numeric;
    previous numeric;
    suspended boolean;
    bar_volume bigint;
BEGIN
    SELECT b.is_suspended, b.volume, b.pre_close INTO suspended, bar_volume, previous
    FROM mkt.daily_bar b WHERE b.stock_code=p_code AND b.trade_date=p_date;
    IF NOT FOUND THEN RETURN '执行日行情缺失'; END IF;
    IF suspended OR bar_volume=0 THEN RETURN '执行日停牌或无成交量'; END IF;
    SELECT list_date INTO listed FROM ref.security
    WHERE stock_code=p_code AND list_date<=p_date AND (delist_date IS NULL OR delist_date>p_date);
    IF listed IS NULL THEN RETURN '执行日未上市或已退市'; END IF;
    SELECT count(*) INTO sessions FROM ref.trading_calendar
    WHERE is_open AND cal_date BETWEEN listed AND p_date;
    IF sessions<=5 THEN RETURN '上市前五个交易日不撮合'; END IF;
    IF EXISTS (
        SELECT 1 FROM mkt.adj_factor
        WHERE stock_code=p_code AND ex_date>p_signal AND ex_date<=p_date
    ) THEN
        RETURN '跨除权事件，需有分红送转明细后才能撮合';
    END IF;
    pct:=ref.fn_limit_pct(p_code, p_date);
    IF p_side='B' AND previous IS NOT NULL AND p_price>=round(previous*(1+pct),2) THEN
        RETURN '涨停不买入';
    END IF;
    IF p_side='S' AND previous IS NOT NULL AND p_price<=round(previous*(1-pct),2) THEN
        RETURN '跌停不卖出';
    END IF;
    RETURN NULL;
END $$;
ALTER FUNCTION research.fn_execution_block(varchar,date,char,numeric,date) OWNER TO quant_owner;
REVOKE ALL ON FUNCTION research.fn_execution_block(varchar,date,char,numeric,date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION research.fn_execution_block(varchar,date,char,numeric,date) TO app_user;

CREATE OR REPLACE FUNCTION research.fn_holding_event(p_code varchar, p_prev date, p_date date) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,ref,mkt AS $$
BEGIN
    IF p_prev IS NULL OR p_date IS NULL OR p_date<=p_prev THEN RETURN NULL; END IF;
    IF EXISTS (
        SELECT 1 FROM mkt.adj_factor a
        WHERE a.stock_code=p_code AND a.ex_date>p_prev AND a.ex_date<=p_date
    ) THEN
        RETURN '持仓跨过未支持的除权事件';
    END IF;
    IF EXISTS (
        SELECT 1 FROM ref.security s
        WHERE s.stock_code=p_code AND s.delist_date IS NOT NULL
          AND s.delist_date>p_prev AND s.delist_date<=p_date
    ) THEN
        RETURN '持仓跨过退市，缺少退市结算';
    END IF;
    RETURN NULL;
END $$;
ALTER FUNCTION research.fn_holding_event(varchar,date,date) OWNER TO quant_owner;
REVOKE ALL ON FUNCTION research.fn_holding_event(varchar,date,date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION research.fn_holding_event(varchar,date,date) TO app_user;
