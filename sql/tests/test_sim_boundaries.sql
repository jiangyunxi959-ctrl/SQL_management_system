DO $$
DECLARE u bigint; a bigint; oid bigint; failed boolean;
BEGIN
 SELECT user_id INTO u FROM core.app_user WHERE username='demo_user';
 PERFORM set_config('app.user_id',u::text,true);
 failed:=false;
 BEGIN
   PERFORM sim.fn_create_account(u,'非法金额','2025-05-13','NaN'::numeric);
 EXCEPTION WHEN check_violation THEN failed:=true;
 END;
 IF NOT failed THEN RAISE EXCEPTION 'NaN 不能作为初始资金'; END IF;
 a:=sim.fn_create_account(u,'除权边界','2025-06-12',100000);
 oid:=sim.fn_place_order(a,'sh.600001','B','OPEN',100,NULL);
 PERFORM sim.fn_advance_day(a);
 failed:=false;
 BEGIN
   PERFORM sim.fn_advance_day(a);
 EXCEPTION WHEN raise_exception THEN
   IF SQLERRM NOT LIKE '%除权事件%' THEN RAISE; END IF;
   failed:=true;
 END;
 IF NOT failed THEN RAISE EXCEPTION '没有公司行动明细时应停止跨除权估值'; END IF;
 IF (SELECT as_of_date FROM sim.account WHERE account_id=a)<>'2025-06-13'::date THEN RAISE EXCEPTION '失败推进未回滚'; END IF;
 IF EXISTS(SELECT 1 FROM sim.v_cash_reconcile WHERE account_id=a) THEN RAISE EXCEPTION '失败操作破坏对账'; END IF;
END $$;
