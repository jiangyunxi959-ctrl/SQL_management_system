-- 知时：行情、复权、ETL 与指标（B 负责）
-- 依赖：00_init.sql；正式环境依赖 A 的 ref.security / ref.trading_calendar。

-- =========================
-- 1. ETL 批次与 staging
-- =========================
CREATE TABLE IF NOT EXISTS etl.load_batch (
    batch_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source      varchar(16) NOT NULL,
    target      text        NOT NULL,
    params      jsonb,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    rows_in     int,
    rows_upsert int,
    rows_reject int,
    status      varchar(8) NOT NULL DEFAULT 'RUNNING'
                CHECK (status IN ('RUNNING', 'OK', 'FAILED'))
);
ALTER TABLE etl.load_batch OWNER TO quant_owner;
COMMENT ON TABLE etl.load_batch IS 'ETL 导入批次；记录来源、参数、输入/写入/拒绝行数和状态。';
COMMENT ON COLUMN etl.load_batch.batch_id IS 'ETL 批次主键。';
COMMENT ON COLUMN etl.load_batch.source IS '数据来源，如 BAOSTOCK/SAMPLE。';
COMMENT ON COLUMN etl.load_batch.target IS '目标对象，如 mkt.daily_bar。';
COMMENT ON COLUMN etl.load_batch.params IS '本批次下载或导入参数。';
COMMENT ON COLUMN etl.load_batch.started_at IS '批次开始时间。';
COMMENT ON COLUMN etl.load_batch.finished_at IS '批次结束时间。';
COMMENT ON COLUMN etl.load_batch.rows_in IS '进入 staging 的行数。';
COMMENT ON COLUMN etl.load_batch.rows_upsert IS '实际插入或发生变化更新的行数。';
COMMENT ON COLUMN etl.load_batch.rows_reject IS '校验未通过的行数。';
COMMENT ON COLUMN etl.load_batch.status IS 'RUNNING/OK/FAILED。';

CREATE TABLE IF NOT EXISTS staging.daily_bar_raw (
    raw_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id     bigint NOT NULL REFERENCES etl.load_batch(batch_id) ON DELETE CASCADE,
    date         text,
    code         text,
    open         text,
    high         text,
    low          text,
    close        text,
    preclose     text,
    volume       text,
    amount       text,
    adjustflag   text,
    turn         text,
    tradestatus  text,
    pctChg       text,
    peTTM        text,
    pbMRQ        text,
    psTTM        text,
    pcfNcfTTM    text,
    isST         text
);
ALTER TABLE staging.daily_bar_raw OWNER TO quant_owner;
COMMENT ON TABLE staging.daily_bar_raw IS 'BaoStock 日 K 原始落地表；业务字段保持文本，便于定位脏数据。';
COMMENT ON COLUMN staging.daily_bar_raw.raw_id IS '原始落地行主键；不使用业务键，允许坏行/重复行进入 staging 后再校验。';
COMMENT ON COLUMN staging.daily_bar_raw.batch_id IS '所属 ETL 批次。';
COMMENT ON COLUMN staging.daily_bar_raw.date IS 'BaoStock date：交易日文本。';
COMMENT ON COLUMN staging.daily_bar_raw.code IS 'BaoStock code：证券代码。';
COMMENT ON COLUMN staging.daily_bar_raw.open IS 'BaoStock open：开盘价文本。';
COMMENT ON COLUMN staging.daily_bar_raw.high IS 'BaoStock high：最高价文本。';
COMMENT ON COLUMN staging.daily_bar_raw.low IS 'BaoStock low：最低价文本。';
COMMENT ON COLUMN staging.daily_bar_raw.close IS 'BaoStock close：收盘价文本。';
COMMENT ON COLUMN staging.daily_bar_raw.preclose IS 'BaoStock preclose：前收价文本。';
COMMENT ON COLUMN staging.daily_bar_raw.volume IS 'BaoStock volume：成交量文本。';
COMMENT ON COLUMN staging.daily_bar_raw.amount IS 'BaoStock amount：成交额文本。';
COMMENT ON COLUMN staging.daily_bar_raw.adjustflag IS 'BaoStock adjustflag；本项目原始行情固定下载 3=不复权。';
COMMENT ON COLUMN staging.daily_bar_raw.turn IS 'BaoStock turn：换手率文本。';
COMMENT ON COLUMN staging.daily_bar_raw.tradestatus IS 'BaoStock tradestatus：1 正常、0 停牌。';
COMMENT ON COLUMN staging.daily_bar_raw.pctChg IS 'BaoStock pctChg：涨跌幅百分比文本。';
COMMENT ON COLUMN staging.daily_bar_raw.peTTM IS 'BaoStock peTTM。';
COMMENT ON COLUMN staging.daily_bar_raw.pbMRQ IS 'BaoStock pbMRQ。';
COMMENT ON COLUMN staging.daily_bar_raw.psTTM IS 'BaoStock psTTM。';
COMMENT ON COLUMN staging.daily_bar_raw.pcfNcfTTM IS 'BaoStock pcfNcfTTM。';
COMMENT ON COLUMN staging.daily_bar_raw.isST IS 'BaoStock isST：0/1；供 A 转换为状态区间。';

CREATE TABLE IF NOT EXISTS staging.adjust_factor_raw (
    raw_id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id          bigint NOT NULL REFERENCES etl.load_batch(batch_id) ON DELETE CASCADE,
    code              text,
    "dividOperateDate" text,
    "foreAdjustFactor" text,
    "backAdjustFactor" text,
    "adjustFactor"     text
);
ALTER TABLE staging.adjust_factor_raw OWNER TO quant_owner;
COMMENT ON TABLE staging.adjust_factor_raw IS 'BaoStock 复权因子原始落地表。';
COMMENT ON COLUMN staging.adjust_factor_raw.raw_id IS '原始落地行主键。';
COMMENT ON COLUMN staging.adjust_factor_raw.batch_id IS '所属 ETL 批次。';
COMMENT ON COLUMN staging.adjust_factor_raw.code IS '证券代码。';
COMMENT ON COLUMN staging.adjust_factor_raw."dividOperateDate" IS '除权除息日。';
COMMENT ON COLUMN staging.adjust_factor_raw."foreAdjustFactor" IS 'BaoStock 前复权因子。';
COMMENT ON COLUMN staging.adjust_factor_raw."backAdjustFactor" IS 'BaoStock 后复权因子；本项目使用该列。';
COMMENT ON COLUMN staging.adjust_factor_raw."adjustFactor" IS 'BaoStock 本次复权因子。';

CREATE TABLE IF NOT EXISTS etl.reject_row (
    reject_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id   bigint NOT NULL REFERENCES etl.load_batch(batch_id) ON DELETE CASCADE,
    row_key    text,
    reason     text NOT NULL,
    raw_row    jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE etl.reject_row OWNER TO quant_owner;
COMMENT ON TABLE etl.reject_row IS 'ETL 被拒绝的原始行及原因，便于定位数据问题。';
COMMENT ON COLUMN etl.reject_row.reject_id IS '拒绝记录主键。';
COMMENT ON COLUMN etl.reject_row.batch_id IS '所属 ETL 批次。';
COMMENT ON COLUMN etl.reject_row.row_key IS '便于定位的业务键，一般为 code/date。';
COMMENT ON COLUMN etl.reject_row.reason IS '拒绝原因。';
COMMENT ON COLUMN etl.reject_row.raw_row IS '原始行 JSON。';
COMMENT ON COLUMN etl.reject_row.created_at IS '记录时间。';

-- 安全转换函数：staging 的坏文本不会让整批 CAST 失败。
CREATE OR REPLACE FUNCTION etl.try_date(p_text text)
RETURNS date LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
    IF p_text IS NULL OR btrim(p_text) = '' THEN RETURN NULL; END IF;
    RETURN p_text::date;
EXCEPTION WHEN others THEN
    RETURN NULL;
END $$;
ALTER FUNCTION etl.try_date(text) OWNER TO quant_owner;
COMMENT ON FUNCTION etl.try_date(text) IS '文本安全转 date；非法值返回 NULL。';

CREATE OR REPLACE FUNCTION etl.try_numeric(p_text text)
RETURNS numeric LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
    IF p_text IS NULL OR btrim(p_text) = '' THEN RETURN NULL; END IF;
    RETURN p_text::numeric;
EXCEPTION WHEN others THEN
    RETURN NULL;
END $$;
ALTER FUNCTION etl.try_numeric(text) OWNER TO quant_owner;
COMMENT ON FUNCTION etl.try_numeric(text) IS '文本安全转 numeric；非法值返回 NULL。';

CREATE OR REPLACE FUNCTION etl.try_bigint(p_text text)
RETURNS bigint LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
    IF p_text IS NULL OR btrim(p_text) = '' THEN RETURN NULL; END IF;
    RETURN p_text::bigint;
EXCEPTION WHEN others THEN
    RETURN NULL;
END $$;
ALTER FUNCTION etl.try_bigint(text) OWNER TO quant_owner;
COMMENT ON FUNCTION etl.try_bigint(text) IS '文本安全转 bigint；非法值返回 NULL。';

-- =========================
-- 2. 行情分区表
-- =========================
CREATE TABLE IF NOT EXISTS mkt.daily_bar (
    stock_code   varchar(12)   NOT NULL REFERENCES ref.security(stock_code),
    trade_date   date          NOT NULL,
    open         numeric(12,4),
    high         numeric(12,4),
    low          numeric(12,4),
    close        numeric(12,4) NOT NULL,
    pre_close    numeric(12,4),
    volume       bigint        NOT NULL DEFAULT 0 CHECK (volume >= 0),
    amount       numeric(20,2) NOT NULL DEFAULT 0 CHECK (amount >= 0),
    is_suspended boolean       NOT NULL DEFAULT false,
    PRIMARY KEY (stock_code, trade_date),
    CHECK (close > 0),
    CHECK (open IS NULL OR open > 0),
    CHECK (high IS NULL OR high > 0),
    CHECK (low IS NULL OR low > 0),
    CHECK (pre_close IS NULL OR pre_close > 0),
    CHECK (high >= low AND high >= GREATEST(open, close) AND low <= LEAST(open, close))
) PARTITION BY RANGE (trade_date);
ALTER TABLE mkt.daily_bar OWNER TO quant_owner;
COMMENT ON TABLE mkt.daily_bar IS '不复权日线行情，按 trade_date 年度范围分区。';
COMMENT ON COLUMN mkt.daily_bar.stock_code IS '证券代码。';
COMMENT ON COLUMN mkt.daily_bar.trade_date IS '交易日期。';
COMMENT ON COLUMN mkt.daily_bar.open IS '开盘价。';
COMMENT ON COLUMN mkt.daily_bar.high IS '最高价。';
COMMENT ON COLUMN mkt.daily_bar.low IS '最低价。';
COMMENT ON COLUMN mkt.daily_bar.close IS '收盘价。';
COMMENT ON COLUMN mkt.daily_bar.pre_close IS '前一交易日收盘价。';
COMMENT ON COLUMN mkt.daily_bar.volume IS '成交量（股）。';
COMMENT ON COLUMN mkt.daily_bar.amount IS '成交额（元）。';
COMMENT ON COLUMN mkt.daily_bar.is_suspended IS '是否停牌。';

DO $$
DECLARE y int;
BEGIN
    FOR y IN 2014..2026 LOOP
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS mkt.daily_bar_%s PARTITION OF mkt.daily_bar FOR VALUES FROM (%L) TO (%L)',
            y, make_date(y, 1, 1), make_date(y + 1, 1, 1)
        );
        EXECUTE format('ALTER TABLE mkt.daily_bar_%s OWNER TO quant_owner', y);
    END LOOP;
END $$;

CREATE INDEX IF NOT EXISTS ix_bar_date_code
    ON mkt.daily_bar (trade_date, stock_code) INCLUDE (close, volume);

-- =========================
-- 3. staging -> mkt 幂等合并
-- =========================
CREATE OR REPLACE FUNCTION etl.fn_merge_daily_bar(p_batch_id bigint)
RETURNS int LANGUAGE plpgsql AS $$
DECLARE
    v_rows_in     int;
    v_rows_valid  int;
    v_rows_upsert int;
BEGIN
    SELECT count(*) INTO v_rows_in
    FROM staging.daily_bar_raw
    WHERE batch_id = p_batch_id;

    IF NOT EXISTS (SELECT 1 FROM etl.load_batch WHERE batch_id = p_batch_id) THEN
        RAISE EXCEPTION 'ETL batch % 不存在', p_batch_id;
    END IF;

    DELETE FROM etl.reject_row WHERE batch_id = p_batch_id;

    WITH parsed AS (
        SELECT r.*,
               count(*) OVER (PARTITION BY r.code, r.date) AS dup_count,
               etl.try_date(r.date) AS p_date,
               etl.try_numeric(r.open) AS p_open,
               etl.try_numeric(r.high) AS p_high,
               etl.try_numeric(r.low) AS p_low,
               etl.try_numeric(r.close) AS p_close,
               etl.try_numeric(r.preclose) AS p_preclose,
               CASE WHEN nullif(btrim(r.volume), '') IS NULL THEN 0 ELSE etl.try_bigint(r.volume) END AS p_volume,
               CASE WHEN nullif(btrim(r.amount), '') IS NULL THEN 0 ELSE etl.try_numeric(r.amount) END AS p_amount
        FROM staging.daily_bar_raw r
        WHERE r.batch_id = p_batch_id
    ), bad AS (
        SELECT p.*,
               concat_ws('; ',
                   CASE WHEN dup_count > 1 THEN '批次内重复 code/date' END,
                   CASE WHEN p_date IS NULL THEN '非法日期' END,
                   CASE WHEN p_date IS NOT NULL AND (p_date < date '2014-01-01' OR p_date >= date '2027-01-01') THEN '日期超出当前分区范围 2014-2026' END,
                   CASE WHEN code !~ '^(sh|sz|bj)\.[0-9]{6}$' THEN '非法证券代码' END,
                   CASE WHEN NOT EXISTS (SELECT 1 FROM ref.security s WHERE s.stock_code = p.code) THEN '证券未进入 ref.security' END,
                   CASE WHEN p_close IS NULL OR p_close <= 0 THEN 'close 非法' END,
                   CASE WHEN p_volume IS NULL OR p_volume < 0 THEN 'volume 非法' END,
                   CASE WHEN p_amount IS NULL OR p_amount < 0 THEN 'amount 非法' END,
                   CASE WHEN adjustflag IS DISTINCT FROM '3' THEN 'adjustflag 必须为 3（不复权）' END,
                   CASE WHEN tradestatus IS NULL OR tradestatus NOT IN ('0','1') THEN 'tradestatus 非法' END,
                   CASE WHEN tradestatus = '1' AND (p_open IS NULL OR p_high IS NULL OR p_low IS NULL) THEN '正常交易日 OHLC 不完整' END,
                   CASE WHEN p_low IS NOT NULL AND p_low <= 0 THEN 'low <= 0' END,
                   CASE WHEN p_high IS NOT NULL AND p_low IS NOT NULL AND p_high < p_low THEN 'high < low' END,
                   CASE WHEN p_high IS NOT NULL AND p_open IS NOT NULL AND p_high < p_open THEN 'high < open' END,
                   CASE WHEN p_high IS NOT NULL AND p_close IS NOT NULL AND p_high < p_close THEN 'high < close' END,
                   CASE WHEN p_low IS NOT NULL AND p_open IS NOT NULL AND p_low > p_open THEN 'low > open' END,
                   CASE WHEN p_low IS NOT NULL AND p_close IS NOT NULL AND p_low > p_close THEN 'low > close' END
               ) AS why
        FROM parsed p
    ), rejected AS (
        INSERT INTO etl.reject_row(batch_id, row_key, reason, raw_row)
        SELECT p_batch_id,
               coalesce(code,'?') || '/' || coalesce(date,'?'),
               why,
               to_jsonb(bad) - ARRAY['dup_count','p_date','p_open','p_high','p_low','p_close','p_preclose','p_volume','p_amount','why']::text[]
        FROM bad
        WHERE why <> ''
        RETURNING 1
    )
    SELECT v_rows_in - count(*) INTO v_rows_valid FROM rejected;

    WITH parsed AS (
        SELECT r.code,
               etl.try_date(r.date) AS trade_date,
               etl.try_numeric(r.open) AS open,
               etl.try_numeric(r.high) AS high,
               etl.try_numeric(r.low) AS low,
               etl.try_numeric(r.close) AS close,
               etl.try_numeric(r.preclose) AS pre_close,
               CASE WHEN nullif(btrim(r.volume), '') IS NULL THEN 0 ELSE etl.try_bigint(r.volume) END AS volume,
               CASE WHEN nullif(btrim(r.amount), '') IS NULL THEN 0 ELSE etl.try_numeric(r.amount) END AS amount,
               r.tradestatus
        FROM staging.daily_bar_raw r
        WHERE r.batch_id = p_batch_id
          AND NOT EXISTS (
              SELECT 1 FROM etl.reject_row x
              WHERE x.batch_id = p_batch_id
                AND x.row_key = coalesce(r.code,'?') || '/' || coalesce(r.date,'?')
          )
    ), upserted AS (
        INSERT INTO mkt.daily_bar AS t
            (stock_code, trade_date, open, high, low, close, pre_close, volume, amount, is_suspended)
        SELECT code, trade_date, open, high, low, close, pre_close, volume, amount, tradestatus = '0'
        FROM parsed
        ON CONFLICT (stock_code, trade_date) DO UPDATE
        SET open         = EXCLUDED.open,
            high         = EXCLUDED.high,
            low          = EXCLUDED.low,
            close        = EXCLUDED.close,
            pre_close    = EXCLUDED.pre_close,
            volume       = EXCLUDED.volume,
            amount       = EXCLUDED.amount,
            is_suspended = EXCLUDED.is_suspended
        WHERE (t.open, t.high, t.low, t.close, t.pre_close, t.volume, t.amount, t.is_suspended)
              IS DISTINCT FROM
              (EXCLUDED.open, EXCLUDED.high, EXCLUDED.low, EXCLUDED.close,
               EXCLUDED.pre_close, EXCLUDED.volume, EXCLUDED.amount, EXCLUDED.is_suspended)
        RETURNING 1
    )
    SELECT count(*) INTO v_rows_upsert FROM upserted;

    UPDATE etl.load_batch
       SET finished_at = clock_timestamp(),
           rows_in     = v_rows_in,
           rows_upsert = v_rows_upsert,
           rows_reject = v_rows_in - v_rows_valid,
           status      = 'OK'
     WHERE batch_id = p_batch_id;

    RETURN v_rows_upsert;
END $$;
ALTER FUNCTION etl.fn_merge_daily_bar(bigint) OWNER TO quant_owner;
COMMENT ON FUNCTION etl.fn_merge_daily_bar(bigint) IS '将指定批次的 staging 日线校验后幂等合并到 mkt.daily_bar；返回实际变化行数。';

-- =========================
-- 4. 复权因子与后复权视图
-- =========================
CREATE TABLE IF NOT EXISTS mkt.adj_factor (
    stock_code  varchar(12)  NOT NULL REFERENCES ref.security(stock_code),
    ex_date     date         NOT NULL,
    back_factor numeric(18,8) NOT NULL CHECK (back_factor > 0),
    valid       daterange    NOT NULL,
    PRIMARY KEY (stock_code, ex_date),
    CHECK (lower(valid) = ex_date),
    EXCLUDE USING gist (stock_code WITH =, valid WITH &&)
);
ALTER TABLE mkt.adj_factor OWNER TO quant_owner;
COMMENT ON TABLE mkt.adj_factor IS '累计后复权因子，以区间表示每个因子的生效区间。';
COMMENT ON COLUMN mkt.adj_factor.stock_code IS '证券代码。';
COMMENT ON COLUMN mkt.adj_factor.ex_date IS '本因子起效的除权除息日；首条为上市日。';
COMMENT ON COLUMN mkt.adj_factor.back_factor IS '累计后复权因子；上市日基准为 1。';
COMMENT ON COLUMN mkt.adj_factor.valid IS '因子有效区间 [ex_date, 下一事件日)。';

CREATE OR REPLACE FUNCTION mkt.trg_adj_factor_valid()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE mkt.adj_factor
       SET valid = daterange(lower(valid), NEW.ex_date, '[)')
     WHERE stock_code = NEW.stock_code
       AND ex_date < NEW.ex_date
       AND valid @> NEW.ex_date;

    NEW.valid := daterange(
        NEW.ex_date,
        (SELECT MIN(ex_date)
           FROM mkt.adj_factor
          WHERE stock_code = NEW.stock_code
            AND ex_date > NEW.ex_date),
        '[)'
    );
    RETURN NEW;
END $$;
ALTER FUNCTION mkt.trg_adj_factor_valid() OWNER TO quant_owner;
COMMENT ON FUNCTION mkt.trg_adj_factor_valid() IS '插入复权因子时自动截断上一段并连接下一段，维持无重叠有效区间。';

DROP TRIGGER IF EXISTS trg_adj_factor_valid ON mkt.adj_factor;
CREATE TRIGGER trg_adj_factor_valid
BEFORE INSERT ON mkt.adj_factor
FOR EACH ROW EXECUTE FUNCTION mkt.trg_adj_factor_valid();

CREATE OR REPLACE FUNCTION etl.fn_merge_adj_factor(p_batch_id bigint)
RETURNS int LANGUAGE plpgsql AS $$
DECLARE
    v_rows_in     int;
    v_rows_reject int;
    v_base_change int := 0;
    v_event_change int := 0;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM etl.load_batch WHERE batch_id = p_batch_id) THEN
        RAISE EXCEPTION 'ETL batch % 不存在', p_batch_id;
    END IF;

    SELECT count(*) INTO v_rows_in
    FROM staging.adjust_factor_raw
    WHERE batch_id = p_batch_id;

    DELETE FROM etl.reject_row WHERE batch_id = p_batch_id;

    WITH parsed AS (
        SELECT r.*,
               count(*) OVER (PARTITION BY r.code, r."dividOperateDate") AS dup_count,
               etl.try_date(r."dividOperateDate") AS p_date,
               etl.try_numeric(r."backAdjustFactor") AS p_factor
        FROM staging.adjust_factor_raw r
        WHERE r.batch_id = p_batch_id
    ), rejected AS (
        INSERT INTO etl.reject_row(batch_id, row_key, reason, raw_row)
        SELECT p_batch_id,
               coalesce(code,'?') || '/' || coalesce("dividOperateDate",'?'),
               concat_ws('; ',
                   CASE WHEN dup_count > 1 THEN '批次内重复 code/dividOperateDate' END,
                   CASE WHEN p_date IS NULL THEN '非法除权除息日' END,
                   CASE WHEN NOT EXISTS (SELECT 1 FROM ref.security s WHERE s.stock_code = p.code) THEN '证券未进入 ref.security' END,
                   CASE WHEN p_factor IS NULL OR p_factor <= 0 THEN 'backAdjustFactor 非法' END,
                   CASE WHEN EXISTS (
                       SELECT 1 FROM ref.security s
                       WHERE s.stock_code = p.code AND p_date < s.list_date
                   ) THEN '除权除息日早于上市日' END
               ),
               to_jsonb(p) - ARRAY['dup_count','p_date','p_factor']::text[]
        FROM parsed p
        WHERE dup_count > 1
           OR p_date IS NULL
           OR p_factor IS NULL OR p_factor <= 0
           OR NOT EXISTS (SELECT 1 FROM ref.security s WHERE s.stock_code = p.code)
           OR EXISTS (SELECT 1 FROM ref.security s WHERE s.stock_code = p.code AND p_date < s.list_date)
        RETURNING 1
    )
    SELECT count(*) INTO v_rows_reject FROM rejected;

    -- 每只有行情的证券在上市日补 back_factor=1，保证任一交易日都能连接因子。
    WITH ins AS (
        INSERT INTO mkt.adj_factor(stock_code, ex_date, back_factor, valid)
        SELECT s.stock_code, s.list_date, 1::numeric, daterange(s.list_date, NULL, '[)')
        FROM ref.security s
        WHERE EXISTS (SELECT 1 FROM mkt.daily_bar b WHERE b.stock_code = s.stock_code)
        ON CONFLICT (stock_code, ex_date) DO NOTHING
        RETURNING 1
    )
    SELECT count(*) INTO v_base_change FROM ins;

    WITH parsed AS (
        SELECT r.code,
               etl.try_date(r."dividOperateDate") AS ex_date,
               etl.try_numeric(r."backAdjustFactor") AS back_factor
        FROM staging.adjust_factor_raw r
        WHERE r.batch_id = p_batch_id
          AND NOT EXISTS (
              SELECT 1 FROM etl.reject_row x
              WHERE x.batch_id = p_batch_id
                AND x.row_key = coalesce(r.code,'?') || '/' || coalesce(r."dividOperateDate",'?')
          )
    ), upserted AS (
        INSERT INTO mkt.adj_factor AS t(stock_code, ex_date, back_factor, valid)
        SELECT code, ex_date, back_factor, daterange(ex_date, NULL, '[)')
        FROM parsed
        ON CONFLICT (stock_code, ex_date) DO UPDATE
        SET back_factor = EXCLUDED.back_factor
        WHERE t.back_factor IS DISTINCT FROM EXCLUDED.back_factor
        RETURNING 1
    )
    SELECT count(*) INTO v_event_change FROM upserted;

    UPDATE etl.load_batch
       SET finished_at = clock_timestamp(),
           rows_in     = v_rows_in,
           rows_upsert = v_base_change + v_event_change,
           rows_reject = v_rows_reject,
           status      = 'OK'
     WHERE batch_id = p_batch_id;

    RETURN v_base_change + v_event_change;
END $$;
ALTER FUNCTION etl.fn_merge_adj_factor(bigint) OWNER TO quant_owner;
COMMENT ON FUNCTION etl.fn_merge_adj_factor(bigint) IS '导入 BaoStock 后复权因子，并为有行情证券补上市日基准因子 1；幂等执行。';

CREATE OR REPLACE VIEW mkt.v_bar_hfq AS
SELECT b.stock_code,
       b.trade_date,
       b.open  * a.back_factor AS open,
       b.high  * a.back_factor AS high,
       b.low   * a.back_factor AS low,
       b.close * a.back_factor AS close,
       b.volume,
       b.amount,
       b.is_suspended,
       a.back_factor
FROM mkt.daily_bar b
JOIN mkt.adj_factor a
  ON a.stock_code = b.stock_code
 AND a.valid @> b.trade_date;
ALTER VIEW mkt.v_bar_hfq OWNER TO quant_owner;
COMMENT ON VIEW mkt.v_bar_hfq IS '后复权日线视图：原始 OHLC × 当日累计后复权因子。';

-- =========================
-- 5. EMA 自定义聚合与指标物化视图
-- =========================
DROP MATERIALIZED VIEW IF EXISTS mkt.mv_indicator_daily CASCADE;

CREATE OR REPLACE FUNCTION mkt.ema_sfunc(state numeric, val numeric, alpha numeric)
RETURNS numeric LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE
             WHEN val IS NULL THEN state
             WHEN state IS NULL THEN val
             ELSE alpha * val + (1 - alpha) * state
           END
$$;
ALTER FUNCTION mkt.ema_sfunc(numeric, numeric, numeric) OWNER TO quant_owner;
COMMENT ON FUNCTION mkt.ema_sfunc(numeric, numeric, numeric) IS 'EMA 聚合状态转移：state <- alpha*val + (1-alpha)*state。';

DROP AGGREGATE IF EXISTS mkt.ema(numeric, numeric);
CREATE AGGREGATE mkt.ema(numeric, numeric) (
    SFUNC = mkt.ema_sfunc,
    STYPE = numeric,
    PARALLEL = SAFE
);
ALTER AGGREGATE mkt.ema(numeric, numeric) OWNER TO quant_owner;
COMMENT ON AGGREGATE mkt.ema(numeric, numeric) IS 'EMA 自定义聚合；第二个参数为 alpha，可作为窗口聚合使用。';

CREATE MATERIALIZED VIEW mkt.mv_indicator_daily AS
WITH r AS (
    SELECT stock_code,
           trade_date,
           close,
           amount,
           is_suspended,
           LN(close / LAG(close) OVER (PARTITION BY stock_code ORDER BY trade_date)) AS log_ret
    FROM mkt.v_bar_hfq
)
SELECT stock_code,
       trade_date,
       is_suspended,
       close AS close_hfq,
       CASE WHEN COUNT(*) OVER w20 = 20 THEN AVG(close) OVER w20 END AS ma20,
       CASE WHEN COUNT(*) OVER w60 = 60 THEN AVG(close) OVER w60 END AS ma60,
       mkt.ema(close, 2.0::numeric / 13) OVER w_all AS ema12,
       mkt.ema(close, 2.0::numeric / 27) OVER w_all AS ema26,
       close / LAG(close, 20) OVER w_all - 1 AS ret_20d,
       close / LAG(close, 60) OVER w_all - 1 AS ret_60d,
       CASE WHEN COUNT(log_ret) OVER w20 = 20
            THEN STDDEV_SAMP(log_ret) OVER w20 * SQRT(252::numeric)
       END AS vol_20d,
       CASE WHEN COUNT(*) OVER w20 = 20 THEN AVG(amount) OVER w20 END AS amount_20d
FROM r
WINDOW
    w_all AS (PARTITION BY stock_code ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW),
    w20   AS (PARTITION BY stock_code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),
    w60   AS (PARTITION BY stock_code ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)
WITH NO DATA;
ALTER MATERIALIZED VIEW mkt.mv_indicator_daily OWNER TO quant_owner;
COMMENT ON MATERIALIZED VIEW mkt.mv_indicator_daily IS '后复权行情上的日频指标：MA/EMA/收益/波动率/成交额均值。';
COMMENT ON COLUMN mkt.mv_indicator_daily.stock_code IS '证券代码。';
COMMENT ON COLUMN mkt.mv_indicator_daily.trade_date IS '交易日期。';
COMMENT ON COLUMN mkt.mv_indicator_daily.is_suspended IS '当日是否停牌。';
COMMENT ON COLUMN mkt.mv_indicator_daily.close_hfq IS '后复权收盘价。';
COMMENT ON COLUMN mkt.mv_indicator_daily.ma20 IS '20 交易日简单移动平均，样本不足时为空。';
COMMENT ON COLUMN mkt.mv_indicator_daily.ma60 IS '60 交易日简单移动平均，样本不足时为空。';
COMMENT ON COLUMN mkt.mv_indicator_daily.ema12 IS 'EMA12，alpha=2/(12+1)。';
COMMENT ON COLUMN mkt.mv_indicator_daily.ema26 IS 'EMA26，alpha=2/(26+1)。';
COMMENT ON COLUMN mkt.mv_indicator_daily.ret_20d IS '相对 20 个交易日前的收益率。';
COMMENT ON COLUMN mkt.mv_indicator_daily.ret_60d IS '相对 60 个交易日前的收益率。';
COMMENT ON COLUMN mkt.mv_indicator_daily.vol_20d IS '最近 20 个日对数收益的样本标准差年化（sqrt(252)）。';
COMMENT ON COLUMN mkt.mv_indicator_daily.amount_20d IS '最近 20 个交易日成交额均值。';

CREATE UNIQUE INDEX ux_indicator_code_date
    ON mkt.mv_indicator_daily(stock_code, trade_date);
CREATE INDEX ix_indicator_date
    ON mkt.mv_indicator_daily(trade_date);

-- =========================
-- 6. 数据质量巡检（应为空集）
-- =========================
CREATE OR REPLACE VIEW etl.dq_missing_bar AS
SELECT s.stock_code, c.cal_date
FROM ref.security s
JOIN ref.trading_calendar c
  ON c.is_open
 AND c.cal_date >= s.list_date
 AND (s.delist_date IS NULL OR c.cal_date < s.delist_date)
 AND c.cal_date <= COALESCE((SELECT MAX(trade_date) FROM mkt.daily_bar), c.cal_date)
WHERE NOT EXISTS (
    SELECT 1 FROM mkt.daily_bar b
    WHERE b.stock_code = s.stock_code
      AND b.trade_date = c.cal_date
);
ALTER VIEW etl.dq_missing_bar OWNER TO quant_owner;
COMMENT ON VIEW etl.dq_missing_bar IS '质量巡检：上市期间交易日缺少行情的证券/日期。';

CREATE OR REPLACE VIEW etl.dq_bar_on_closed_day AS
SELECT b.stock_code, b.trade_date
FROM mkt.daily_bar b
JOIN ref.trading_calendar c ON c.cal_date = b.trade_date
WHERE NOT c.is_open;
ALTER VIEW etl.dq_bar_on_closed_day OWNER TO quant_owner;
COMMENT ON VIEW etl.dq_bar_on_closed_day IS '质量巡检：行情落在非交易日。';

CREATE OR REPLACE VIEW etl.dq_bar_without_factor AS
SELECT b.stock_code, b.trade_date
FROM mkt.daily_bar b
WHERE NOT EXISTS (
    SELECT 1 FROM mkt.adj_factor a
    WHERE a.stock_code = b.stock_code
      AND a.valid @> b.trade_date
);
ALTER VIEW etl.dq_bar_without_factor OWNER TO quant_owner;
COMMENT ON VIEW etl.dq_bar_without_factor IS '质量巡检：日线找不到当日复权因子。';
