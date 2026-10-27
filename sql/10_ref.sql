-- A：证券基础资料及名称、状态、行业和成分股的有效期。
-- 保持 B 的证券、日历字段不变，因此原来的行情代码可以直接复用。
CREATE TABLE IF NOT EXISTS ref.trading_calendar (
    cal_date date PRIMARY KEY,
    is_open boolean NOT NULL
);
COMMENT ON TABLE ref.trading_calendar IS '交易日历；真实市场使用交易所日历，合成样本单独使用模拟日历。';
CREATE OR REPLACE VIEW ref.v_trade_day AS
SELECT cal_date AS trade_date,
       lag(cal_date) OVER (ORDER BY cal_date) AS prev_trade_date,
       lead(cal_date) OVER (ORDER BY cal_date) AS next_trade_date
FROM ref.trading_calendar WHERE is_open;

CREATE TABLE IF NOT EXISTS ref.security (
    stock_code varchar(12) PRIMARY KEY CHECK (stock_code ~ '^(sh|sz|bj)\.[0-9]{6}$'),
    exchange char(2) NOT NULL CHECK (exchange IN ('SH','SZ','BJ')),
    board varchar(8) NOT NULL CHECK (board IN ('MAIN','GEM','STAR','BSE')),
    list_date date NOT NULL,
    delist_date date,
    CHECK (delist_date IS NULL OR delist_date > list_date)
);
COMMENT ON TABLE ref.security IS '证券主表；代码格式与 B 的行情表一致。';
-- 从 B 的开发 stub 接入时，也补上正式代码格式检查。
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='ref.security'::regclass
                   AND conname='security_stock_code_check') THEN
        ALTER TABLE ref.security ADD CONSTRAINT security_stock_code_check
        CHECK (stock_code ~ '^(sh|sz|bj)\.[0-9]{6}$');
    END IF;
END $$;

-- 区间统一为 [开始日, 结束日)：包含开始日，不包含结束日。
-- 排他约束表示：同一股票不能同时拥有两条互相重叠的状态。
CREATE TABLE IF NOT EXISTS ref.security_name (
    stock_code varchar(12) NOT NULL REFERENCES ref.security,
    name text NOT NULL CHECK (btrim(name) <> ''),
    valid daterange NOT NULL CHECK (NOT isempty(valid) AND lower(valid) IS NOT NULL),
    source text NOT NULL,
    PRIMARY KEY (stock_code, valid),
    EXCLUDE USING gist (stock_code WITH =, valid WITH &&)
);
CREATE TABLE IF NOT EXISTS ref.security_status (
    stock_code varchar(12) NOT NULL REFERENCES ref.security,
    status varchar(8) NOT NULL CHECK (status IN ('NORMAL','ST','*ST')),
    valid daterange NOT NULL CHECK (NOT isempty(valid) AND lower(valid) IS NOT NULL),
    source text NOT NULL,
    PRIMARY KEY (stock_code, valid),
    EXCLUDE USING gist (stock_code WITH =, valid WITH &&)
);
CREATE TABLE IF NOT EXISTS ref.industry (
    industry_code varchar(16) PRIMARY KEY,
    name text NOT NULL,
    parent_code varchar(16) REFERENCES ref.industry,
    CHECK (parent_code IS DISTINCT FROM industry_code)
);
CREATE TABLE IF NOT EXISTS ref.industry_member (
    stock_code varchar(12) NOT NULL REFERENCES ref.security,
    industry_code varchar(16) NOT NULL REFERENCES ref.industry,
    valid daterange NOT NULL CHECK (NOT isempty(valid) AND lower(valid) IS NOT NULL),
    source text NOT NULL,
    PRIMARY KEY (stock_code, valid),
    EXCLUDE USING gist (stock_code WITH =, valid WITH &&)
);
CREATE TABLE IF NOT EXISTS ref.index_info (
    index_code varchar(12) PRIMARY KEY,
    name text NOT NULL
);
CREATE TABLE IF NOT EXISTS ref.index_member (
    index_code varchar(12) NOT NULL REFERENCES ref.index_info,
    stock_code varchar(12) NOT NULL REFERENCES ref.security,
    valid daterange NOT NULL CHECK (NOT isempty(valid) AND lower(valid) IS NOT NULL),
    source text NOT NULL,
    PRIMARY KEY (index_code, stock_code, valid),
    EXCLUDE USING gist (index_code WITH =, stock_code WITH =, valid WITH &&)
);
COMMENT ON TABLE ref.security_name IS '证券简称历史；今天的名称只从本次观察日记录，不倒填到上市日。';
COMMENT ON TABLE ref.security_status IS '风险警示历史；BaoStock 的 isST 仅映射 NORMAL/ST，不猜测 *ST。';
COMMENT ON TABLE ref.industry IS '单一分类体系的行业树。';
COMMENT ON TABLE ref.industry_member IS '证券所属行业的有效区间；每个时点只记录一个行业。';
COMMENT ON TABLE ref.index_info IS '指数目录；SYNTH 开头的代码仅用于教学演示。';
COMMENT ON TABLE ref.index_member IS '指数成员历史；区间边界来自公告或明确标注的合成案例。';

CREATE TABLE IF NOT EXISTS ref.price_limit_rule (
    rule_id int GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    board varchar(8) NOT NULL CHECK (board IN ('MAIN','GEM','STAR','BSE')),
    status_class varchar(8) NOT NULL CHECK (status_class IN ('NORMAL','ST')),
    limit_pct numeric(5,4) NOT NULL CHECK (limit_pct > 0 AND limit_pct < 1),
    valid daterange NOT NULL CHECK (NOT isempty(valid)),
    source text NOT NULL,
    EXCLUDE USING gist (board WITH =, status_class WITH =, valid WITH &&)
);
CREATE TABLE IF NOT EXISTS ref.fee_rule (
    rule_id int GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fee_type varchar(16) NOT NULL CHECK (fee_type IN ('COMMISSION','STAMP_DUTY','TRANSFER')),
    side char(1) NOT NULL CHECK (side IN ('B','S')),
    rate numeric(10,8) NOT NULL CHECK (rate >= 0),
    min_amount numeric(10,2) NOT NULL DEFAULT 0 CHECK (min_amount >= 0),
    valid daterange NOT NULL CHECK (NOT isempty(valid)),
    source text NOT NULL,
    EXCLUDE USING gist (fee_type WITH =, side WITH =, valid WITH &&)
);
COMMENT ON TABLE ref.price_limit_rule IS '普通交易日的涨跌幅规则；不覆盖新股上市初期等特殊情形。';
COMMENT ON TABLE ref.fee_rule IS '教学账户费率；佣金是显式假设，印花税和过户费保留公开来源。';

CREATE OR REPLACE FUNCTION ref.fn_fee(p_date date, p_side char(1), p_amount numeric)
RETURNS numeric LANGUAGE plpgsql STABLE AS $$
DECLARE v_count int; v_total numeric;
BEGIN
    IF p_date IS NULL OR p_side IS NULL OR p_side NOT IN ('B','S')
       OR p_amount IS NULL OR p_amount < 0 THEN
        RAISE EXCEPTION '日期、买卖方向或成交金额不合法';
    END IF;
    SELECT count(*), sum(round(greatest(p_amount * rate, min_amount), 2))
      INTO v_count, v_total FROM ref.fee_rule WHERE side=p_side AND valid @> p_date;
    -- 买卖两边都要明确记录三类费用，买方印花税可明确为 0。
    IF v_count <> 3 THEN RAISE EXCEPTION '该日期的三类费率尚未配齐：%', p_date; END IF;
    IF p_amount = 0 THEN RETURN 0; END IF;
    RETURN v_total;
END $$;

CREATE OR REPLACE FUNCTION ref.fn_limit_pct(p_code varchar, p_date date)
RETURNS numeric LANGUAGE plpgsql STABLE AS $$
DECLARE v_limit numeric;
BEGIN
    SELECT r.limit_pct INTO v_limit
    FROM ref.security s
    JOIN ref.security_status st ON st.stock_code=s.stock_code AND st.valid @> p_date
    JOIN ref.price_limit_rule r ON r.board=s.board
      AND r.status_class=CASE WHEN st.status='NORMAL' THEN 'NORMAL' ELSE 'ST' END
      AND r.valid @> p_date
    WHERE s.stock_code=p_code AND s.list_date<=p_date
      AND (s.delist_date IS NULL OR p_date<s.delist_date);
    IF v_limit IS NULL THEN RAISE EXCEPTION '该股票/日期的状态或规则缺失：% %', p_code, p_date; END IF;
    RETURN v_limit;
END $$;
COMMENT ON FUNCTION ref.fn_fee(date,char,numeric) IS '按成交日期逐项计算并四舍五入费用；缺失配置时报错。';
COMMENT ON FUNCTION ref.fn_limit_pct(varchar,date) IS '关联证券、历史状态和规则；未知状态不按正常股票猜测。';

-- B 已创建这些角色。表归 owner；ETL 可维护数据；Agent 不直接访问原表。
ALTER TABLE ref.trading_calendar OWNER TO quant_owner;
ALTER TABLE ref.security OWNER TO quant_owner;
ALTER TABLE ref.security_name OWNER TO quant_owner;
ALTER TABLE ref.security_status OWNER TO quant_owner;
ALTER TABLE ref.industry OWNER TO quant_owner;
ALTER TABLE ref.industry_member OWNER TO quant_owner;
ALTER TABLE ref.index_info OWNER TO quant_owner;
ALTER TABLE ref.index_member OWNER TO quant_owner;
ALTER TABLE ref.price_limit_rule OWNER TO quant_owner;
ALTER TABLE ref.fee_rule OWNER TO quant_owner;
ALTER VIEW ref.v_trade_day OWNER TO quant_owner;
ALTER FUNCTION ref.fn_fee(date,char,numeric) OWNER TO quant_owner;
ALTER FUNCTION ref.fn_limit_pct(varchar,date) OWNER TO quant_owner;
GRANT USAGE ON SCHEMA ref TO etl_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ref TO etl_writer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ref TO etl_writer;

-- 字段说明，便于在数据库工具里直接查看。
COMMENT ON COLUMN ref.fee_rule.rule_id IS '规则行自增主键。';
COMMENT ON COLUMN ref.fee_rule.fee_type IS '费用类型：佣金、印花税或过户费。';
COMMENT ON COLUMN ref.fee_rule.side IS 'B 买入，S 卖出。';
COMMENT ON COLUMN ref.fee_rule.rate IS '费率小数，0.0003 表示万分之三。';
COMMENT ON COLUMN ref.fee_rule.min_amount IS '该项费用的最低人民币金额。';
COMMENT ON COLUMN ref.fee_rule.valid IS '业务有效区间，左闭右开；PIT 中上界最多为研究日次日。';
COMMENT ON COLUMN ref.fee_rule.source IS '数据或规则来源；教学数据明确标记 SYNTHETIC。';
COMMENT ON COLUMN ref.index_info.index_code IS '指数代码；SYNTH300 是教学指数。';
COMMENT ON COLUMN ref.index_info.name IS '当条记录对应的名称。';
COMMENT ON COLUMN ref.index_member.index_code IS '指数代码；SYNTH300 是教学指数。';
COMMENT ON COLUMN ref.index_member.stock_code IS '股票代码，格式为 sh/sz/bj.六位数字。';
COMMENT ON COLUMN ref.index_member.valid IS '业务有效区间，左闭右开；PIT 中上界最多为研究日次日。';
COMMENT ON COLUMN ref.index_member.source IS '数据或规则来源；教学数据明确标记 SYNTHETIC。';
COMMENT ON COLUMN ref.industry.industry_code IS '单一分类体系中的行业代码。';
COMMENT ON COLUMN ref.industry.name IS '当条记录对应的名称。';
COMMENT ON COLUMN ref.industry.parent_code IS '上级行业代码；根行业为空。';
COMMENT ON COLUMN ref.industry_member.stock_code IS '股票代码，格式为 sh/sz/bj.六位数字。';
COMMENT ON COLUMN ref.industry_member.industry_code IS '单一分类体系中的行业代码。';
COMMENT ON COLUMN ref.industry_member.valid IS '业务有效区间，左闭右开；PIT 中上界最多为研究日次日。';
COMMENT ON COLUMN ref.industry_member.source IS '数据或规则来源；教学数据明确标记 SYNTHETIC。';
COMMENT ON COLUMN ref.price_limit_rule.rule_id IS '规则行自增主键。';
COMMENT ON COLUMN ref.price_limit_rule.board IS '板块：MAIN/GEM/STAR/BSE。';
COMMENT ON COLUMN ref.price_limit_rule.status_class IS 'NORMAL 为正常；ST 统一覆盖 ST 与 *ST。';
COMMENT ON COLUMN ref.price_limit_rule.limit_pct IS '普通交易日涨跌幅比例；0.1 表示 10%。';
COMMENT ON COLUMN ref.price_limit_rule.valid IS '业务有效区间，左闭右开；PIT 中上界最多为研究日次日。';
COMMENT ON COLUMN ref.price_limit_rule.source IS '数据或规则来源；教学数据明确标记 SYNTHETIC。';
COMMENT ON COLUMN ref.security.stock_code IS '股票代码，格式为 sh/sz/bj.六位数字。';
COMMENT ON COLUMN ref.security.exchange IS '交易所：SH/SZ/BJ。';
COMMENT ON COLUMN ref.security.board IS '板块：MAIN/GEM/STAR/BSE。';
COMMENT ON COLUMN ref.security.list_date IS '上市日期，包含当天。';
COMMENT ON COLUMN ref.security.delist_date IS '摘牌日期，PIT 隐藏尚未发生的摘牌日期。';
COMMENT ON COLUMN ref.security_name.stock_code IS '股票代码，格式为 sh/sz/bj.六位数字。';
COMMENT ON COLUMN ref.security_name.name IS '当条记录对应的名称。';
COMMENT ON COLUMN ref.security_name.valid IS '业务有效区间，左闭右开；PIT 中上界最多为研究日次日。';
COMMENT ON COLUMN ref.security_name.source IS '数据或规则来源；教学数据明确标记 SYNTHETIC。';
COMMENT ON COLUMN ref.security_status.stock_code IS '股票代码，格式为 sh/sz/bj.六位数字。';
COMMENT ON COLUMN ref.security_status.status IS '证券状态：NORMAL/ST/*ST。';
COMMENT ON COLUMN ref.security_status.valid IS '业务有效区间，左闭右开；PIT 中上界最多为研究日次日。';
COMMENT ON COLUMN ref.security_status.source IS '数据或规则来源；教学数据明确标记 SYNTHETIC。';
COMMENT ON COLUMN ref.trading_calendar.cal_date IS '自然日期。';
COMMENT ON COLUMN ref.trading_calendar.is_open IS '该市场当天是否开市。';
COMMENT ON COLUMN ref.v_trade_day.trade_date IS '交易日期。';
COMMENT ON COLUMN ref.v_trade_day.prev_trade_date IS '日历中前一个交易日。';
COMMENT ON COLUMN ref.v_trade_day.next_trade_date IS '日历中后一个交易日。';
COMMENT ON VIEW ref.v_trade_day IS '交易日及相邻交易日；属于原始日历管理视图。';
