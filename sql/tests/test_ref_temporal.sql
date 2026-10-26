-- 每个测试由运行器包在事务中，结束后回滚。
DO $$
DECLARE v_value numeric;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM ref.security_status
        WHERE stock_code='sz.000001' AND status='ST' AND valid @> date '2025-03-10') THEN
        RAISE EXCEPTION 'ST 连续区间转换错误';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM ref.security_status
        WHERE stock_code='sz.000001' AND status='NORMAL' AND valid @> date '2025-03-17') THEN
        RAISE EXCEPTION 'ST 结束边界错误';
    END IF;
    BEGIN
        INSERT INTO ref.security_status VALUES ('sz.000001','NORMAL','[2025-03-10,2025-03-12)','TEST');
        RAISE EXCEPTION '重叠状态居然写入成功';
    EXCEPTION WHEN exclusion_violation THEN NULL; END;
    BEGIN
        INSERT INTO ref.index_member VALUES ('SYNTH300','sh.600001','[2025-06-01,2025-08-01)','TEST');
        RAISE EXCEPTION '重叠成员区间居然写入成功';
    EXCEPTION WHEN exclusion_violation THEN NULL; END;
    BEGIN
        INSERT INTO ref.security_name VALUES ('sh.600001','空区间','empty','TEST');
        RAISE EXCEPTION '空区间居然写入成功';
    EXCEPTION WHEN check_violation THEN NULL; END;
    BEGIN
        INSERT INTO ref.security_name VALUES ('sh.600001','重叠名称','[2025-01-01,2025-02-01)','TEST');
        RAISE EXCEPTION '重叠名称写入成功';
    EXCEPTION WHEN exclusion_violation THEN NULL; END;
    BEGIN
        INSERT INTO ref.industry_member VALUES ('sh.600001','DEMO_TECH','[2025-01-01,2025-02-01)','TEST');
        RAISE EXCEPTION '重叠行业写入成功';
    EXCEPTION WHEN exclusion_violation THEN NULL; END;
    BEGIN
        INSERT INTO ref.price_limit_rule(board,status_class,limit_pct,valid,source)
        VALUES ('MAIN','NORMAL',0.20,'[2025-01-01,2025-02-01)','TEST');
        RAISE EXCEPTION '重叠涨跌幅规则写入成功';
    EXCEPTION WHEN exclusion_violation THEN NULL; END;
    BEGIN
        INSERT INTO ref.fee_rule(fee_type,side,rate,valid,source)
        VALUES ('COMMISSION','B',0.001,'[2025-01-01,2025-02-01)','TEST');
        RAISE EXCEPTION '重叠费率写入成功';
    EXCEPTION WHEN exclusion_violation THEN NULL; END;
    v_value := ref.fn_fee('2025-05-06','B',10000);
    IF v_value<>5.10 THEN RAISE EXCEPTION '买方费用错误 %',v_value; END IF;
    IF ref.fn_fee('2025-05-06','S',10000)<>10.10 THEN RAISE EXCEPTION '卖方费用错误'; END IF;
    IF ref.fn_limit_pct('sz.000001','2025-03-10')<>0.05 THEN RAISE EXCEPTION 'ST 涨跌幅关联错误'; END IF;
    IF ref.fn_limit_pct('sz.000001','2025-03-17')<>0.10 THEN RAISE EXCEPTION '正常涨跌幅关联错误'; END IF;
END $$;
