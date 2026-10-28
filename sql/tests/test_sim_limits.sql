DO $$
DECLARE u bigint; a bigint; oid bigint; f numeric;
BEGIN
 SELECT user_id INTO u FROM core.app_user WHERE username='demo_user';
 PERFORM set_config('app.user_id',u::text,true);
 a:=sim.fn_create_account(u,'涨跌停测试','2025-05-12',100000);
 oid:=sim.fn_place_order(a,'sh.600003','B','OPEN',100,NULL);
 UPDATE mkt.daily_bar SET pre_close=round(open/1.11,2) WHERE stock_code='sh.600003' AND trade_date='2025-05-13';
 PERFORM sim.fn_advance_day(a);
 IF NOT EXISTS(SELECT 1 FROM sim.orders WHERE order_id=oid AND status='REJECTED' AND reason LIKE '涨停%') THEN
   RAISE EXCEPTION '涨停买入没有拒绝'; END IF;
 oid:=sim.fn_place_order(a,'sh.600003','B','OPEN',100,NULL);
 PERFORM sim.fn_advance_day(a);
 oid:=sim.fn_place_order(a,'sh.600003','S','OPEN',100,NULL);
 UPDATE mkt.daily_bar SET pre_close=round(open/0.89,2) WHERE stock_code='sh.600003' AND trade_date='2025-05-15';
 PERFORM sim.fn_advance_day(a);
 IF NOT EXISTS(SELECT 1 FROM sim.orders WHERE order_id=oid AND status='REJECTED' AND reason LIKE '跌停%') THEN
   RAISE EXCEPTION '跌停卖出没有拒绝'; END IF;
 IF (SELECT frozen_qty FROM sim.position WHERE account_id=a AND stock_code='sh.600003')<>0 THEN
   RAISE EXCEPTION '跌停拒单没有释放持仓'; END IF;
 IF EXISTS(SELECT 1 FROM sim.v_cash_reconcile WHERE account_id=a) THEN RAISE EXCEPTION '涨跌停对账失败'; END IF;
 -- 限价订单不应按不利价格成交。
 oid:=sim.fn_place_order(a,'sh.600004','B','OPEN',100,0.01);
 PERFORM sim.fn_advance_day(a);
 IF NOT EXISTS(SELECT 1 FROM sim.orders WHERE order_id=oid AND status='REJECTED' AND reason LIKE '%限价%') THEN
   RAISE EXCEPTION '限价未生效'; END IF;
END $$;
