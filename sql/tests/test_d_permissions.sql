-- 用真实的 app_user 身份检查 D 的授权。超级用户只负责切换角色。
DO $$
DECLARE
    v_public int;
BEGIN
    SELECT count(*) INTO v_public FROM factor.definition WHERE visibility = 'SYSTEM_PUBLIC';
    IF (SELECT count(*) FROM factor.definition WHERE code IN (
        'VWAP_D','RET_INTRADAY_D','RET_OVERNIGHT_D','RANGE_D','VWAP20_D','VWAP_BIAS_D',
        'VOLUME_REL20_D','MOM20_D','MA20_BIAS_D','VOL20_D'
    )) <> 10 THEN
        RAISE EXCEPTION '原有 10 个公共公式缺失';
    END IF;
    IF v_public < 10 THEN
        RAISE EXCEPTION '公共因子不足，实际 %', v_public;
    END IF;
    EXECUTE 'SET LOCAL ROLE app_user';
    IF (SELECT count(*) FROM factor.definition) <> v_public THEN
        RAISE EXCEPTION '未登录上下文不应看到私有公式，或公共目录不完整';
    END IF;
    BEGIN
        PERFORM 1 FROM mkt.daily_bar LIMIT 1;
        RAISE EXCEPTION '普通用户不应读取原始行情表';
    EXCEPTION WHEN insufficient_privilege THEN
        NULL;
    END;
    BEGIN
        INSERT INTO factor.definition(visibility, name) VALUES ('USER_PRIVATE', '越权');
        RAISE EXCEPTION '普通用户不应直接插入公式';
    EXCEPTION WHEN insufficient_privilege THEN
        NULL;
    END;
    BEGIN
        DELETE FROM audit.request_event;
        RAISE EXCEPTION '普通用户不应删除请求记录';
    EXCEPTION WHEN insufficient_privilege THEN
        NULL;
    END;
    EXECUTE 'RESET ROLE';
    EXECUTE 'SET LOCAL ROLE agent_reader';
    BEGIN
        PERFORM 1 FROM factor.definition LIMIT 1;
        RAISE EXCEPTION 'AI 角色不应直接读取因子表';
    EXCEPTION WHEN insufficient_privilege THEN
        NULL;
    END;
    EXECUTE 'RESET ROLE';
    EXECUTE 'SET LOCAL ROLE auditor';
    BEGIN
        UPDATE audit.request_event SET result_summary = '改写';
        RAISE EXCEPTION '审计角色不应改写记录';
    EXCEPTION WHEN insufficient_privilege OR OTHERS THEN
        IF SQLERRM NOT LIKE '%操作记录只能追加%' AND SQLSTATE <> '42501' THEN
            RAISE;
        END IF;
    END;
END $$;
