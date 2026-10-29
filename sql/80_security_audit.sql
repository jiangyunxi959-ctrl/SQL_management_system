-- D：登录凭据、会话、请求记录和业务记录。凭据不授予普通查询角色。

CREATE TABLE IF NOT EXISTS core.user_credential (
    user_id       bigint PRIMARY KEY REFERENCES core.app_user(user_id),
    password_hash text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE core.user_credential OWNER TO quant_owner;
COMMENT ON TABLE core.user_credential IS '登录密码摘要。普通应用、AI 和审计角色都不能直接读取。';
COMMENT ON COLUMN core.user_credential.user_id IS '对应的应用用户。';
COMMENT ON COLUMN core.user_credential.password_hash IS 'Argon2 密码摘要，不是明文。';
COMMENT ON COLUMN core.user_credential.created_at IS '凭据创建时间。';
COMMENT ON COLUMN core.user_credential.updated_at IS '最近一次修改密码摘要的时间。';

CREATE TABLE IF NOT EXISTS core.auth_session (
    session_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     bigint NOT NULL REFERENCES core.app_user(user_id),
    token_hash  text NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    expires_at  timestamptz NOT NULL,
    revoked_at  timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE core.auth_session OWNER TO quant_owner;
COMMENT ON TABLE core.auth_session IS '登录会话。数据库里只保存凭证摘要，不保存登录凭证本身。';
COMMENT ON COLUMN core.auth_session.session_id IS '会话主键。';
COMMENT ON COLUMN core.auth_session.user_id IS '会话所属用户。';
COMMENT ON COLUMN core.auth_session.token_hash IS '会话凭证的 SHA-256 摘要。';
COMMENT ON COLUMN core.auth_session.expires_at IS '过期时间。过期后不能再解析出用户。';
COMMENT ON COLUMN core.auth_session.revoked_at IS '注销或撤销时间。为空表示尚未撤销。';
COMMENT ON COLUMN core.auth_session.created_at IS '会话创建时间。';

CREATE TABLE IF NOT EXISTS audit.request_event (
    request_id    uuid NOT NULL,
    event_seq     integer NOT NULL CHECK (event_seq >= 1),
    event_type    varchar(16) NOT NULL CHECK (event_type IN ('START', 'SUCCESS', 'FAILURE', 'REJECTED', 'PENDING_CHECK')),
    user_id       bigint,
    as_of         date,
    tool_name     text NOT NULL,
    result_summary text,
    error_class   text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (request_id, event_seq)
);
ALTER TABLE audit.request_event OWNER TO quant_owner;
CREATE UNIQUE INDEX IF NOT EXISTS ux_request_one_success
    ON audit.request_event(request_id) WHERE event_type = 'SUCCESS';
CREATE INDEX IF NOT EXISTS ix_request_user ON audit.request_event(user_id, created_at);

CREATE TABLE IF NOT EXISTS audit.business_event (
    event_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id   uuid,
    user_id      bigint NOT NULL,
    event_type   text NOT NULL,
    object_type  text NOT NULL,
    object_id    text NOT NULL,
    detail       jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE audit.business_event OWNER TO quant_owner;
CREATE INDEX IF NOT EXISTS ix_business_user ON audit.business_event(user_id, event_id);

CREATE TABLE IF NOT EXISTS audit.idempotency (
    user_id          bigint NOT NULL,
    idempotency_key  text NOT NULL CHECK (char_length(idempotency_key) BETWEEN 8 AND 80),
    request_id       uuid NOT NULL,
    result_ref       text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, idempotency_key)
);
ALTER TABLE audit.idempotency OWNER TO quant_owner;

COMMENT ON TABLE audit.request_event IS '请求开始、成功、失败、拒绝和待核对记录。与业务事务分开提交。';
COMMENT ON COLUMN audit.request_event.request_id IS '一次请求的编号。同一请求的多条事件共用它。';
COMMENT ON COLUMN audit.request_event.event_seq IS '同一请求内从 1 递增的事件序号。';
COMMENT ON COLUMN audit.request_event.event_type IS 'START、SUCCESS、FAILURE、REJECTED 或 PENDING_CHECK。';
COMMENT ON COLUMN audit.request_event.user_id IS '已确认的用户。未登录的拒绝可以为空。';
COMMENT ON COLUMN audit.request_event.as_of IS '请求使用的研究日。';
COMMENT ON COLUMN audit.request_event.tool_name IS '页面操作或受控工具名称。';
COMMENT ON COLUMN audit.request_event.result_summary IS '不含密码和会话凭证的简短结果。';
COMMENT ON COLUMN audit.request_event.error_class IS '失败或拒绝的类别，不包含连接密码。';
COMMENT ON COLUMN audit.request_event.created_at IS '事件写入时间。';
COMMENT ON TABLE audit.business_event IS '与业务修改同一事务提交的记录。业务回滚时它也回滚。';
COMMENT ON COLUMN audit.business_event.event_id IS '业务事件主键。';
COMMENT ON COLUMN audit.business_event.request_id IS '关联的请求编号。可以后于请求开始记录写入。';
COMMENT ON COLUMN audit.business_event.user_id IS '做出这次业务修改的用户。';
COMMENT ON COLUMN audit.business_event.event_type IS '业务动作，例如创建公式版本或模拟下单。';
COMMENT ON COLUMN audit.business_event.object_type IS '对象类别，例如 factor.version 或 sim.orders。';
COMMENT ON COLUMN audit.business_event.object_id IS '对象编号。公式同时写定义和版本。';
COMMENT ON COLUMN audit.business_event.detail IS '不含凭据的补充说明。';
COMMENT ON COLUMN audit.business_event.created_at IS '事件写入时间。';
COMMENT ON TABLE audit.idempotency IS '同一用户的重复提交键。用来避免一次点击产生两笔模拟订单。';
COMMENT ON COLUMN audit.idempotency.user_id IS '提交请求的用户。';
COMMENT ON COLUMN audit.idempotency.idempotency_key IS '调用方提供的重复提交键。';
COMMENT ON COLUMN audit.idempotency.request_id IS '第一次成功请求的编号。';
COMMENT ON COLUMN audit.idempotency.result_ref IS '第一次成功创建的对象编号。';
COMMENT ON COLUMN audit.idempotency.created_at IS '第一次成功写入的时间。';

CREATE OR REPLACE FUNCTION audit.trg_append_only() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION '操作记录只能追加，不能修改或删除' USING ERRCODE = '42501';
END $$;
ALTER FUNCTION audit.trg_append_only() OWNER TO quant_owner;
DROP TRIGGER IF EXISTS request_append_only ON audit.request_event;
CREATE TRIGGER request_append_only BEFORE UPDATE OR DELETE ON audit.request_event
FOR EACH ROW EXECUTE FUNCTION audit.trg_append_only();
DROP TRIGGER IF EXISTS business_append_only ON audit.business_event;
CREATE TRIGGER business_append_only BEFORE UPDATE OR DELETE ON audit.business_event
FOR EACH ROW EXECUTE FUNCTION audit.trg_append_only();
DROP TRIGGER IF EXISTS idempotency_append_only ON audit.idempotency;
CREATE TRIGGER idempotency_append_only BEFORE UPDATE OR DELETE ON audit.idempotency
FOR EACH ROW EXECUTE FUNCTION audit.trg_append_only();

CREATE OR REPLACE FUNCTION core.fn_password_hash(p_username text)
RETURNS table(user_id bigint, password_hash text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, core AS $$
    SELECT u.user_id, c.password_hash
    FROM core.app_user u
    JOIN core.user_credential c ON c.user_id = u.user_id
    WHERE u.username = p_username
$$;
ALTER FUNCTION core.fn_password_hash(text) OWNER TO quant_owner;
COMMENT ON FUNCTION core.fn_password_hash(text) IS '按用户名读取密码摘要。只授予登录角色，不告诉调用方账号是否存在。';

CREATE OR REPLACE FUNCTION core.fn_open_session(p_user_id bigint, p_token_hash text, p_expires timestamptz)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, core AS $$
DECLARE
    v_id bigint;
BEGIN
    IF p_token_hash IS NULL OR p_token_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION '会话摘要不合法';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM core.app_user WHERE core.app_user.user_id = p_user_id) THEN
        RAISE EXCEPTION '用户不存在';
    END IF;
    INSERT INTO core.auth_session(user_id, token_hash, expires_at)
    VALUES (p_user_id, p_token_hash, p_expires)
    RETURNING session_id INTO v_id;
    RETURN v_id;
END $$;
ALTER FUNCTION core.fn_open_session(bigint, text, timestamptz) OWNER TO quant_owner;
COMMENT ON FUNCTION core.fn_open_session(bigint, text, timestamptz) IS '保存会话摘要和有效期。不接收明文会话凭证。';

CREATE OR REPLACE FUNCTION core.fn_session_user(p_token_hash text)
RETURNS table(user_id bigint, username varchar, display_name varchar)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, core AS $$
    SELECT u.user_id, u.username, u.display_name
    FROM core.auth_session s
    JOIN core.app_user u ON u.user_id = s.user_id
    WHERE s.token_hash = p_token_hash
      AND s.revoked_at IS NULL
      AND s.expires_at > now()
$$;
ALTER FUNCTION core.fn_session_user(text) OWNER TO quant_owner;
COMMENT ON FUNCTION core.fn_session_user(text) IS '用会话摘要解析尚未过期、尚未撤销的用户。';

CREATE OR REPLACE FUNCTION core.fn_revoke_session(p_token_hash text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, core AS $$
BEGIN
    UPDATE core.auth_session
       SET revoked_at = now()
     WHERE token_hash = p_token_hash AND revoked_at IS NULL;
    RETURN FOUND;
END $$;
ALTER FUNCTION core.fn_revoke_session(text) OWNER TO quant_owner;
COMMENT ON FUNCTION core.fn_revoke_session(text) IS '撤销一个会话。不知道摘要就不能撤销别人的会话。';

CREATE OR REPLACE FUNCTION audit.fn_append_request(
    p_request uuid, p_type text, p_user bigint, p_as_of date, p_tool text, p_summary text, p_error text
) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, audit AS $$
DECLARE
    v_seq integer;
BEGIN
    IF p_type NOT IN ('START', 'SUCCESS', 'FAILURE', 'REJECTED', 'PENDING_CHECK') THEN
        RAISE EXCEPTION '未知的请求事件类型';
    END IF;
    IF p_tool IS NULL OR char_length(p_tool) > 80 THEN
        RAISE EXCEPTION '工具名称不合法';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(p_request::text, 0));
    IF p_type = 'SUCCESS' AND EXISTS (
        SELECT 1 FROM audit.request_event WHERE request_id = p_request AND event_type = 'SUCCESS'
    ) THEN
        RAISE EXCEPTION '同一请求不能记录两次成功';
    END IF;
    SELECT coalesce(max(event_seq), 0) + 1 INTO v_seq
    FROM audit.request_event WHERE request_id = p_request;
    INSERT INTO audit.request_event(request_id, event_seq, event_type, user_id, as_of, tool_name, result_summary, error_class)
    VALUES (p_request, v_seq, p_type, p_user, p_as_of, p_tool, left(p_summary, 500), left(p_error, 120));
    RETURN v_seq;
END $$;
ALTER FUNCTION audit.fn_append_request(uuid, text, bigint, date, text, text, text) OWNER TO quant_owner;
COMMENT ON FUNCTION audit.fn_append_request(uuid, text, bigint, date, text, text, text) IS '追加一条请求事件。调用方不能修改旧事件。';

CREATE OR REPLACE FUNCTION audit.fn_business(p_type text, p_object_type text, p_object_id text, p_detail jsonb)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, audit, factor AS $$
DECLARE
    v_user bigint := factor.app_user_id();
    v_request text := nullif(current_setting('app.request_id', true), '');
    v_id bigint;
BEGIN
    IF v_user IS NULL THEN RAISE EXCEPTION '缺少已验证用户' USING ERRCODE = '42501'; END IF;
    INSERT INTO audit.business_event(request_id, user_id, event_type, object_type, object_id, detail)
    VALUES (v_request::uuid, v_user, p_type, p_object_type, p_object_id, coalesce(p_detail, '{}'::jsonb))
    RETURNING event_id INTO v_id;
    RETURN v_id;
END $$;
ALTER FUNCTION audit.fn_business(text, text, text, jsonb) OWNER TO quant_owner;
COMMENT ON FUNCTION audit.fn_business(text, text, text, jsonb) IS '在当前业务事务中追加一条业务事件。';

CREATE OR REPLACE FUNCTION audit.trg_sim_order() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, audit, sim AS $$
DECLARE
    v_request text := nullif(current_setting('app.request_id', true), '');
BEGIN
    IF v_request IS NOT NULL THEN
        INSERT INTO audit.business_event(request_id, user_id, event_type, object_type, object_id, detail)
        VALUES (v_request::uuid, sim.require_user(), 'SIM_ORDER', 'sim.orders', NEW.order_id::text,
                jsonb_build_object('stock_code', NEW.stock_code, 'side', NEW.side, 'qty', NEW.qty));
    END IF;
    RETURN NEW;
END $$;
ALTER FUNCTION audit.trg_sim_order() OWNER TO quant_owner;
DROP TRIGGER IF EXISTS sim_order_audit ON sim.orders;
CREATE TRIGGER sim_order_audit AFTER INSERT ON sim.orders
FOR EACH ROW EXECUTE FUNCTION audit.trg_sim_order();

ALTER TABLE audit.request_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit.business_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit.idempotency ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS request_own ON audit.request_event;
CREATE POLICY request_own ON audit.request_event FOR SELECT TO app_user
    USING (user_id = factor.app_user_id());
DROP POLICY IF EXISTS request_auditor ON audit.request_event;
CREATE POLICY request_auditor ON audit.request_event FOR SELECT TO auditor USING (true);
DROP POLICY IF EXISTS business_own ON audit.business_event;
CREATE POLICY business_own ON audit.business_event FOR SELECT TO app_user
    USING (user_id = factor.app_user_id());
DROP POLICY IF EXISTS business_auditor ON audit.business_event;
CREATE POLICY business_auditor ON audit.business_event FOR SELECT TO auditor USING (true);
DROP POLICY IF EXISTS idempotency_own ON audit.idempotency;
CREATE POLICY idempotency_own ON audit.idempotency FOR SELECT TO app_user
    USING (user_id = factor.app_user_id());

REVOKE ALL ON TABLE core.user_credential FROM PUBLIC;
REVOKE ALL ON TABLE core.auth_session FROM PUBLIC;
REVOKE ALL ON FUNCTION core.fn_password_hash(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION core.fn_open_session(bigint, text, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION core.fn_session_user(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION core.fn_revoke_session(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION audit.fn_append_request(uuid, text, bigint, date, text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION audit.fn_business(text, text, text, jsonb) FROM PUBLIC;
