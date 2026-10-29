-- D：因子定义、版本、运行与结果。可重复执行，不删除已有行情或交易数据。

CREATE SCHEMA IF NOT EXISTS factor AUTHORIZATION quant_owner;
ALTER SCHEMA factor OWNER TO quant_owner;
REVOKE ALL ON SCHEMA factor FROM PUBLIC;

CREATE OR REPLACE FUNCTION factor.app_user_id() RETURNS bigint
LANGUAGE sql STABLE SET search_path = pg_catalog AS $$
    SELECT nullif(current_setting('app.user_id', true), '')::bigint
$$;
ALTER FUNCTION factor.app_user_id() OWNER TO quant_owner;
COMMENT ON FUNCTION factor.app_user_id() IS '当前事务里的应用用户编号；这不是登录证明，只供已验证的服务使用。';

CREATE TABLE IF NOT EXISTS factor.definition (
    factor_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    visibility  varchar(16) NOT NULL CHECK (visibility IN ('SYSTEM_PUBLIC', 'USER_PRIVATE')),
    owner_id    bigint REFERENCES core.app_user(user_id),
    code        varchar(64),
    name        varchar(128) NOT NULL CHECK (btrim(name) <> ''),
    archived    boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CHECK (
        (visibility = 'SYSTEM_PUBLIC' AND owner_id IS NULL AND code IS NOT NULL)
        OR (visibility = 'USER_PRIVATE' AND owner_id IS NOT NULL AND code IS NULL)
    )
);
ALTER TABLE factor.definition OWNER TO quant_owner;
CREATE UNIQUE INDEX IF NOT EXISTS ux_factor_public_code
    ON factor.definition(code) WHERE visibility = 'SYSTEM_PUBLIC';
CREATE UNIQUE INDEX IF NOT EXISTS ux_factor_private_name
    ON factor.definition(owner_id, name) WHERE visibility = 'USER_PRIVATE';

CREATE TABLE IF NOT EXISTS factor.version (
    factor_id        bigint NOT NULL REFERENCES factor.definition(factor_id),
    version_no       integer NOT NULL CHECK (version_no >= 1),
    expression       text NOT NULL CHECK (char_length(expression) BETWEEN 1 AND 1024),
    normalized_tree  jsonb NOT NULL CHECK (jsonb_typeof(normalized_tree) = 'object'),
    syntax_version   varchar(16) NOT NULL,
    content_digest   text NOT NULL CHECK (content_digest ~ '^[0-9a-f]{64}$'),
    unit_label       text NOT NULL,
    unit_exponents   jsonb NOT NULL,
    price_basis      varchar(16) NOT NULL CHECK (price_basis IN ('RAW', 'HFQ', 'SHARE', 'NONE')),
    lookback         integer NOT NULL CHECK (lookback BETWEEN 1 AND 252),
    fields           text[] NOT NULL,
    explanation      text NOT NULL,
    ca_sensitive     boolean NOT NULL,
    source_factor_id bigint,
    source_version   integer,
    source_doc       text,
    source_name      text,
    source_page      text,
    adaptation_note  text,
    source_digest    text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (factor_id, version_no),
    FOREIGN KEY (source_factor_id, source_version)
        REFERENCES factor.version(factor_id, version_no)
);
ALTER TABLE factor.version OWNER TO quant_owner;

CREATE TABLE IF NOT EXISTS factor.run (
    run_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id        bigint NOT NULL REFERENCES core.app_user(user_id),
    factor_id       bigint NOT NULL,
    version_no      integer NOT NULL,
    as_of           date NOT NULL,
    universe        varchar(32) NOT NULL,
    engine_version  text NOT NULL,
    input_digest    text NOT NULL,
    status          varchar(16) NOT NULL CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED')),
    row_count       integer CHECK (row_count IS NULL OR row_count >= 0),
    error_class     text,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    FOREIGN KEY (factor_id, version_no) REFERENCES factor.version(factor_id, version_no),
    CHECK (
        (status = 'RUNNING' AND finished_at IS NULL AND error_class IS NULL)
        OR (status = 'SUCCEEDED' AND finished_at IS NOT NULL AND error_class IS NULL)
        OR (status = 'FAILED' AND finished_at IS NOT NULL)
    )
);
ALTER TABLE factor.run OWNER TO quant_owner;
CREATE INDEX IF NOT EXISTS ix_factor_run_owner ON factor.run(owner_id, run_id DESC);

CREATE TABLE IF NOT EXISTS factor.value (
    run_id        bigint NOT NULL REFERENCES factor.run(run_id),
    stock_code    varchar(12) NOT NULL CHECK (stock_code ~ '^(sh|sz|bj)\.[0-9]{6}$'),
    trade_date    date NOT NULL,
    value         numeric,
    quality       varchar(8) NOT NULL CHECK (quality IN ('OK', 'MISSING')),
    reason_codes  text[] NOT NULL DEFAULT ARRAY[]::text[],
    reason_detail text,
    PRIMARY KEY (run_id, stock_code, trade_date),
    CHECK (
        (quality = 'OK' AND value IS NOT NULL AND value::text <> 'NaN' AND cardinality(reason_codes) = 0)
        OR (quality = 'MISSING' AND value IS NULL AND cardinality(reason_codes) >= 1)
    )
);
ALTER TABLE factor.value OWNER TO quant_owner;
CREATE INDEX IF NOT EXISTS ix_factor_value_run ON factor.value(run_id, stock_code);

COMMENT ON TABLE factor.definition IS '因子定义。公共公式没有个人所有者；私有公式只属于创建它的用户。';
COMMENT ON COLUMN factor.definition.factor_id IS '因子定义主键。页面用它选择公式，不用名称猜测。';
COMMENT ON COLUMN factor.definition.visibility IS 'SYSTEM_PUBLIC 公共目录，或 USER_PRIVATE 个人公式。';
COMMENT ON COLUMN factor.definition.owner_id IS '私有公式的所有者；公共公式必须为空。';
COMMENT ON COLUMN factor.definition.code IS '公共公式代码，全局唯一；私有公式为空。';
COMMENT ON COLUMN factor.definition.name IS '显示名称。同一用户的私有名称不能重复，但可以和公共名称相同。';
COMMENT ON COLUMN factor.definition.archived IS '归档后不再作为新运行的默认选择，旧版本和旧结果仍保留。';
COMMENT ON COLUMN factor.definition.created_at IS '定义创建时间。';
COMMENT ON TABLE factor.version IS '公式版本。版本一旦写入不能改写，修改公式只能新增版本。';
COMMENT ON COLUMN factor.version.factor_id IS '所属因子定义。';
COMMENT ON COLUMN factor.version.version_no IS '从 1 开始的版本号，同一因子内唯一。';
COMMENT ON COLUMN factor.version.expression IS '原始表达式，最长 1024 字符。';
COMMENT ON COLUMN factor.version.normalized_tree IS '去掉空白后的规范结构，不改变运算顺序和数字精度。';
COMMENT ON COLUMN factor.version.syntax_version IS '语法版本，第一版为 v1。';
COMMENT ON COLUMN factor.version.content_digest IS '语法版本和规范结构的 SHA-256，用来发现内容冲突。';
COMMENT ON COLUMN factor.version.unit_label IS '结果单位的中文说明。';
COMMENT ON COLUMN factor.version.unit_exponents IS '原始价格、调整后价格和股数的单位指数。';
COMMENT ON COLUMN factor.version.price_basis IS '结果仍携带的价格口径：RAW、HFQ、SHARE 或 NONE。';
COMMENT ON COLUMN factor.version.lookback IS '含当天在内需要的交易日条数，1 到 252。';
COMMENT ON COLUMN factor.version.fields IS '表达式依赖的输入字段。';
COMMENT ON COLUMN factor.version.explanation IS '按结构生成的中文解释。';
COMMENT ON COLUMN factor.version.ca_sensitive IS '是否需要检查原始价格或股数跨除权事件。';
COMMENT ON COLUMN factor.version.source_factor_id IS '复制来源的因子；复制后不跟随来源变化。';
COMMENT ON COLUMN factor.version.source_version IS '复制来源的版本号。';
COMMENT ON COLUMN factor.version.source_doc IS '来源资料名称。';
COMMENT ON COLUMN factor.version.source_name IS '资料中的原条目名称。';
COMMENT ON COLUMN factor.version.source_page IS '资料页码或位置。';
COMMENT ON COLUMN factor.version.adaptation_note IS '日线改编说明。不表示供应商结果被完整复现。';
COMMENT ON COLUMN factor.version.source_digest IS '来源文件摘要，只用于识别资料版本。';
COMMENT ON COLUMN factor.version.created_at IS '该版本写入时间。';
COMMENT ON TABLE factor.run IS '一次因子运行。只在计算和结果全部写入后标记成功。';
COMMENT ON COLUMN factor.run.run_id IS '运行主键。结果和页面都按它查找。';
COMMENT ON COLUMN factor.run.owner_id IS '实际运行人。使用公共公式时，运行记录仍属于运行人。';
COMMENT ON COLUMN factor.run.factor_id IS '运行所绑定的因子定义。';
COMMENT ON COLUMN factor.run.version_no IS '运行所绑定的版本，不随后来的新版本变化。';
COMMENT ON COLUMN factor.run.as_of IS '研究日。只使用该日日终及之前的资料。';
COMMENT ON COLUMN factor.run.universe IS '研究日的历史股票池代码。';
COMMENT ON COLUMN factor.run.engine_version IS '计算公式的程序版本。';
COMMENT ON COLUMN factor.run.input_digest IS '本次实际读到的相关输入摘要。之后的新行情不会改变它。';
COMMENT ON COLUMN factor.run.status IS 'RUNNING 未完成，SUCCEEDED 已完成，FAILED 已失败。成功运行不会停留在半成品。';
COMMENT ON COLUMN factor.run.row_count IS '写入的结果行数，包含有原因的空值。';
COMMENT ON COLUMN factor.run.error_class IS '失败类别。成功时必须为空。';
COMMENT ON COLUMN factor.run.started_at IS '运行开始时间。';
COMMENT ON COLUMN factor.run.finished_at IS '完成或失败时间。未完成的运行为空。';
COMMENT ON TABLE factor.value IS '某次运行在研究日的逐证券结果。完成之后不能改写。';
COMMENT ON COLUMN factor.value.run_id IS '所属运行。';
COMMENT ON COLUMN factor.value.stock_code IS '证券代码。';
COMMENT ON COLUMN factor.value.trade_date IS '结果对应的交易日，第一版是研究日当天的最近交易日。';
COMMENT ON COLUMN factor.value.value IS '有限数值。缺失时为空，不用零代替。';
COMMENT ON COLUMN factor.value.quality IS 'OK 表示数值可用，MISSING 表示空值且必须有原因。';
COMMENT ON COLUMN factor.value.reason_codes IS '一个或多个原因码，多个原因都保留。';
COMMENT ON COLUMN factor.value.reason_detail IS '原因的中文说明。';

CREATE OR REPLACE FUNCTION factor.trg_definition_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
    v_super boolean;
BEGIN
    SELECT rolsuper INTO v_super FROM pg_roles WHERE rolname = session_user;
    IF NEW.visibility = 'SYSTEM_PUBLIC' AND NOT coalesce(v_super, false) THEN
        RAISE EXCEPTION '普通用户不能发布或改写公共公式' USING ERRCODE = '42501';
    END IF;
    IF NEW.visibility = 'USER_PRIVATE'
       AND NEW.owner_id IS DISTINCT FROM nullif(current_setting('app.user_id', true), '')::bigint THEN
        RAISE EXCEPTION '不能为其他用户创建公式' USING ERRCODE = '42501';
    END IF;
    IF TG_OP = 'UPDATE' AND (OLD.visibility, OLD.owner_id, OLD.code) IS DISTINCT FROM (NEW.visibility, NEW.owner_id, NEW.code) THEN
        RAISE EXCEPTION '不能改变公式的归属或公共代码' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END $$;
ALTER FUNCTION factor.trg_definition_guard() OWNER TO quant_owner;
DROP TRIGGER IF EXISTS definition_guard ON factor.definition;
CREATE TRIGGER definition_guard BEFORE INSERT OR UPDATE ON factor.definition
FOR EACH ROW EXECUTE FUNCTION factor.trg_definition_guard();

CREATE OR REPLACE FUNCTION factor.trg_version_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, factor AS $$
DECLARE
    v_visibility varchar(16);
    v_owner bigint;
    v_super boolean;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION '已保存的公式版本不能修改或删除' USING ERRCODE = '42501';
    END IF;
    SELECT visibility, owner_id INTO v_visibility, v_owner
    FROM factor.definition WHERE factor_id = NEW.factor_id;
    SELECT rolsuper INTO v_super FROM pg_roles WHERE rolname = session_user;
    IF v_visibility = 'SYSTEM_PUBLIC' AND NOT coalesce(v_super, false) THEN
        RAISE EXCEPTION '普通用户不能发布公共公式版本' USING ERRCODE = '42501';
    END IF;
    IF v_visibility = 'USER_PRIVATE'
       AND v_owner IS DISTINCT FROM nullif(current_setting('app.user_id', true), '')::bigint THEN
        RAISE EXCEPTION '不能修改其他用户的公式' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END $$;
ALTER FUNCTION factor.trg_version_guard() OWNER TO quant_owner;
DROP TRIGGER IF EXISTS version_guard ON factor.version;
CREATE TRIGGER version_guard BEFORE INSERT OR UPDATE OR DELETE ON factor.version
FOR EACH ROW EXECUTE FUNCTION factor.trg_version_guard();

CREATE OR REPLACE FUNCTION factor.trg_result_frozen() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION '已保存的运行和结果不能修改或删除' USING ERRCODE = '42501';
END $$;
ALTER FUNCTION factor.trg_result_frozen() OWNER TO quant_owner;
DROP TRIGGER IF EXISTS run_frozen ON factor.run;
CREATE TRIGGER run_frozen BEFORE UPDATE OR DELETE ON factor.run
FOR EACH ROW EXECUTE FUNCTION factor.trg_result_frozen();
DROP TRIGGER IF EXISTS value_frozen ON factor.value;
CREATE TRIGGER value_frozen BEFORE UPDATE OR DELETE ON factor.value
FOR EACH ROW EXECUTE FUNCTION factor.trg_result_frozen();

CREATE OR REPLACE FUNCTION pit.fn_factor_panel(p_universe text, p_lookback integer)
RETURNS TABLE (
    stock_code varchar, trade_date date, day_ord integer,
    open_raw numeric, high_raw numeric, low_raw numeric, close_raw numeric,
    open_hfq numeric, high_hfq numeric, low_hfq numeric, close_hfq numeric,
    volume bigint, amount numeric, is_suspended boolean, has_bar boolean,
    back_factor numeric, ca_in_window boolean
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, pit, ref, mkt AS $$
#variable_conflict use_column
DECLARE
    v_as_of date;
    v_market date;
    v_count integer;
BEGIN
    v_as_of := pit.as_of();
    IF p_universe IS NULL OR p_universe !~ '^[A-Za-z0-9_]{1,32}$' THEN
        RAISE EXCEPTION '股票池不合法';
    END IF;
    IF p_lookback IS NULL OR p_lookback < 1 OR p_lookback > 252 THEN
        RAISE EXCEPTION '历史长度必须是 1 到 252';
    END IF;
    SELECT max(cal_date) INTO v_market FROM ref.trading_calendar
    WHERE is_open AND cal_date <= v_as_of;
    IF v_market IS NULL THEN RAISE EXCEPTION '研究日之前没有交易日'; END IF;
    SELECT count(*) INTO v_count
    FROM pit.index_member m
    JOIN pit.security s ON s.stock_code = m.stock_code
    WHERE m.index_code = p_universe AND m.valid @> v_as_of
      AND (s.delist_date IS NULL OR s.delist_date > v_as_of);
    IF v_count < 1 THEN RAISE EXCEPTION '研究日的股票池没有成员'; END IF;
    IF v_count > 100 THEN RAISE EXCEPTION '一次最多计算 100 只证券'; END IF;
    RETURN QUERY
    WITH cal AS (
        SELECT c.cal_date, row_number() OVER (ORDER BY c.cal_date)::integer AS day_ord
        FROM (
            SELECT cal_date FROM ref.trading_calendar
            WHERE is_open AND cal_date <= v_market
            ORDER BY cal_date DESC
            LIMIT p_lookback
        ) c
    ),
    members AS (
        SELECT s.stock_code
        FROM pit.index_member m
        JOIN pit.security s ON s.stock_code = m.stock_code
        WHERE m.index_code = p_universe AND m.valid @> v_as_of
          AND (s.delist_date IS NULL OR s.delist_date > v_as_of)
    ),
    bounds AS (
        SELECT min(cal.cal_date) AS start_date, max(cal.cal_date) AS end_date FROM cal
    ),
    events AS (
        SELECT DISTINCT event.stock_code
        FROM members
        JOIN mkt.adj_factor event ON event.stock_code = members.stock_code
        JOIN bounds ON event.ex_date > bounds.start_date AND event.ex_date <= bounds.end_date
    ),
    spine AS (
        SELECT members.stock_code, cal.cal_date AS trade_date, cal.day_ord FROM members CROSS JOIN cal
    )
    SELECT sp.stock_code, sp.trade_date, sp.day_ord,
           raw.open, raw.high, raw.low, raw.close,
           hfq.open, hfq.high, hfq.low, hfq.close,
           raw.volume, raw.amount,
           coalesce(raw.is_suspended, false),
           raw.stock_code IS NOT NULL,
           hfq.back_factor,
           ev.stock_code IS NOT NULL
    FROM spine sp
    LEFT JOIN pit.daily_bar_raw raw
      ON raw.stock_code = sp.stock_code AND raw.trade_date = sp.trade_date
    LEFT JOIN pit.daily_bar_hfq hfq
      ON hfq.stock_code = sp.stock_code AND hfq.trade_date = sp.trade_date
    LEFT JOIN events ev ON ev.stock_code = sp.stock_code;
END $$;
ALTER FUNCTION pit.fn_factor_panel(text, integer) OWNER TO quant_owner;
COMMENT ON FUNCTION pit.fn_factor_panel(text, integer) IS '按研究日股票池和交易日历生成稠密行情。缺行保留，不读取研究日之后的数据。';
REVOKE ALL ON FUNCTION pit.fn_factor_panel(text, integer) FROM PUBLIC;

ALTER TABLE factor.definition ENABLE ROW LEVEL SECURITY;
ALTER TABLE factor.version ENABLE ROW LEVEL SECURITY;
ALTER TABLE factor.run ENABLE ROW LEVEL SECURITY;
ALTER TABLE factor.value ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS definition_visible ON factor.definition;
CREATE POLICY definition_visible ON factor.definition FOR SELECT TO app_user
    USING (visibility = 'SYSTEM_PUBLIC' OR owner_id = factor.app_user_id());
DROP POLICY IF EXISTS version_visible ON factor.version;
CREATE POLICY version_visible ON factor.version FOR SELECT TO app_user
    USING (EXISTS (
        SELECT 1 FROM factor.definition d
        WHERE d.factor_id = factor.version.factor_id
          AND (d.visibility = 'SYSTEM_PUBLIC' OR d.owner_id = factor.app_user_id())
    ));
DROP POLICY IF EXISTS run_own ON factor.run;
CREATE POLICY run_own ON factor.run FOR SELECT TO app_user
    USING (owner_id = factor.app_user_id());
DROP POLICY IF EXISTS value_own ON factor.value;
CREATE POLICY value_own ON factor.value FOR SELECT TO app_user
    USING (EXISTS (
        SELECT 1 FROM factor.run r WHERE r.run_id = factor.value.run_id AND r.owner_id = factor.app_user_id()
    ));
