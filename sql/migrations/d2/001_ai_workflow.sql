-- D2：提示词、对话、证据和检查点。可重复执行，不改行情和交易表。

ALTER TABLE factor.version ADD COLUMN IF NOT EXISTS category text;
ALTER TABLE factor.version ADD COLUMN IF NOT EXISTS source_kind text;

CREATE TABLE IF NOT EXISTS factor.public_meta (
    code varchar(64) PRIMARY KEY,
    category text NOT NULL,
    source_kind text NOT NULL,
    delivery text NOT NULL CHECK (delivery IN ('EXISTING', 'PROPOSED'))
);
ALTER TABLE factor.public_meta OWNER TO quant_owner;
COMMENT ON TABLE factor.public_meta IS '公共公式的分类和来源性质。表达式仍以 factor.version 为准。';
COMMENT ON COLUMN factor.public_meta.code IS '公共公式代码。';
COMMENT ON COLUMN factor.public_meta.category IS '展示用分类，例如收益与趋势。';
COMMENT ON COLUMN factor.public_meta.source_kind IS 'DAILY_ADAPTATION 表示日频改编，PROJECT_ADDITION 表示项目补充。';
COMMENT ON COLUMN factor.public_meta.delivery IS 'EXISTING 为原有10项，PROPOSED 为本次新增。';

CREATE TABLE IF NOT EXISTS factor.source_entry (
    source_id text PRIMARY KEY,
    document text NOT NULL,
    code text,
    name text,
    page text,
    source_line integer,
    status text NOT NULL,
    review_note text
);
ALTER TABLE factor.source_entry OWNER TO quant_owner;
COMMENT ON TABLE factor.source_entry IS '370条来源索引。未复现的条目不能当作可运行因子。';
COMMENT ON COLUMN factor.source_entry.source_id IS '来源编号，例如 SRC-001。';
COMMENT ON COLUMN factor.source_entry.document IS '原资料名称。';
COMMENT ON COLUMN factor.source_entry.code IS '资料中的条目代码，可能重名。';
COMMENT ON COLUMN factor.source_entry.name IS '资料中的名称。';
COMMENT ON COLUMN factor.source_entry.page IS '页码。';
COMMENT ON COLUMN factor.source_entry.source_line IS '整理稿中的行号。';
COMMENT ON COLUMN factor.source_entry.status IS '实现状态。NOT_IMPLEMENTED_AS_ORIGINAL 表示不能运行原公式。';
COMMENT ON COLUMN factor.source_entry.review_note IS '为什么不能把日线结果称为原公式复现。';

CREATE TABLE IF NOT EXISTS agent.prompt_profile (
    profile_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id bigint NOT NULL REFERENCES core.app_user(user_id),
    name varchar(64) NOT NULL,
    active_version integer,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (owner_id, name)
);
ALTER TABLE agent.prompt_profile OWNER TO quant_owner;
COMMENT ON TABLE agent.prompt_profile IS '用户自己的提示词配置。不能修改系统提示词。';
COMMENT ON COLUMN agent.prompt_profile.profile_id IS '配置主键。';
COMMENT ON COLUMN agent.prompt_profile.owner_id IS '配置所有者。';
COMMENT ON COLUMN agent.prompt_profile.name IS '同一用户内唯一的配置名。';
COMMENT ON COLUMN agent.prompt_profile.active_version IS '下一回合使用的版本号。';
COMMENT ON COLUMN agent.prompt_profile.created_at IS '创建时间。';

CREATE TABLE IF NOT EXISTS agent.prompt_version (
    profile_id bigint NOT NULL REFERENCES agent.prompt_profile(profile_id),
    version_no integer NOT NULL CHECK (version_no >= 1),
    content text NOT NULL CHECK (char_length(content) BETWEEN 1 AND 4000),
    digest text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (profile_id, version_no)
);
ALTER TABLE agent.prompt_version OWNER TO quant_owner;
COMMENT ON TABLE agent.prompt_version IS '个人提示词版本。写入后不能改，下一回合才使用新版本。';
COMMENT ON COLUMN agent.prompt_version.profile_id IS '所属配置。';
COMMENT ON COLUMN agent.prompt_version.version_no IS '从 1 开始的版本。';
COMMENT ON COLUMN agent.prompt_version.content IS '偏好正文，最多 4000 字。';
COMMENT ON COLUMN agent.prompt_version.digest IS '正文摘要。';
COMMENT ON COLUMN agent.prompt_version.created_at IS '写入时间。';

CREATE TABLE IF NOT EXISTS agent.conversation (
    thread_id uuid PRIMARY KEY,
    owner_id bigint NOT NULL REFERENCES core.app_user(user_id),
    as_of date NOT NULL,
    universe varchar(32) NOT NULL,
    data_mode varchar(16) NOT NULL CHECK (data_mode IN ('TEACHING', 'REAL')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (thread_id, owner_id)
);
ALTER TABLE agent.conversation OWNER TO quant_owner;
COMMENT ON TABLE agent.conversation IS '一次研究对话。thread_id 只用来定位，不证明身份。';
COMMENT ON COLUMN agent.conversation.thread_id IS '对话编号。';
COMMENT ON COLUMN agent.conversation.owner_id IS '对话所有者。';
COMMENT ON COLUMN agent.conversation.as_of IS '本对话锁定的研究日。';
COMMENT ON COLUMN agent.conversation.universe IS '本对话锁定的股票池。';
COMMENT ON COLUMN agent.conversation.data_mode IS 'TEACHING 或 REAL。';
COMMENT ON COLUMN agent.conversation.created_at IS '创建时间。';

CREATE TABLE IF NOT EXISTS agent.turn (
    turn_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    thread_id uuid NOT NULL,
    owner_id bigint NOT NULL,
    client_request_id text NOT NULL CHECK (char_length(client_request_id) BETWEEN 8 AND 80),
    status varchar(16) NOT NULL CHECK (status IN ('RUNNING', 'COMPLETED', 'INTERRUPTED', 'FAILED', 'CANCELLED')),
    prompt_version integer,
    system_hash text NOT NULL,
    provider text,
    model_name text,
    model_calls integer NOT NULL DEFAULT 0,
    tool_calls integer NOT NULL DEFAULT 0,
    answer jsonb,
    error_class text,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    FOREIGN KEY (thread_id, owner_id) REFERENCES agent.conversation(thread_id, owner_id),
    UNIQUE (owner_id, client_request_id),
    CHECK ((status = 'RUNNING' AND finished_at IS NULL) OR (status <> 'RUNNING' AND finished_at IS NOT NULL))
);
ALTER TABLE agent.turn OWNER TO quant_owner;
CREATE UNIQUE INDEX IF NOT EXISTS ux_turn_one_running ON agent.turn(thread_id) WHERE status = 'RUNNING';
COMMENT ON TABLE agent.turn IS '一轮提问。同一对话同时只能有一个进行中的回合。';
COMMENT ON COLUMN agent.turn.turn_id IS '回合主键。';
COMMENT ON COLUMN agent.turn.thread_id IS '所属对话。';
COMMENT ON COLUMN agent.turn.owner_id IS '必须与对话所有者一致。';
COMMENT ON COLUMN agent.turn.client_request_id IS '页面提交编号，防止同一次提问写两次。';
COMMENT ON COLUMN agent.turn.status IS 'RUNNING、COMPLETED、INTERRUPTED、FAILED 或 CANCELLED。';
COMMENT ON COLUMN agent.turn.prompt_version IS '回合开始时锁定的个人提示词版本。';
COMMENT ON COLUMN agent.turn.system_hash IS '回合开始时的系统提示词摘要。';
COMMENT ON COLUMN agent.turn.provider IS '模型服务名称，不含密钥。';
COMMENT ON COLUMN agent.turn.model_name IS '模型名称。';
COMMENT ON COLUMN agent.turn.model_calls IS '含重试和修复在内的模型请求次数。';
COMMENT ON COLUMN agent.turn.tool_calls IS '工具调用次数。';
COMMENT ON COLUMN agent.turn.answer IS '已核对的回答草稿。权威数字仍由证据决定。';
COMMENT ON COLUMN agent.turn.error_class IS '失败或限制类别。';
COMMENT ON COLUMN agent.turn.started_at IS '开始时间。';
COMMENT ON COLUMN agent.turn.finished_at IS '结束时间。进行中为空。';

CREATE TABLE IF NOT EXISTS agent.message (
    message_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    turn_id bigint NOT NULL REFERENCES agent.turn(turn_id),
    owner_id bigint NOT NULL,
    seq integer NOT NULL CHECK (seq >= 1),
    role varchar(16) NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool')),
    content text NOT NULL CHECK (char_length(content) <= 32000),
    tool_call_id text,
    tool_calls jsonb,
    UNIQUE (turn_id, seq)
);
ALTER TABLE agent.message OWNER TO quant_owner;
COMMENT ON TABLE agent.message IS '发给模型的协议消息。助手调用必须来自模型返回，不在这里伪造。';
COMMENT ON COLUMN agent.message.message_id IS '消息主键。';
COMMENT ON COLUMN agent.message.turn_id IS '所属回合。';
COMMENT ON COLUMN agent.message.owner_id IS '消息所有者。';
COMMENT ON COLUMN agent.message.seq IS '回合内顺序。';
COMMENT ON COLUMN agent.message.role IS 'system、user、assistant 或 tool。';
COMMENT ON COLUMN agent.message.content IS '文本内容，最长 32000 字。';
COMMENT ON COLUMN agent.message.tool_call_id IS '工具结果对应的模型调用编号。';
COMMENT ON COLUMN agent.message.tool_calls IS '模型返回的工具调用，保留原始编号。';

CREATE TABLE IF NOT EXISTS agent.evidence (
    turn_id bigint NOT NULL REFERENCES agent.turn(turn_id),
    evidence_id text NOT NULL CHECK (evidence_id ~ '^E[0-9]+$'),
    owner_id bigint NOT NULL,
    tool_name text NOT NULL,
    object_ref text,
    context jsonb NOT NULL,
    payload jsonb NOT NULL,
    digest text NOT NULL,
    PRIMARY KEY (turn_id, evidence_id)
);
ALTER TABLE agent.evidence OWNER TO quant_owner;
COMMENT ON TABLE agent.evidence IS '工具返回的可核对依据。页面卡片使用这里的数值。';
COMMENT ON COLUMN agent.evidence.turn_id IS '所属回合。';
COMMENT ON COLUMN agent.evidence.evidence_id IS '本回合内的依据编号，例如 E1。';
COMMENT ON COLUMN agent.evidence.owner_id IS '依据所有者。';
COMMENT ON COLUMN agent.evidence.tool_name IS '产生依据的工具。';
COMMENT ON COLUMN agent.evidence.object_ref IS '运行、公式或其他对象编号。';
COMMENT ON COLUMN agent.evidence.context IS '日期、股票池、版本、单位和数据性质。';
COMMENT ON COLUMN agent.evidence.payload IS '工具数据，单条受大小限制。';
COMMENT ON COLUMN agent.evidence.digest IS '依据摘要。';

CREATE TABLE IF NOT EXISTS agent.tool_execution (
    owner_id bigint NOT NULL,
    thread_id uuid NOT NULL,
    turn_id bigint NOT NULL REFERENCES agent.turn(turn_id),
    operation_key text NOT NULL,
    call_id text,
    status varchar(16) NOT NULL CHECK (status IN ('SUCCEEDED', 'REJECTED', 'FAILED')),
    result_ref text,
    error_class text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, thread_id, turn_id, operation_key)
);
ALTER TABLE agent.tool_execution OWNER TO quant_owner;
COMMENT ON TABLE agent.tool_execution IS '同一回合里相同操作只成功一次。模型调用编号不能单独当作防重依据。';
COMMENT ON COLUMN agent.tool_execution.owner_id IS '操作所有者。';
COMMENT ON COLUMN agent.tool_execution.thread_id IS '所属对话。';
COMMENT ON COLUMN agent.tool_execution.turn_id IS '所属回合。';
COMMENT ON COLUMN agent.tool_execution.operation_key IS '由用户、对话、回合、工具、参数和上下文算出的键。';
COMMENT ON COLUMN agent.tool_execution.call_id IS '模型协议里的调用编号。';
COMMENT ON COLUMN agent.tool_execution.status IS 'SUCCEEDED、REJECTED 或 FAILED。';
COMMENT ON COLUMN agent.tool_execution.result_ref IS '成功时的对象编号，例如运行编号。';
COMMENT ON COLUMN agent.tool_execution.error_class IS '拒绝或失败类别。';
COMMENT ON COLUMN agent.tool_execution.created_at IS '写入时间。';

CREATE TABLE IF NOT EXISTS agent.graph_checkpoint (
    owner_id bigint NOT NULL,
    thread_id uuid NOT NULL,
    checkpoint_ns text NOT NULL DEFAULT '',
    checkpoint_id text NOT NULL,
    parent_id text,
    serde_type text NOT NULL,
    blob bytea NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, thread_id, checkpoint_ns, checkpoint_id),
    FOREIGN KEY (thread_id, owner_id) REFERENCES agent.conversation(thread_id, owner_id)
);
ALTER TABLE agent.graph_checkpoint OWNER TO quant_owner;
COMMENT ON TABLE agent.graph_checkpoint IS '图的检查点。读取和写入都要带所有者，thread_id 本身不是登录证明。';
COMMENT ON COLUMN agent.graph_checkpoint.owner_id IS '检查点所有者。';
COMMENT ON COLUMN agent.graph_checkpoint.thread_id IS '所属对话。';
COMMENT ON COLUMN agent.graph_checkpoint.checkpoint_ns IS '图的命名空间，默认为空。';
COMMENT ON COLUMN agent.graph_checkpoint.checkpoint_id IS '检查点编号。';
COMMENT ON COLUMN agent.graph_checkpoint.parent_id IS '上一个检查点。';
COMMENT ON COLUMN agent.graph_checkpoint.serde_type IS '序列化类型。';
COMMENT ON COLUMN agent.graph_checkpoint.blob IS '检查点内容，不含密钥。';
COMMENT ON COLUMN agent.graph_checkpoint.metadata IS '框架元数据。';
COMMENT ON COLUMN agent.graph_checkpoint.created_at IS '写入时间。';

CREATE TABLE IF NOT EXISTS agent.checkpoint_write (
    owner_id bigint NOT NULL,
    thread_id uuid NOT NULL,
    checkpoint_ns text NOT NULL DEFAULT '',
    checkpoint_id text NOT NULL,
    task_id text NOT NULL,
    idx integer NOT NULL,
    channel text NOT NULL,
    serde_type text NOT NULL,
    blob bytea NOT NULL,
    PRIMARY KEY (owner_id, thread_id, checkpoint_ns, checkpoint_id, task_id, idx),
    FOREIGN KEY (thread_id, owner_id) REFERENCES agent.conversation(thread_id, owner_id)
);
ALTER TABLE agent.checkpoint_write OWNER TO quant_owner;
COMMENT ON TABLE agent.checkpoint_write IS '图尚未合并的写入，用于中断后继续。';
COMMENT ON COLUMN agent.checkpoint_write.owner_id IS '写入所有者。';
COMMENT ON COLUMN agent.checkpoint_write.thread_id IS '所属对话。';
COMMENT ON COLUMN agent.checkpoint_write.checkpoint_ns IS '图的命名空间。';
COMMENT ON COLUMN agent.checkpoint_write.checkpoint_id IS '所属检查点。';
COMMENT ON COLUMN agent.checkpoint_write.task_id IS '产生写入的任务。';
COMMENT ON COLUMN agent.checkpoint_write.idx IS '任务内序号。';
COMMENT ON COLUMN agent.checkpoint_write.channel IS '状态通道名。';
COMMENT ON COLUMN agent.checkpoint_write.serde_type IS '序列化类型。';
COMMENT ON COLUMN agent.checkpoint_write.blob IS '写入内容。';

CREATE OR REPLACE FUNCTION agent.trg_prompt_frozen() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION '已保存的个人提示词版本不能修改或删除' USING ERRCODE = '42501';
END $$;
ALTER FUNCTION agent.trg_prompt_frozen() OWNER TO quant_owner;
DROP TRIGGER IF EXISTS prompt_version_frozen ON agent.prompt_version;
CREATE TRIGGER prompt_version_frozen BEFORE UPDATE OR DELETE ON agent.prompt_version
FOR EACH ROW EXECUTE FUNCTION agent.trg_prompt_frozen();

ALTER TABLE factor.public_meta ENABLE ROW LEVEL SECURITY;
ALTER TABLE factor.public_meta FORCE ROW LEVEL SECURITY;
ALTER TABLE factor.source_entry ENABLE ROW LEVEL SECURITY;
ALTER TABLE factor.source_entry FORCE ROW LEVEL SECURITY;
ALTER TABLE agent.prompt_profile ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent.prompt_profile FORCE ROW LEVEL SECURITY;
ALTER TABLE agent.prompt_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent.prompt_version FORCE ROW LEVEL SECURITY;
ALTER TABLE agent.conversation ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent.conversation FORCE ROW LEVEL SECURITY;
ALTER TABLE agent.turn ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent.turn FORCE ROW LEVEL SECURITY;
ALTER TABLE agent.message ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent.message FORCE ROW LEVEL SECURITY;
ALTER TABLE agent.evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent.evidence FORCE ROW LEVEL SECURITY;
ALTER TABLE agent.tool_execution ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent.tool_execution FORCE ROW LEVEL SECURITY;
ALTER TABLE agent.graph_checkpoint ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent.graph_checkpoint FORCE ROW LEVEL SECURITY;
ALTER TABLE agent.checkpoint_write ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent.checkpoint_write FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS public_meta_read ON factor.public_meta;
CREATE POLICY public_meta_read ON factor.public_meta FOR SELECT TO app_user USING (true);
DROP POLICY IF EXISTS source_read ON factor.source_entry;
CREATE POLICY source_read ON factor.source_entry FOR SELECT TO app_user USING (true);
DROP POLICY IF EXISTS prompt_profile_own ON agent.prompt_profile;
CREATE POLICY prompt_profile_own ON agent.prompt_profile FOR ALL TO app_user
    USING (owner_id = factor.app_user_id()) WITH CHECK (owner_id = factor.app_user_id());
DROP POLICY IF EXISTS prompt_version_own ON agent.prompt_version;
CREATE POLICY prompt_version_own ON agent.prompt_version FOR SELECT TO app_user
    USING (EXISTS (SELECT 1 FROM agent.prompt_profile p WHERE p.profile_id = prompt_version.profile_id AND p.owner_id = factor.app_user_id()));
DROP POLICY IF EXISTS prompt_version_insert ON agent.prompt_version;
CREATE POLICY prompt_version_insert ON agent.prompt_version FOR INSERT TO app_user
    WITH CHECK (EXISTS (SELECT 1 FROM agent.prompt_profile p WHERE p.profile_id = prompt_version.profile_id AND p.owner_id = factor.app_user_id()));
DROP POLICY IF EXISTS conversation_own ON agent.conversation;
CREATE POLICY conversation_own ON agent.conversation FOR ALL TO app_user
    USING (owner_id = factor.app_user_id()) WITH CHECK (owner_id = factor.app_user_id());
DROP POLICY IF EXISTS turn_own ON agent.turn;
CREATE POLICY turn_own ON agent.turn FOR ALL TO app_user
    USING (owner_id = factor.app_user_id()) WITH CHECK (owner_id = factor.app_user_id());
DROP POLICY IF EXISTS message_own ON agent.message;
CREATE POLICY message_own ON agent.message FOR ALL TO app_user
    USING (owner_id = factor.app_user_id()) WITH CHECK (owner_id = factor.app_user_id());
DROP POLICY IF EXISTS evidence_own ON agent.evidence;
CREATE POLICY evidence_own ON agent.evidence FOR ALL TO app_user
    USING (owner_id = factor.app_user_id()) WITH CHECK (owner_id = factor.app_user_id());
DROP POLICY IF EXISTS execution_own ON agent.tool_execution;
CREATE POLICY execution_own ON agent.tool_execution FOR ALL TO app_user
    USING (owner_id = factor.app_user_id()) WITH CHECK (owner_id = factor.app_user_id());
DROP POLICY IF EXISTS checkpoint_own ON agent.graph_checkpoint;
CREATE POLICY checkpoint_own ON agent.graph_checkpoint FOR ALL TO checkpoint_writer
    USING (owner_id = factor.app_user_id()) WITH CHECK (owner_id = factor.app_user_id());
DROP POLICY IF EXISTS checkpoint_write_own ON agent.checkpoint_write;
CREATE POLICY checkpoint_write_own ON agent.checkpoint_write FOR ALL TO checkpoint_writer
    USING (owner_id = factor.app_user_id()) WITH CHECK (owner_id = factor.app_user_id());

GRANT USAGE ON SCHEMA agent TO quant_owner, app_user;
GRANT USAGE ON SCHEMA pit, ref, mkt, audit, core, sim, factor TO quant_owner;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA pit, ref, mkt, audit, core, sim, factor TO quant_owner;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA pit, ref, mkt, audit, core, sim, factor TO quant_owner;
GRANT SELECT ON factor.public_meta, factor.source_entry TO app_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON
    agent.prompt_profile, agent.conversation, agent.turn, agent.message, agent.evidence,
    agent.tool_execution, agent.graph_checkpoint, agent.checkpoint_write
    TO app_user;
GRANT SELECT, INSERT ON agent.prompt_version TO app_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA agent TO app_user;
REVOKE ALL ON TABLE agent.prompt_profile, agent.prompt_version, agent.conversation, agent.turn,
    agent.message, agent.evidence, agent.tool_execution, agent.graph_checkpoint, agent.checkpoint_write
    FROM agent_reader;

GRANT USAGE ON SCHEMA agent,factor TO checkpoint_writer;
GRANT EXECUTE ON FUNCTION factor.app_user_id() TO checkpoint_writer;
GRANT SELECT,INSERT,UPDATE,DELETE ON agent.graph_checkpoint,agent.checkpoint_write TO checkpoint_writer;
REVOKE ALL ON agent.graph_checkpoint,agent.checkpoint_write FROM app_user;

-- Bind child rows to the same owner/turn/thread, including direct restricted-role writes.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='agent.turn'::regclass AND conname='turn_owner_binding') THEN
        ALTER TABLE agent.turn ADD CONSTRAINT turn_owner_binding UNIQUE(turn_id,owner_id);
        ALTER TABLE agent.turn ADD CONSTRAINT turn_thread_binding UNIQUE(turn_id,owner_id,thread_id);
        ALTER TABLE agent.message ADD CONSTRAINT message_owner_binding FOREIGN KEY(turn_id,owner_id) REFERENCES agent.turn(turn_id,owner_id);
        ALTER TABLE agent.evidence ADD CONSTRAINT evidence_owner_binding FOREIGN KEY(turn_id,owner_id) REFERENCES agent.turn(turn_id,owner_id);
        ALTER TABLE agent.tool_execution ADD CONSTRAINT execution_owner_binding FOREIGN KEY(turn_id,owner_id,thread_id) REFERENCES agent.turn(turn_id,owner_id,thread_id);
    END IF;
END $$;
