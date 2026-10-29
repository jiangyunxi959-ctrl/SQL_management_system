-- D：因子维护函数、受控调用记录和最小授权。

CREATE TABLE IF NOT EXISTS agent.session (
    agent_session_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id          bigint NOT NULL REFERENCES core.app_user(user_id),
    auth_session_id  bigint REFERENCES core.auth_session(session_id),
    as_of            date NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    closed_at        timestamptz
);
ALTER TABLE agent.session OWNER TO quant_owner;
CREATE TABLE IF NOT EXISTS agent.tool_call (
    tool_call_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    agent_session_id bigint NOT NULL REFERENCES agent.session(agent_session_id),
    request_id       uuid NOT NULL,
    tool_name        text NOT NULL,
    args_digest      text NOT NULL,
    object_ref       text,
    created_at       timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE agent.tool_call OWNER TO quant_owner;
COMMENT ON TABLE agent.session IS '受控工具会话，绑定已登录用户和研究日。';
COMMENT ON COLUMN agent.session.agent_session_id IS '工具会话主键。';
COMMENT ON COLUMN agent.session.user_id IS '发起会话的真实用户。';
COMMENT ON COLUMN agent.session.auth_session_id IS '对应的登录会话。';
COMMENT ON COLUMN agent.session.as_of IS '会话固定的研究日。';
COMMENT ON COLUMN agent.session.created_at IS '会话创建时间。';
COMMENT ON COLUMN agent.session.closed_at IS '会话关闭时间。';
COMMENT ON TABLE agent.tool_call IS '一次受控工具调用，不保存任意 SQL。';
COMMENT ON COLUMN agent.tool_call.tool_call_id IS '调用主键。';
COMMENT ON COLUMN agent.tool_call.agent_session_id IS '所属工具会话。';
COMMENT ON COLUMN agent.tool_call.request_id IS '关联的请求编号。';
COMMENT ON COLUMN agent.tool_call.tool_name IS '允许清单中的工具名。';
COMMENT ON COLUMN agent.tool_call.args_digest IS '参数摘要，不保存会话凭证。';
COMMENT ON COLUMN agent.tool_call.object_ref IS '调用产生或读取的对象编号。';
COMMENT ON COLUMN agent.tool_call.created_at IS '调用时间。';

CREATE OR REPLACE FUNCTION factor.fn_create_private(
    p_name text, p_expression text, p_tree jsonb, p_digest text, p_syntax text,
    p_unit text, p_exponents jsonb, p_basis text, p_lookback integer,
    p_fields text[], p_explanation text, p_ca boolean
) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, factor, audit AS $$
DECLARE
    v_user bigint := factor.app_user_id();
    v_id bigint;
BEGIN
    IF v_user IS NULL THEN RAISE EXCEPTION '请先登录' USING ERRCODE = '42501'; END IF;
    IF p_name IS NULL OR char_length(btrim(p_name)) NOT BETWEEN 1 AND 128 THEN
        RAISE EXCEPTION '公式名称不合法';
    END IF;
    IF p_digest !~ '^[0-9a-f]{64}$' OR p_lookback NOT BETWEEN 1 AND 252 THEN
        RAISE EXCEPTION '公式摘要或历史长度不合法';
    END IF;
    INSERT INTO factor.definition(visibility, owner_id, name)
    VALUES ('USER_PRIVATE', v_user, btrim(p_name))
    RETURNING factor_id INTO v_id;
    INSERT INTO factor.version(
        factor_id, version_no, expression, normalized_tree, syntax_version, content_digest,
        unit_label, unit_exponents, price_basis, lookback, fields, explanation, ca_sensitive
    ) VALUES (
        v_id, 1, p_expression, p_tree, p_syntax, p_digest, p_unit, p_exponents, p_basis,
        p_lookback, p_fields, p_explanation, p_ca
    );
    PERFORM audit.fn_business('FACTOR_VERSION', 'factor.version', v_id::text || ':1',
        jsonb_build_object('name', btrim(p_name)));
    RETURN v_id;
END $$;
ALTER FUNCTION factor.fn_create_private(text, text, jsonb, text, text, text, jsonb, text, integer, text[], text, boolean) OWNER TO quant_owner;

CREATE OR REPLACE FUNCTION factor.fn_new_version(
    p_factor_id bigint, p_expression text, p_tree jsonb, p_digest text, p_syntax text,
    p_unit text, p_exponents jsonb, p_basis text, p_lookback integer,
    p_fields text[], p_explanation text, p_ca boolean
) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, factor, audit AS $$
DECLARE
    v_user bigint := factor.app_user_id();
    v_no integer;
BEGIN
    IF v_user IS NULL THEN RAISE EXCEPTION '请先登录' USING ERRCODE = '42501'; END IF;
    IF NOT EXISTS (
        SELECT 1 FROM factor.definition
        WHERE factor_id = p_factor_id AND visibility = 'USER_PRIVATE' AND owner_id = v_user
    ) THEN
        RAISE EXCEPTION '公式不存在或不属于当前用户' USING ERRCODE = '42501';
    END IF;
    PERFORM pg_advisory_xact_lock(p_factor_id);
    SELECT coalesce(max(version_no), 0) + 1 INTO v_no
    FROM factor.version WHERE factor_id = p_factor_id;
    INSERT INTO factor.version(
        factor_id, version_no, expression, normalized_tree, syntax_version, content_digest,
        unit_label, unit_exponents, price_basis, lookback, fields, explanation, ca_sensitive
    ) VALUES (
        p_factor_id, v_no, p_expression, p_tree, p_syntax, p_digest, p_unit, p_exponents,
        p_basis, p_lookback, p_fields, p_explanation, p_ca
    );
    PERFORM audit.fn_business('FACTOR_VERSION', 'factor.version', p_factor_id::text || ':' || v_no::text,
        jsonb_build_object('version', v_no));
    RETURN v_no;
END $$;
ALTER FUNCTION factor.fn_new_version(bigint, text, jsonb, text, text, text, jsonb, text, integer, text[], text, boolean) OWNER TO quant_owner;

CREATE OR REPLACE FUNCTION factor.fn_copy_public(p_factor_id bigint, p_version integer, p_name text)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, factor, audit AS $$
DECLARE
    v_user bigint := factor.app_user_id();
    v_id bigint;
    v_row factor.version;
BEGIN
    IF v_user IS NULL THEN RAISE EXCEPTION '请先登录' USING ERRCODE = '42501'; END IF;
    IF NOT EXISTS (
        SELECT 1 FROM factor.definition
        WHERE factor_id = p_factor_id AND visibility = 'SYSTEM_PUBLIC'
    ) THEN
        RAISE EXCEPTION '只能复制公共公式' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_row FROM factor.version
    WHERE factor_id = p_factor_id AND version_no = p_version;
    IF NOT FOUND THEN RAISE EXCEPTION '公共版本不存在'; END IF;
    INSERT INTO factor.definition(visibility, owner_id, name)
    VALUES ('USER_PRIVATE', v_user, btrim(p_name))
    RETURNING factor_id INTO v_id;
    INSERT INTO factor.version(
        factor_id, version_no, expression, normalized_tree, syntax_version, content_digest,
        unit_label, unit_exponents, price_basis, lookback, fields, explanation, ca_sensitive,
        source_factor_id, source_version, source_doc, source_name, source_page, adaptation_note, source_digest
    ) VALUES (
        v_id, 1, v_row.expression, v_row.normalized_tree, v_row.syntax_version, v_row.content_digest,
        v_row.unit_label, v_row.unit_exponents, v_row.price_basis, v_row.lookback, v_row.fields,
        v_row.explanation, v_row.ca_sensitive, p_factor_id, p_version, v_row.source_doc, v_row.source_name,
        v_row.source_page, v_row.adaptation_note, v_row.source_digest
    );
    PERFORM audit.fn_business('FACTOR_COPY', 'factor.version', v_id::text || ':1',
        jsonb_build_object('source', p_factor_id, 'source_version', p_version));
    RETURN v_id;
END $$;
ALTER FUNCTION factor.fn_copy_public(bigint, integer, text) OWNER TO quant_owner;

CREATE OR REPLACE FUNCTION factor.fn_archive(p_factor_id bigint) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, factor, audit AS $$
DECLARE
    v_user bigint := factor.app_user_id();
BEGIN
    IF v_user IS NULL THEN RAISE EXCEPTION '请先登录' USING ERRCODE = '42501'; END IF;
    UPDATE factor.definition SET archived = true
    WHERE factor_id = p_factor_id AND visibility = 'USER_PRIVATE' AND owner_id = v_user;
    IF NOT FOUND THEN RAISE EXCEPTION '公式不存在或不属于当前用户' USING ERRCODE = '42501'; END IF;
    PERFORM audit.fn_business('FACTOR_ARCHIVE', 'factor.definition', p_factor_id::text, '{}'::jsonb);
END $$;
ALTER FUNCTION factor.fn_archive(bigint) OWNER TO quant_owner;

CREATE OR REPLACE FUNCTION factor.fn_save_run(
    p_factor_id bigint, p_version integer, p_as_of date, p_universe text,
    p_engine text, p_digest text, p_rows jsonb
) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, factor, audit, pit AS $$
DECLARE
    v_user bigint := factor.app_user_id();
    v_run bigint;
    v_count integer;
BEGIN
    IF v_user IS NULL THEN RAISE EXCEPTION '请先登录' USING ERRCODE = '42501'; END IF;
    IF p_as_of IS DISTINCT FROM pit.as_of() THEN
        RAISE EXCEPTION '运行日期必须等于当前研究日';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM factor.definition d
        JOIN factor.version v ON v.factor_id = d.factor_id
        WHERE d.factor_id = p_factor_id AND v.version_no = p_version
          AND (d.visibility = 'SYSTEM_PUBLIC' OR d.owner_id = v_user)
    ) THEN
        RAISE EXCEPTION '公式版本不存在或不可用' USING ERRCODE = '42501';
    END IF;
    IF jsonb_typeof(p_rows) <> 'array' OR jsonb_array_length(p_rows) > 100 THEN
        RAISE EXCEPTION '结果条数不合法';
    END IF;
    INSERT INTO factor.run(
        owner_id, factor_id, version_no, as_of, universe, engine_version, input_digest,
        status, row_count, finished_at
    ) VALUES (
        v_user, p_factor_id, p_version, p_as_of, p_universe, p_engine, p_digest,
        'SUCCEEDED', jsonb_array_length(p_rows), now()
    ) RETURNING run_id INTO v_run;
    INSERT INTO factor.value(run_id, stock_code, trade_date, value, quality, reason_codes, reason_detail)
    SELECT v_run, item.stock_code, item.trade_date, item.value, item.quality, item.reason_codes, item.reason_detail
    FROM jsonb_to_recordset(p_rows) AS item(
        stock_code varchar, trade_date date, value numeric, quality varchar,
        reason_codes text[], reason_detail text
    );
    GET DIAGNOSTICS v_count = ROW_COUNT;
    IF v_count <> jsonb_array_length(p_rows) THEN
        RAISE EXCEPTION '结果没有完整保存';
    END IF;
    PERFORM audit.fn_business('FACTOR_RUN', 'factor.run', v_run::text,
        jsonb_build_object('factor_id', p_factor_id, 'version', p_version, 'rows', v_count));
    RETURN v_run;
END $$;
ALTER FUNCTION factor.fn_save_run(bigint, integer, date, text, text, text, jsonb) OWNER TO quant_owner;

REVOKE ALL ON FUNCTION factor.fn_save_run(bigint, integer, date, text, text, text, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION factor.fn_create_private(text, text, jsonb, text, text, text, jsonb, text, integer, text[], text, boolean) FROM PUBLIC;
REVOKE ALL ON FUNCTION factor.fn_new_version(bigint, text, jsonb, text, text, text, jsonb, text, integer, text[], text, boolean) FROM PUBLIC;
REVOKE ALL ON FUNCTION factor.fn_copy_public(bigint, integer, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION factor.fn_archive(bigint) FROM PUBLIC;

GRANT USAGE ON SCHEMA factor TO app_user;
GRANT SELECT ON factor.definition, factor.version, factor.run, factor.value TO app_user;
GRANT EXECUTE ON FUNCTION factor.app_user_id() TO app_user;
GRANT EXECUTE ON FUNCTION factor.fn_create_private(text, text, jsonb, text, text, text, jsonb, text, integer, text[], text, boolean) TO app_user;
GRANT EXECUTE ON FUNCTION factor.fn_new_version(bigint, text, jsonb, text, text, text, jsonb, text, integer, text[], text, boolean) TO app_user;
GRANT EXECUTE ON FUNCTION factor.fn_copy_public(bigint, integer, text) TO app_user;
GRANT EXECUTE ON FUNCTION factor.fn_archive(bigint) TO app_user;
GRANT EXECUTE ON FUNCTION factor.fn_save_run(bigint, integer, date, text, text, text, jsonb) TO app_user;
GRANT EXECUTE ON FUNCTION pit.fn_factor_panel(text, integer) TO app_user;

GRANT USAGE ON SCHEMA core TO app_user, auth_login;
GRANT EXECUTE ON FUNCTION core.fn_session_user(text), core.fn_revoke_session(text) TO app_user;
GRANT EXECUTE ON FUNCTION core.fn_password_hash(text), core.fn_open_session(bigint, text, timestamptz) TO auth_login;

GRANT USAGE ON SCHEMA audit TO app_user, auditor, audit_writer;
GRANT SELECT ON audit.request_event, audit.business_event, audit.idempotency TO app_user, auditor;
GRANT INSERT ON audit.idempotency TO app_user;
GRANT EXECUTE ON FUNCTION audit.fn_business(text, text, text, jsonb) TO app_user;
GRANT EXECUTE ON FUNCTION audit.fn_append_request(uuid, text, bigint, date, text, text, text) TO audit_writer;
DROP POLICY IF EXISTS idempotency_insert ON audit.idempotency;
CREATE POLICY idempotency_insert ON audit.idempotency FOR INSERT TO app_user
    WITH CHECK (user_id = factor.app_user_id());

GRANT USAGE ON SCHEMA agent TO app_user;
GRANT SELECT, INSERT ON agent.session, agent.tool_call TO app_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA agent TO app_user;
ALTER TABLE agent.session ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent.tool_call ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_session_own ON agent.session;
CREATE POLICY agent_session_own ON agent.session FOR SELECT TO app_user
    USING (user_id = factor.app_user_id());
DROP POLICY IF EXISTS agent_session_insert ON agent.session;
CREATE POLICY agent_session_insert ON agent.session FOR INSERT TO app_user
    WITH CHECK (user_id = factor.app_user_id());
DROP POLICY IF EXISTS agent_call_own ON agent.tool_call;
CREATE POLICY agent_call_own ON agent.tool_call FOR SELECT TO app_user
    USING (EXISTS (
        SELECT 1 FROM agent.session s
        WHERE s.agent_session_id = agent.tool_call.agent_session_id
          AND s.user_id = factor.app_user_id()
    ));
DROP POLICY IF EXISTS agent_call_insert ON agent.tool_call;
CREATE POLICY agent_call_insert ON agent.tool_call FOR INSERT TO app_user
    WITH CHECK (EXISTS (
        SELECT 1 FROM agent.session s
        WHERE s.agent_session_id = agent_session_id AND s.user_id = factor.app_user_id()
    ));

REVOKE ALL ON SCHEMA factor FROM agent_reader;
REVOKE ALL ON ALL TABLES IN SCHEMA factor FROM agent_reader;
REVOKE ALL ON SCHEMA audit FROM agent_reader;
REVOKE ALL ON ALL TABLES IN SCHEMA audit FROM agent_reader;
