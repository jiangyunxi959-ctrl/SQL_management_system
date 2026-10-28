-- C：日频模拟账户。日终提交订单，下一交易日按 OPEN/CLOSE 撮合。
-- 所有账户写操作先锁账户，再锁订单/持仓；同一账户的并发写入串行化。
CREATE TABLE IF NOT EXISTS sim.account (
    account_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id bigint NOT NULL REFERENCES core.app_user,
    name text NOT NULL CHECK (btrim(name)<>''),
    as_of_date date NOT NULL,
    initial_cash numeric(20,2) NOT NULL CHECK(initial_cash>0 AND initial_cash<'Infinity'::numeric),
    cash numeric(20,2) NOT NULL CHECK(cash>=0 AND cash<'Infinity'::numeric),
    frozen_cash numeric(20,2) NOT NULL DEFAULT 0 CHECK(frozen_cash>=0 AND frozen_cash<=cash),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS sim.position (
    account_id bigint NOT NULL REFERENCES sim.account,
    stock_code varchar(12) NOT NULL REFERENCES ref.security,
    qty int NOT NULL DEFAULT 0 CHECK(qty>=0),
    sellable_qty int NOT NULL DEFAULT 0 CHECK(sellable_qty>=0 AND sellable_qty<=qty),
    frozen_qty int NOT NULL DEFAULT 0 CHECK(frozen_qty>=0 AND frozen_qty<=qty),
    PRIMARY KEY(account_id,stock_code)
);
CREATE TABLE IF NOT EXISTS sim.order_transition (
    old_status varchar(12), new_status varchar(12), PRIMARY KEY(old_status,new_status)
);
INSERT INTO sim.order_transition VALUES ('PENDING','FILLED'),('PENDING','CANCELLED'),('PENDING','REJECTED')
ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS sim.orders (
    order_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES sim.account,
    stock_code varchar(12) NOT NULL REFERENCES ref.security,
    side char(1) NOT NULL CHECK(side IN ('B','S')),
    exec_point varchar(8) NOT NULL CHECK(exec_point IN ('OPEN','CLOSE')),
    qty int NOT NULL CHECK(qty>0 AND qty%100=0),
    limit_price numeric(14,2) CHECK(limit_price>0 AND limit_price<'Infinity'::numeric),
    submitted_date date NOT NULL,
    exec_date date NOT NULL CHECK(exec_date>submitted_date),
    reserved_cash numeric(20,2) NOT NULL DEFAULT 0 CHECK(reserved_cash>=0),
    status varchar(12) NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','FILLED','CANCELLED','REJECTED')),
    reason text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS orders_pending ON sim.orders(account_id,exec_date,exec_point,order_id) WHERE status='PENDING';
CREATE TABLE IF NOT EXISTS sim.fill (
    fill_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id bigint NOT NULL UNIQUE REFERENCES sim.orders,
    trade_date date NOT NULL,
    qty int NOT NULL CHECK(qty>0),
    price numeric(14,2) NOT NULL CHECK(price>0),
    amount numeric(20,2) NOT NULL CHECK(amount>0),
    fee numeric(20,2) NOT NULL CHECK(fee>=0),
    CHECK(amount=qty*price)
);
CREATE TABLE IF NOT EXISTS sim.cash_ledger (
    ledger_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES sim.account,
    order_id bigint UNIQUE REFERENCES sim.orders,
    event_date date NOT NULL,
    kind varchar(12) NOT NULL CHECK(kind IN ('INITIAL','BUY','SELL')),
    delta numeric(20,2) NOT NULL,
    balance_after numeric(20,2) NOT NULL CHECK(balance_after>=0),
    CHECK((kind='INITIAL' AND order_id IS NULL AND delta>0) OR
          (kind='BUY' AND order_id IS NOT NULL AND delta<0) OR
          (kind='SELL' AND order_id IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS ledger_initial ON sim.cash_ledger(account_id) WHERE kind='INITIAL';

CREATE OR REPLACE FUNCTION sim.trg_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION '成交与资金流水只允许追加'; END $$;
DROP TRIGGER IF EXISTS immutable ON sim.fill;
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON sim.fill FOR EACH ROW EXECUTE FUNCTION sim.trg_immutable();
DROP TRIGGER IF EXISTS immutable ON sim.cash_ledger;
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON sim.cash_ledger FOR EACH ROW EXECUTE FUNCTION sim.trg_immutable();
CREATE OR REPLACE FUNCTION sim.trg_order_state() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,sim AS $$
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.status<>'PENDING' THEN RAISE EXCEPTION '订单初态必须为 PENDING'; END IF;
    ELSE
        IF (to_jsonb(NEW)-'status'-'reason') IS DISTINCT FROM (to_jsonb(OLD)-'status'-'reason') THEN
            RAISE EXCEPTION '已提交订单的参数不可更改';
        END IF;
        IF NOT EXISTS(SELECT 1 FROM sim.order_transition WHERE old_status=OLD.status AND new_status=NEW.status) THEN
            RAISE EXCEPTION '非法订单状态转换：% -> %',OLD.status,NEW.status;
        END IF;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS order_state ON sim.orders;
CREATE TRIGGER order_state BEFORE INSERT OR UPDATE ON sim.orders FOR EACH ROW EXECUTE FUNCTION sim.trg_order_state();

CREATE OR REPLACE FUNCTION sim.require_user() RETURNS bigint LANGUAGE plpgsql STABLE AS $$
DECLARE u bigint := nullif(current_setting('app.user_id',true),'')::bigint;
BEGIN
    IF u IS NULL THEN RAISE EXCEPTION '请先设置 app.user_id' USING ERRCODE='42501'; END IF;
    RETURN u;
END $$;
CREATE OR REPLACE FUNCTION sim.lock_account(p_id bigint) RETURNS sim.account
LANGUAGE plpgsql SET search_path=pg_catalog,sim AS $$
DECLARE a sim.account;
BEGIN
    SELECT * INTO a FROM sim.account WHERE account_id=p_id AND owner_id=sim.require_user() FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '账户不存在或不属于当前用户' USING ERRCODE='42501'; END IF;
    RETURN a;
END $$;
CREATE OR REPLACE FUNCTION sim.fn_create_account(p_owner_id bigint,p_name text,p_start_date date,p_cash numeric)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,sim,ref AS $$
DECLARE n bigint;
BEGIN
    IF p_owner_id IS DISTINCT FROM sim.require_user() THEN RAISE EXCEPTION '不能为其他用户开户' USING ERRCODE='42501'; END IF;
    IF NOT EXISTS(SELECT 1 FROM ref.trading_calendar WHERE cal_date=p_start_date AND is_open) THEN
        RAISE EXCEPTION '账户起始日必须是已知交易日';
    END IF;
    INSERT INTO sim.account(owner_id,name,as_of_date,initial_cash,cash)
    VALUES(p_owner_id,p_name,p_start_date,p_cash,p_cash) RETURNING account_id INTO n;
    INSERT INTO sim.cash_ledger(account_id,event_date,kind,delta,balance_after)
    VALUES(n,p_start_date,'INITIAL',p_cash,p_cash);
    RETURN n;
END $$;
CREATE OR REPLACE FUNCTION sim.fn_place_order(p_account_id bigint,p_stock_code varchar,p_side char(1),
    p_exec_point varchar,p_qty int,p_limit_price numeric)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,sim,ref,mkt AS $$
DECLARE a sim.account; b mkt.daily_bar; d date; r numeric:=0; n bigint; q int; pct numeric;
BEGIN
    a:=sim.lock_account(p_account_id);
    IF p_side IS NULL OR p_side NOT IN ('B','S') OR p_exec_point IS NULL OR p_exec_point NOT IN ('OPEN','CLOSE')
       OR p_qty IS NULL OR p_qty<=0 OR p_qty%100<>0 OR p_limit_price<=0 OR p_limit_price<>round(p_limit_price,2) THEN
        RAISE EXCEPTION '订单方向、执行点、整手数量或限价不合法';
    END IF;
    SELECT min(cal_date) INTO d FROM ref.trading_calendar WHERE is_open AND cal_date>a.as_of_date;
    IF d IS NULL THEN RAISE EXCEPTION '没有下一交易日'; END IF;
    SELECT * INTO b FROM mkt.daily_bar WHERE stock_code=p_stock_code AND trade_date=a.as_of_date;
    IF NOT FOUND THEN RAISE EXCEPTION '研究日行情缺失'; END IF;
    IF b.is_suspended THEN RAISE EXCEPTION '研究日停牌，拒绝下单'; END IF;
    pct:=ref.fn_limit_pct(p_stock_code,a.as_of_date);
    -- 只用提交日日终已知价格估算冻结额；执行日规则变化导致资金不足时拒绝成交。
    IF p_side='B' THEN
        r:=round(coalesce(p_limit_price,round(b.close*(1+pct),2))*p_qty,2);
        r:=r+ref.fn_fee(a.as_of_date,'B',r);
        IF a.cash-a.frozen_cash<r THEN RAISE EXCEPTION '可用资金不足'; END IF;
        UPDATE sim.account SET frozen_cash=frozen_cash+r WHERE account_id=p_account_id;
    ELSE
        -- 日终下单只会在次日执行，今天买入的股票届时已满足 T+1。
        SELECT qty-frozen_qty INTO q FROM sim.position WHERE account_id=p_account_id AND stock_code=p_stock_code FOR UPDATE;
        IF q IS NULL OR q<p_qty THEN RAISE EXCEPTION '可卖持仓不足，禁止卖出尚未成交的当日买单（T+1）'; END IF;
        UPDATE sim.position SET frozen_qty=frozen_qty+p_qty WHERE account_id=p_account_id AND stock_code=p_stock_code;
    END IF;
    INSERT INTO sim.orders(account_id,stock_code,side,exec_point,qty,limit_price,submitted_date,exec_date,reserved_cash)
    VALUES(p_account_id,p_stock_code,p_side,p_exec_point,p_qty,p_limit_price,a.as_of_date,d,r) RETURNING order_id INTO n;
    RETURN n;
END $$;
CREATE OR REPLACE FUNCTION sim.release_order(p_order bigint,p_status varchar,p_reason text) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog,sim AS $$
DECLARE o sim.orders;
BEGIN
    SELECT * INTO STRICT o FROM sim.orders WHERE order_id=p_order FOR UPDATE;
    IF o.status<>'PENDING' THEN RAISE EXCEPTION '订单不处于待成交状态'; END IF;
    IF o.side='B' THEN
        UPDATE sim.account SET frozen_cash=frozen_cash-o.reserved_cash WHERE account_id=o.account_id;
    ELSE
        UPDATE sim.position SET frozen_qty=frozen_qty-o.qty WHERE account_id=o.account_id AND stock_code=o.stock_code;
    END IF;
    UPDATE sim.orders SET status=p_status,reason=p_reason WHERE order_id=p_order;
END $$;
CREATE OR REPLACE FUNCTION sim.fn_cancel_order(p_order_id bigint) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,sim AS $$
DECLARE aid bigint;
BEGIN
    SELECT account_id INTO aid FROM sim.orders WHERE order_id=p_order_id;
    PERFORM sim.lock_account(aid);
    PERFORM sim.release_order(p_order_id,'CANCELLED','用户撤单');
END $$;
CREATE OR REPLACE FUNCTION sim.fn_advance_day(p_account_id bigint)
RETURNS TABLE(order_id bigint,status varchar,reason text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,sim,ref,mkt AS $$
DECLARE a sim.account; d date; o sim.orders; b mkt.daily_bar; p numeric; fee numeric; amount numeric;
        pct numeric; why text; cash_left numeric; available int; listed date; sessions int;
BEGIN
    a:=sim.lock_account(p_account_id);
    SELECT min(cal_date) INTO d FROM ref.trading_calendar WHERE is_open AND cal_date>a.as_of_date;
    IF d IS NULL THEN RAISE EXCEPTION '没有下一交易日'; END IF;
    -- 仅有复权因子无法区分现金分红与送转；禁止悄悄按错误股数撮合。
    IF EXISTS(SELECT 1 FROM sim.position p JOIN mkt.adj_factor f USING(stock_code)
              WHERE p.account_id=p_account_id AND p.qty>0 AND f.ex_date>a.as_of_date AND f.ex_date<=d) THEN
        RAISE EXCEPTION '持仓跨除权事件：需补充现金分红/送转明细后处理';
    END IF;
    IF EXISTS(SELECT 1 FROM sim.position p JOIN ref.security s USING(stock_code)
              WHERE p.account_id=p_account_id AND p.qty>0 AND s.delist_date<=d) THEN
        RAISE EXCEPTION '持仓已退市：需补充退市结算价格后处理';
    END IF;
    UPDATE sim.position SET sellable_qty=qty WHERE account_id=p_account_id;
    FOR o IN SELECT * FROM sim.orders x WHERE x.account_id=p_account_id AND x.status='PENDING'
             AND x.exec_date=d ORDER BY CASE x.exec_point WHEN 'OPEN' THEN 0 ELSE 1 END,x.order_id FOR UPDATE LOOP
        why:=NULL;
        SELECT * INTO b FROM mkt.daily_bar WHERE stock_code=o.stock_code AND trade_date=d;
        IF NOT FOUND THEN why:='执行日行情缺失';
        ELSIF b.is_suspended OR b.volume=0 THEN why:='执行日停牌或无成交量';
        ELSE
            SELECT list_date INTO listed FROM ref.security WHERE stock_code=o.stock_code
                AND list_date<=d AND (delist_date IS NULL OR delist_date>d);
            SELECT count(*) INTO sessions FROM ref.trading_calendar WHERE is_open AND cal_date BETWEEN listed AND d;
            IF listed IS NULL THEN why:='执行日未上市或已退市';
            ELSIF sessions<=5 THEN why:='样本撮合暂不支持上市前五个交易日'; END IF;
        END IF;
        IF why IS NULL THEN
            pct:=ref.fn_limit_pct(o.stock_code,d);
            p:=round(CASE o.exec_point WHEN 'OPEN' THEN b.open ELSE b.close END,2);
            IF o.side='B' AND p>=round(b.pre_close*(1+pct),2) THEN why:='涨停不买入';
            ELSIF o.side='S' AND p<=round(b.pre_close*(1-pct),2) THEN why:='跌停不卖出';
            ELSIF o.limit_price IS NOT NULL AND ((o.side='B' AND p>o.limit_price) OR (o.side='S' AND p<o.limit_price)) THEN
                why:='执行点价格未满足限价';
            END IF;
            amount:=p*o.qty; fee:=ref.fn_fee(d,o.side,amount);
            SELECT cash-frozen_cash+o.reserved_cash INTO cash_left FROM sim.account WHERE account_id=p_account_id;
            SELECT sellable_qty INTO available FROM sim.position WHERE account_id=p_account_id AND stock_code=o.stock_code;
            IF why IS NULL AND o.side='B' AND cash_left<amount+fee THEN why:='执行日资金不足'; END IF;
            IF why IS NULL AND o.side='S' AND coalesce(available,0)<o.qty THEN why:='T+1 可卖不足'; END IF;
            IF why IS NULL AND o.side='S' AND cash_left+amount-fee<0 THEN why:='卖出不足以支付费用'; END IF;
        END IF;
        IF why IS NOT NULL THEN
            PERFORM sim.release_order(o.order_id,'REJECTED',why);
        ELSE
            PERFORM sim.release_order(o.order_id,'FILLED','日频执行点成交');
            UPDATE sim.account SET cash=cash+CASE o.side WHEN 'B' THEN -amount-fee ELSE amount-fee END WHERE account_id=p_account_id;
            IF o.side='B' THEN
                INSERT INTO sim.position(account_id,stock_code,qty) VALUES(p_account_id,o.stock_code,o.qty)
                ON CONFLICT(account_id,stock_code) DO UPDATE SET qty=sim.position.qty+EXCLUDED.qty;
            ELSE
                UPDATE sim.position SET qty=qty-o.qty,sellable_qty=sellable_qty-o.qty
                WHERE account_id=p_account_id AND stock_code=o.stock_code;
            END IF;
            INSERT INTO sim.fill(order_id,trade_date,qty,price,amount,fee) VALUES(o.order_id,d,o.qty,p,amount,fee);
            INSERT INTO sim.cash_ledger(account_id,order_id,event_date,kind,delta,balance_after)
            SELECT p_account_id,o.order_id,d,CASE o.side WHEN 'B' THEN 'BUY' ELSE 'SELL' END,
                   CASE o.side WHEN 'B' THEN -amount-fee ELSE amount-fee END,cash
            FROM sim.account WHERE account_id=p_account_id;
        END IF;
    END LOOP;
    UPDATE sim.account SET as_of_date=d WHERE account_id=p_account_id;
    RETURN QUERY SELECT x.order_id,x.status,x.reason FROM sim.orders x WHERE x.account_id=p_account_id AND x.exec_date=d
        ORDER BY x.order_id;
END $$;

CREATE OR REPLACE VIEW sim.v_cash_reconcile WITH(security_invoker=true) AS
SELECT a.account_id,a.cash,coalesce(l.total,0) AS ledger_cash,a.frozen_cash,coalesce(o.total,0) AS order_frozen
FROM sim.account a
LEFT JOIN (SELECT account_id,sum(delta) total FROM sim.cash_ledger GROUP BY account_id) l USING(account_id)
LEFT JOIN (SELECT account_id,sum(reserved_cash) total FROM sim.orders WHERE status='PENDING' GROUP BY account_id) o USING(account_id)
WHERE a.cash<>coalesce(l.total,0) OR a.frozen_cash<>coalesce(o.total,0);

-- 函数校验账户归属；读表使用 RLS。D 可在此基础上统一安全配置。
DO $$ DECLARE t text; f record; BEGIN
    FOREACH t IN ARRAY ARRAY['account','position','orders','fill','cash_ledger','order_transition'] LOOP
        EXECUTE format('ALTER TABLE sim.%I OWNER TO quant_owner',t);
        EXECUTE format('COMMENT ON TABLE sim.%I IS %L',t,'C 日频模拟交易：'||t||'；接口与口径见 README_C.md');
    END LOOP;
    FOR f IN SELECT p.oid::regprocedure AS sig FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='sim' LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO quant_owner',f.sig);
        EXECUTE format('COMMENT ON FUNCTION %s IS %L',f.sig,'C 交易函数；统一账户锁和事务，见 README_C.md');
    END LOOP;
END $$;
ALTER VIEW sim.v_cash_reconcile OWNER TO quant_owner;
COMMENT ON VIEW sim.v_cash_reconcile IS '异常对账行：现金与流水合计、冻结额与待执行买单应一致；正常为空。';
ALTER TABLE sim.account ENABLE ROW LEVEL SECURITY;
ALTER TABLE sim.position ENABLE ROW LEVEL SECURITY;
ALTER TABLE sim.orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE sim.fill ENABLE ROW LEVEL SECURITY;
ALTER TABLE sim.cash_ledger ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS own_account ON sim.account;
CREATE POLICY own_account ON sim.account USING(owner_id=sim.require_user());
DROP POLICY IF EXISTS own_position ON sim.position;
CREATE POLICY own_position ON sim.position USING(account_id IN (SELECT account_id FROM sim.account));
DROP POLICY IF EXISTS own_orders ON sim.orders;
CREATE POLICY own_orders ON sim.orders USING(account_id IN (SELECT account_id FROM sim.account));
DROP POLICY IF EXISTS own_fill ON sim.fill;
CREATE POLICY own_fill ON sim.fill USING(order_id IN (SELECT order_id FROM sim.orders));
DROP POLICY IF EXISTS own_ledger ON sim.cash_ledger;
CREATE POLICY own_ledger ON sim.cash_ledger USING(account_id IN (SELECT account_id FROM sim.account));
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA sim FROM PUBLIC;
GRANT USAGE ON SCHEMA sim TO app_user;
GRANT SELECT ON sim.account,sim.position,sim.orders,sim.fill,sim.cash_ledger,sim.v_cash_reconcile TO app_user;
GRANT EXECUTE ON FUNCTION sim.require_user(),sim.fn_create_account(bigint,text,date,numeric),
 sim.fn_place_order(bigint,varchar,char,varchar,int,numeric),sim.fn_cancel_order(bigint),sim.fn_advance_day(bigint) TO app_user;

DO $$ DECLARE c record; BEGIN
    FOR c IN SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='sim' LOOP
        EXECUTE format('COMMENT ON COLUMN sim.%I.%I IS %L',c.table_name,c.column_name,
                       'C '||c.column_name||'；金额元、数量股、日期交易日；字段口径见 README_C.md');
    END LOOP;
END $$;

-- 面向数据库客户端的逐字段口径。
COMMENT ON COLUMN sim.account.account_id IS '模拟账户主键；所有账户写操作先取得该账户行锁。';
COMMENT ON COLUMN sim.account.owner_id IS '所属应用用户；必须匹配事务内 app.user_id。';
COMMENT ON COLUMN sim.account.name IS '账户名称，不能为空。';
COMMENT ON COLUMN sim.account.as_of_date IS '账户已经完成撮合的最后一个交易日日终；新订单在下一交易日执行。';
COMMENT ON COLUMN sim.account.initial_cash IS '开户本金，人民币元，固定两位小数。';
COMMENT ON COLUMN sim.account.cash IS '已结算资金总额，包含冻结金额，人民币元。';
COMMENT ON COLUMN sim.account.frozen_cash IS '待执行买单冻结总额；不得超过资金余额。';
COMMENT ON COLUMN sim.account.created_at IS '记录创建时间，含时区。';
COMMENT ON COLUMN sim.position.account_id IS '模拟账户主键；所有账户写操作先取得该账户行锁。';
COMMENT ON COLUMN sim.position.stock_code IS '证券代码，引用 A 的证券主表，格式如 sh.600003。';
COMMENT ON COLUMN sim.position.qty IS '股票数量，单位股；订单按 100 股整手，持仓可为零。';
COMMENT ON COLUMN sim.position.sellable_qty IS '当前交易日已解锁数量；当日新买入数量在下一次日初结算时解锁。';
COMMENT ON COLUMN sim.position.frozen_qty IS '次日待执行卖单冻结数量；不得超过持仓总数。';
COMMENT ON COLUMN sim.order_transition.old_status IS '允许的状态转换起点。';
COMMENT ON COLUMN sim.orders.order_id IS '订单主键；成交和非开户资金流水关联到该订单。';
COMMENT ON COLUMN sim.orders.account_id IS '模拟账户主键；所有账户写操作先取得该账户行锁。';
COMMENT ON COLUMN sim.orders.stock_code IS '证券代码，引用 A 的证券主表，格式如 sh.600003。';
COMMENT ON COLUMN sim.orders.side IS '买卖方向：B 买入，S 卖出。';
COMMENT ON COLUMN sim.orders.exec_point IS '下一交易日执行点：OPEN 开盘，CLOSE 收盘。';
COMMENT ON COLUMN sim.orders.qty IS '股票数量，单位股；订单按 100 股整手，持仓可为零。';
COMMENT ON COLUMN sim.orders.limit_price IS '可选限价，人民币元；NULL 按执行点价格；仅判断该执行点，不扫描日内路径。';
COMMENT ON COLUMN sim.orders.submitted_date IS '提交订单时已经结束的研究日。';
COMMENT ON COLUMN sim.orders.exec_date IS '计划执行的下一交易日，必须晚于提交日。';
COMMENT ON COLUMN sim.orders.reserved_cash IS '该买单冻结的资金与预估费用；卖单为零。';
COMMENT ON COLUMN sim.orders.status IS '记录状态；合法取值由 CHECK 和状态机约束。';
COMMENT ON COLUMN sim.orders.reason IS '成交、拒单或撤单的说明。';
COMMENT ON COLUMN sim.orders.created_at IS '记录创建时间，含时区。';
COMMENT ON COLUMN sim.fill.fill_id IS '成交记录主键。';
COMMENT ON COLUMN sim.fill.order_id IS '订单主键；成交和非开户资金流水关联到该订单。';
COMMENT ON COLUMN sim.fill.trade_date IS '交易日；净值表为该日日终估值。';
COMMENT ON COLUMN sim.fill.qty IS '股票数量，单位股；订单按 100 股整手，持仓可为零。';
COMMENT ON COLUMN sim.fill.price IS '执行点价格，人民币元，四舍五入至分。';
COMMENT ON COLUMN sim.fill.amount IS '成交金额，等于成交价格乘股数，人民币元。';
COMMENT ON COLUMN sim.fill.fee IS '调用 A 的历史费率函数逐项四舍五入得到的总费用，人民币元。';
COMMENT ON COLUMN sim.cash_ledger.ledger_id IS '资金流水追加序号。';
COMMENT ON COLUMN sim.cash_ledger.account_id IS '模拟账户主键；所有账户写操作先取得该账户行锁。';
COMMENT ON COLUMN sim.cash_ledger.order_id IS '订单主键；成交和非开户资金流水关联到该订单。';
COMMENT ON COLUMN sim.cash_ledger.event_date IS '资金变动日期。';
COMMENT ON COLUMN sim.cash_ledger.kind IS '资金变动类型：INITIAL 入金、BUY 买入、SELL 卖出。';
COMMENT ON COLUMN sim.cash_ledger.delta IS '资金变动额；正数流入，负数流出，人民币元。';
COMMENT ON COLUMN sim.cash_ledger.balance_after IS '该条变动完成后的资金余额，人民币元。';
