-- 每个测试文件由 B 的运行器独立连接执行，结束整体回滚。
CREATE OR REPLACE FUNCTION pg_temp.expect_error(q text,pattern text) RETURNS void LANGUAGE plpgsql AS $$
DECLARE caught boolean:=false;
BEGIN
 BEGIN EXECUTE q; EXCEPTION WHEN OTHERS THEN
   IF SQLERRM NOT LIKE '%'||pattern||'%' THEN RAISE EXCEPTION '异常不符合预期：%',SQLERRM; END IF;
   caught:=true;
 END;
 IF NOT caught THEN RAISE EXCEPTION '预期异常未发生：%',q; END IF;
END $$;
DO $$
DECLARE u bigint; a bigint; oid bigint; q int; s int; frozen numeric;
BEGIN
 SELECT user_id INTO u FROM core.app_user WHERE username='demo_user';
 PERFORM set_config('app.user_id',u::text,true);
 a:=sim.fn_create_account(u,'C 规则测试','2025-05-13',100000);
 PERFORM pg_temp.expect_error(format('SELECT sim.fn_place_order(%s,''sh.600003'',''B'',''OPEN'',10000000,NULL)',a),'资金不足');
 PERFORM pg_temp.expect_error(format('SELECT sim.fn_place_order(%s,''sh.600003'',''B'',''OPEN'',1,NULL)',a),'整手');
 oid:=sim.fn_place_order(a,'sh.600003','B','OPEN',100,NULL);
 PERFORM pg_temp.expect_error(format('SELECT sim.fn_place_order(%s,''sh.600003'',''S'',''CLOSE'',100,NULL)',a),'T+1');
 PERFORM sim.fn_cancel_order(oid);
 SELECT frozen_cash INTO frozen FROM sim.account WHERE account_id=a;
 IF frozen<>0 THEN RAISE EXCEPTION '撤单没有释放冻结资金'; END IF;
 PERFORM pg_temp.expect_error(format('SELECT sim.fn_cancel_order(%s)',oid),'待成交');
 oid:=sim.fn_place_order(a,'sh.600003','B','OPEN',100,NULL);
 PERFORM sim.fn_advance_day(a);
 SELECT qty,sellable_qty INTO q,s FROM sim.position WHERE account_id=a AND stock_code='sh.600003';
 IF q<>100 OR s<>0 THEN RAISE EXCEPTION '当天新买持仓不应成为当天可卖：%/%',q,s; END IF;
 PERFORM pg_temp.expect_error(format('UPDATE sim.orders SET status=''PENDING'' WHERE order_id=%s',oid),'非法订单状态');
 PERFORM pg_temp.expect_error(format('UPDATE sim.fill SET price=price WHERE order_id=%s',oid),'只允许追加');
 PERFORM pg_temp.expect_error(format('DELETE FROM sim.cash_ledger WHERE account_id=%s',a),'只允许追加');
 -- 这笔卖单在次日成交，允许卖出昨天买入的股票。
 oid:=sim.fn_place_order(a,'sh.600003','S','OPEN',100,NULL);
 PERFORM pg_temp.expect_error(format('SELECT sim.fn_place_order(%s,''sh.600003'',''S'',''OPEN'',100,NULL)',a),'可卖持仓不足');
 PERFORM sim.fn_advance_day(a);
 SELECT qty INTO q FROM sim.position WHERE account_id=a AND stock_code='sh.600003';
 IF q<>0 THEN RAISE EXCEPTION '次日卖出没有扣减持仓'; END IF;
 IF EXISTS(SELECT 1 FROM sim.v_cash_reconcile WHERE account_id=a) THEN RAISE EXCEPTION '账户现金或冻结对账失败'; END IF;
 -- 已知停牌日拒单；前一天提交的订单在执行日停牌时也拒绝并释放冻结。
 a:=sim.fn_create_account(u,'停牌测试','2025-05-14',100000);
 oid:=sim.fn_place_order(a,'sh.600001','B','OPEN',100,NULL);
 PERFORM sim.fn_advance_day(a);
 IF (SELECT status FROM sim.orders WHERE order_id=oid)<>'REJECTED' THEN RAISE EXCEPTION '停牌订单未拒绝'; END IF;
 PERFORM pg_temp.expect_error(format('SELECT sim.fn_place_order(%s,''sh.600001'',''B'',''OPEN'',100,NULL)',a),'停牌');
 IF EXISTS(SELECT 1 FROM sim.v_cash_reconcile WHERE account_id=a) THEN RAISE EXCEPTION '停牌拒单对账失败'; END IF;
END $$;
