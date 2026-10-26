-- A：一行是一家公司、一个报告期的一次披露，而不是“永远只有一个最新值”。
CREATE TABLE IF NOT EXISTS staging.financial_raw (
    raw_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_file text NOT NULL,
    content_hash char(64) NOT NULL,
    line_no integer NOT NULL CHECK (line_no>=2),
    payload jsonb NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(content_hash,line_no)
);
COMMENT ON TABLE staging.financial_raw IS '通过校验的财务 CSV 原行；文件摘要和行号用于追溯、避免重复落地。';
CREATE TABLE IF NOT EXISTS fin.report (
    stock_code varchar(12) NOT NULL REFERENCES ref.security,
    report_period date NOT NULL,
    announce_date date NOT NULL,
    available_date date NOT NULL,
    is_restated boolean NOT NULL DEFAULT false,
    revenue numeric(20,2),
    net_profit numeric(20,2),
    total_assets numeric(20,2),
    total_equity numeric(20,2),
    eps numeric(12,4),
    roe numeric(10,6),
    source varchar(32) NOT NULL,
    source_url text NOT NULL,
    source_kind varchar(20) NOT NULL CHECK (source_kind IN ('DISCLOSURE','PROVIDER_SNAPSHOT','SYNTHETIC')),
    period_basis varchar(12) NOT NULL CHECK (period_basis IN ('YTD','QUARTER','ANNUAL')),
    profit_basis varchar(16) NOT NULL CHECK (profit_basis IN ('CONSOLIDATED','PARENT')),
    eps_basis varchar(12) NOT NULL DEFAULT 'BASIC' CHECK (eps_basis IN ('BASIC','TTM')),
    notes text NOT NULL DEFAULT '',
    sys_period tstzrange NOT NULL DEFAULT tstzrange(clock_timestamp(), NULL, '[)'),
    PRIMARY KEY (stock_code, report_period, announce_date),
    CHECK (report_period=(date_trunc('quarter', report_period)+interval '3 months - 1 day')::date),
    CHECK (announce_date > report_period AND available_date >= announce_date),
    CHECK (btrim(source_url) <> '')
);
CREATE INDEX IF NOT EXISTS ix_fin_available ON fin.report(available_date, stock_code);
CREATE TABLE IF NOT EXISTS fin.report_history (LIKE fin.report);
CREATE INDEX IF NOT EXISTS ix_fin_history_time ON fin.report_history USING gist(sys_period);
COMMENT ON TABLE fin.report IS '当前数据库中的披露版本；金额为人民币元，ROE 为小数，公告按日终口径可见。';
COMMENT ON TABLE fin.report_history IS '本地修改或删除前的旧记录；与公司后续发布的更正版本分别保存。';
COMMENT ON COLUMN fin.report.available_date IS '允许用于研究的最早日期。已核对公告用公告日；未核对的供应商快照用采集日。';
COMMENT ON COLUMN fin.report.sys_period IS '本系统记录该行的时间区间；用于重现过去库内的财务数据。';
COMMENT ON COLUMN fin.report.roe IS '比例，如 0.15 表示 15%；录入时明确核对供应商单位。';

CREATE OR REPLACE FUNCTION fin.trg_report_versioning()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_time timestamptz := clock_timestamp();
BEGIN
    -- clock_timestamp 每次实际取时，支持同一事务内多次更正。
    OLD.sys_period := tstzrange(lower(OLD.sys_period), v_time, '[)');
    INSERT INTO fin.report_history SELECT OLD.*;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    NEW.sys_period := tstzrange(v_time, NULL, '[)');
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_report_versioning ON fin.report;
CREATE TRIGGER trg_report_versioning BEFORE UPDATE OR DELETE ON fin.report
FOR EACH ROW EXECUTE FUNCTION fin.trg_report_versioning();

CREATE OR REPLACE FUNCTION fin.fn_report_as_recorded(p_ts timestamptz)
RETURNS SETOF fin.report LANGUAGE sql STABLE AS $$
    SELECT * FROM fin.report WHERE p_ts IS NULL OR sys_period @> p_ts
    UNION ALL
    SELECT * FROM fin.report_history WHERE p_ts IS NOT NULL AND sys_period @> p_ts
$$;
CREATE OR REPLACE VIEW fin.v_report_known AS
SELECT r.*, daterange(available_date,
       lead(available_date) OVER (PARTITION BY stock_code, report_period
                                 ORDER BY available_date, announce_date), '[)') AS known_range
FROM fin.report r;
COMMENT ON VIEW fin.v_report_known IS '管理员查看当前披露版本的知悉区间；PIT 查询另从指定系统快照中选版本。';
COMMENT ON FUNCTION fin.fn_report_as_recorded(timestamptz) IS '按本地系统时间恢复财务行；NULL 表示当前记录。';
COMMENT ON FUNCTION fin.trg_report_versioning() IS '更新或删除时先保存旧行及系统存续时间。';
ALTER TABLE fin.report OWNER TO quant_owner;
ALTER TABLE fin.report_history OWNER TO quant_owner;
ALTER VIEW fin.v_report_known OWNER TO quant_owner;
ALTER FUNCTION fin.trg_report_versioning() OWNER TO quant_owner;
ALTER FUNCTION fin.fn_report_as_recorded(timestamptz) OWNER TO quant_owner;
GRANT USAGE ON SCHEMA fin TO etl_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON fin.report TO etl_writer;
GRANT SELECT, INSERT ON fin.report_history TO etl_writer;
ALTER TABLE staging.financial_raw OWNER TO quant_owner;
GRANT SELECT,INSERT ON staging.financial_raw TO etl_writer;
GRANT USAGE ON SEQUENCE staging.financial_raw_raw_id_seq TO etl_writer;

-- 字段说明，便于在数据库工具里直接查看。
COMMENT ON COLUMN fin.report.stock_code IS '股票代码，格式为 sh/sz/bj.六位数字。';
COMMENT ON COLUMN fin.report.report_period IS '财报统计截止日，季度末。';
COMMENT ON COLUMN fin.report.announce_date IS '这一次披露的公告日，以公开日期为准。';
COMMENT ON COLUMN fin.report.is_restated IS '该披露是否为更正版本；不对 PIT 调用方暴露。';
COMMENT ON COLUMN fin.report.revenue IS '人民币收入，具体业务口径见 notes。';
COMMENT ON COLUMN fin.report.net_profit IS '人民币净利润；归母或合并口径见 profit_basis。';
COMMENT ON COLUMN fin.report.total_assets IS '合并总资产，人民币元。';
COMMENT ON COLUMN fin.report.total_equity IS '合并所有者权益总额，人民币元。';
COMMENT ON COLUMN fin.report.eps IS '每股收益，元/股；BASIC 或 TTM 见 eps_basis。';
COMMENT ON COLUMN fin.report.source IS '数据或规则来源；教学数据明确标记 SYNTHETIC。';
COMMENT ON COLUMN fin.report.source_url IS '原公告、供应商文档或教学案例标识。';
COMMENT ON COLUMN fin.report.source_kind IS 'DISCLOSURE 核对公告、PROVIDER_SNAPSHOT 采集快照、SYNTHETIC 合成。';
COMMENT ON COLUMN fin.report.period_basis IS 'YTD 年初累计、QUARTER 单季、ANNUAL 全年。';
COMMENT ON COLUMN fin.report.profit_basis IS 'PARENT 归母净利润，CONSOLIDATED 合并净利润。';
COMMENT ON COLUMN fin.report.eps_basis IS 'BASIC 基本每股收益，TTM 滚动每股收益。';
COMMENT ON COLUMN fin.report.notes IS '口径、来源范围及未核对字段说明。';
COMMENT ON COLUMN fin.report_history.stock_code IS '股票代码，格式为 sh/sz/bj.六位数字。';
COMMENT ON COLUMN fin.report_history.report_period IS '财报统计截止日，季度末。';
COMMENT ON COLUMN fin.report_history.announce_date IS '这一次披露的公告日，以公开日期为准。';
COMMENT ON COLUMN fin.report_history.available_date IS '允许用于研究的最早日期：核对公告用公告日，供应商快照用采集日。';
COMMENT ON COLUMN fin.report_history.is_restated IS '该披露是否为更正版本；不对 PIT 调用方暴露。';
COMMENT ON COLUMN fin.report_history.revenue IS '人民币收入，具体业务口径见 notes。';
COMMENT ON COLUMN fin.report_history.net_profit IS '人民币净利润；归母或合并口径见 profit_basis。';
COMMENT ON COLUMN fin.report_history.total_assets IS '合并总资产，人民币元。';
COMMENT ON COLUMN fin.report_history.total_equity IS '合并所有者权益总额，人民币元。';
COMMENT ON COLUMN fin.report_history.eps IS '每股收益，元/股；BASIC 或 TTM 见 eps_basis。';
COMMENT ON COLUMN fin.report_history.roe IS '收益率小数；0.15 表示 15%。';
COMMENT ON COLUMN fin.report_history.source IS '数据或规则来源；教学数据明确标记 SYNTHETIC。';
COMMENT ON COLUMN fin.report_history.source_url IS '原公告、供应商文档或教学案例标识。';
COMMENT ON COLUMN fin.report_history.source_kind IS 'DISCLOSURE 核对公告、PROVIDER_SNAPSHOT 采集快照、SYNTHETIC 合成。';
COMMENT ON COLUMN fin.report_history.period_basis IS 'YTD 年初累计、QUARTER 单季、ANNUAL 全年。';
COMMENT ON COLUMN fin.report_history.profit_basis IS 'PARENT 归母净利润，CONSOLIDATED 合并净利润。';
COMMENT ON COLUMN fin.report_history.eps_basis IS 'BASIC 基本每股收益，TTM 滚动每股收益。';
COMMENT ON COLUMN fin.report_history.notes IS '口径、来源范围及未核对字段说明。';
COMMENT ON COLUMN fin.report_history.sys_period IS '本地系统记录该版本的时间区间。';
COMMENT ON COLUMN fin.v_report_known.stock_code IS '股票代码，格式为 sh/sz/bj.六位数字。';
COMMENT ON COLUMN fin.v_report_known.report_period IS '财报统计截止日，季度末。';
COMMENT ON COLUMN fin.v_report_known.announce_date IS '这一次披露的公告日，以公开日期为准。';
COMMENT ON COLUMN fin.v_report_known.available_date IS '允许用于研究的最早日期：核对公告用公告日，供应商快照用采集日。';
COMMENT ON COLUMN fin.v_report_known.is_restated IS '该披露是否为更正版本；不对 PIT 调用方暴露。';
COMMENT ON COLUMN fin.v_report_known.revenue IS '人民币收入，具体业务口径见 notes。';
COMMENT ON COLUMN fin.v_report_known.net_profit IS '人民币净利润；归母或合并口径见 profit_basis。';
COMMENT ON COLUMN fin.v_report_known.total_assets IS '合并总资产，人民币元。';
COMMENT ON COLUMN fin.v_report_known.total_equity IS '合并所有者权益总额，人民币元。';
COMMENT ON COLUMN fin.v_report_known.eps IS '每股收益，元/股；BASIC 或 TTM 见 eps_basis。';
COMMENT ON COLUMN fin.v_report_known.roe IS '收益率小数；0.15 表示 15%。';
COMMENT ON COLUMN fin.v_report_known.source IS '数据或规则来源；教学数据明确标记 SYNTHETIC。';
COMMENT ON COLUMN fin.v_report_known.source_url IS '原公告、供应商文档或教学案例标识。';
COMMENT ON COLUMN fin.v_report_known.source_kind IS 'DISCLOSURE 核对公告、PROVIDER_SNAPSHOT 采集快照、SYNTHETIC 合成。';
COMMENT ON COLUMN fin.v_report_known.period_basis IS 'YTD 年初累计、QUARTER 单季、ANNUAL 全年。';
COMMENT ON COLUMN fin.v_report_known.profit_basis IS 'PARENT 归母净利润，CONSOLIDATED 合并净利润。';
COMMENT ON COLUMN fin.v_report_known.eps_basis IS 'BASIC 基本每股收益，TTM 滚动每股收益。';
COMMENT ON COLUMN fin.v_report_known.notes IS '口径、来源范围及未核对字段说明。';
COMMENT ON COLUMN fin.v_report_known.sys_period IS '本地系统记录该版本的时间区间。';
COMMENT ON COLUMN fin.v_report_known.known_range IS '由各次披露可用日期推导的知悉区间；仅管理员查看。';
COMMENT ON COLUMN staging.financial_raw.raw_id IS '原始行自增编号。';
COMMENT ON COLUMN staging.financial_raw.source_file IS '原始 CSV 的文件名。';
COMMENT ON COLUMN staging.financial_raw.content_hash IS '原始文件内容 SHA-256，识别重复文件。';
COMMENT ON COLUMN staging.financial_raw.line_no IS 'CSV 原始行号，表头为第 1 行。';
COMMENT ON COLUMN staging.financial_raw.payload IS '原始 CSV 行，保留未映射字段。';
COMMENT ON COLUMN staging.financial_raw.loaded_at IS '本地落地时间。';
