-- 知时：数据库初始化（B 负责）
-- 目标：PostgreSQL 16；重复执行安全。

CREATE EXTENSION IF NOT EXISTS btree_gist;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'quant_owner') THEN
        CREATE ROLE quant_owner NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'etl_writer') THEN
        CREATE ROLE etl_writer LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN
        CREATE ROLE app_user LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_reader') THEN
        CREATE ROLE agent_reader LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'auditor') THEN
        CREATE ROLE auditor LOGIN;
    END IF;
END $$;

ALTER ROLE quant_owner NOLOGIN;
ALTER ROLE etl_writer LOGIN;
ALTER ROLE app_user LOGIN;
ALTER ROLE agent_reader LOGIN;
ALTER ROLE auditor LOGIN;

CREATE SCHEMA IF NOT EXISTS core AUTHORIZATION quant_owner;
CREATE SCHEMA IF NOT EXISTS staging AUTHORIZATION quant_owner;
CREATE SCHEMA IF NOT EXISTS etl AUTHORIZATION quant_owner;
CREATE SCHEMA IF NOT EXISTS ref AUTHORIZATION quant_owner;
CREATE SCHEMA IF NOT EXISTS mkt AUTHORIZATION quant_owner;
CREATE SCHEMA IF NOT EXISTS fin AUTHORIZATION quant_owner;
CREATE SCHEMA IF NOT EXISTS pit AUTHORIZATION quant_owner;
CREATE SCHEMA IF NOT EXISTS sim AUTHORIZATION quant_owner;
CREATE SCHEMA IF NOT EXISTS research AUTHORIZATION quant_owner;
CREATE SCHEMA IF NOT EXISTS agent AUTHORIZATION quant_owner;
CREATE SCHEMA IF NOT EXISTS audit AUTHORIZATION quant_owner;

ALTER SCHEMA core OWNER TO quant_owner;
ALTER SCHEMA staging OWNER TO quant_owner;
ALTER SCHEMA etl OWNER TO quant_owner;
ALTER SCHEMA ref OWNER TO quant_owner;
ALTER SCHEMA mkt OWNER TO quant_owner;
ALTER SCHEMA fin OWNER TO quant_owner;
ALTER SCHEMA pit OWNER TO quant_owner;
ALTER SCHEMA sim OWNER TO quant_owner;
ALTER SCHEMA research OWNER TO quant_owner;
ALTER SCHEMA agent OWNER TO quant_owner;
ALTER SCHEMA audit OWNER TO quant_owner;

CREATE TABLE IF NOT EXISTS core.app_user (
    user_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username     varchar(64)  NOT NULL UNIQUE,
    display_name varchar(128) NOT NULL,
    role         varchar(16)  NOT NULL DEFAULT 'LEARNER'
                 CHECK (role IN ('LEARNER', 'ADMIN', 'AUDITOR')),
    created_at   timestamptz  NOT NULL DEFAULT now()
);
ALTER TABLE core.app_user OWNER TO quant_owner;

COMMENT ON TABLE core.app_user IS '应用用户基础表；供 RLS、模拟账户、回测和 Agent 会话引用。';
COMMENT ON COLUMN core.app_user.user_id IS '应用用户主键。';
COMMENT ON COLUMN core.app_user.username IS '登录/展示用唯一用户名。';
COMMENT ON COLUMN core.app_user.display_name IS '用户显示名称。';
COMMENT ON COLUMN core.app_user.role IS '应用角色：LEARNER/ADMIN/AUDITOR。';
COMMENT ON COLUMN core.app_user.created_at IS '用户创建时间。';
