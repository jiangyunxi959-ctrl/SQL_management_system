DO $$
DECLARE b bigint; f bigint; n int; code text;
BEGIN
 SELECT stock_code INTO code FROM ref.security WHERE list_date<'2025-01-01' ORDER BY stock_code LIMIT 1;
 INSERT INTO etl.load_batch(source,target) VALUES ('TEST','mkt.daily_bar') RETURNING batch_id INTO b;
 INSERT INTO staging.daily_bar_raw(batch_id,date,code,open,high,low,close,preclose,volume,amount,adjustflag,tradestatus) VALUES
 (b,'2025-01-02',code,'10','11','9','10','10','100','100','3','1'),
 (b,'2025-01-03',code,'NaN','NaN','NaN','NaN','10','100','100','3','1'),
 (b,'2025-01-06',code,'10','11','9','10','10','100','NaN','3','1'),
 (b,'2025-01-07',code,'10','11','9','10','bad','100','100','3','1'),
 (b,'2025-01-08',code,'10','11','9','10','-1','100','100','3','1'),
 (b,'2025-01-09',code,'-1',NULL,NULL,'10','10','0','0','3','0'),
 (b,'2025-01-10',code,'10','11','9','10','10','100','100','2','1'),
 (b,'2025-01-13',code,'10','11','9','10','10','100','100','3','1'),
 (b,'2025-1-13',code,'10','11','9','10','10','100','100','3','1'),
 (b,'2025-01-14',code,'10','11','9','10','10','100','100','3','1'),
 (b,'2025-01-14',code,'10','11','9','10','10','100','100','3','1'),
 (b,'2025-01-15',code,'10','11','9','10','10','100','100','3','1'),
 (b,'2025-01-16',code,'10','11','9','10','10','100','Infinity','3','1'),
 (b,'2025-01-17',code,'10','11','9','10','0.00001','100','100','3','1'),
 (b,'2025-01-20',code,'10','11','9','10','10','100','9999999999999999999999','3','1'),
 (b,'2025-01-21',code,NULL,NULL,NULL,'10',NULL,'0','0','3','0'),
 (b,'2025-01-22',code,'garbage',NULL,NULL,'10',NULL,'0','0','3','0');
 PERFORM etl.fn_merge_daily_bar(b);
 SELECT count(*) INTO n FROM etl.reject_row WHERE batch_id=b;
 IF n<>14 THEN RAISE EXCEPTION 'expected 14 rejected daily rows, got %',n; END IF;
 SELECT count(*) INTO n FROM mkt.daily_bar WHERE stock_code=code AND trade_date IN ('2025-01-02','2025-01-15','2025-01-21') AND close=10;
 IF n<>3 THEN RAISE EXCEPTION 'valid rows not loaded %', n; END IF;
 INSERT INTO etl.load_batch(source,target) VALUES ('TEST','mkt.adj_factor') RETURNING batch_id INTO f;
 INSERT INTO staging.adjust_factor_raw(batch_id,code,"dividOperateDate","backAdjustFactor") VALUES
 (f,code,'2026-01-01','1.1'),(f,code,'2026-01-02','NaN'),(f,code,'2026-01-03','Infinity'),
 (f,code,'2026-01-04','-Infinity'),(f,code,'2026-01-05','0.000000001'),(f,code,'2026-01-06','99999999999999999999'),
 (f,code,'2026-01-07','1.2'),(f,code,'2026-1-7','1.2'),(f,code,'2026-01-08','1.2'),(f,code,'2026-01-08','1.2');
 PERFORM etl.fn_merge_adj_factor(f);
 SELECT count(*) INTO n FROM etl.reject_row WHERE batch_id=f;
 IF n<>9 THEN RAISE EXCEPTION 'expected 9 rejected factor rows, got %',n; END IF;
 IF NOT EXISTS (SELECT 1 FROM mkt.adj_factor WHERE stock_code=code AND ex_date='2026-01-01' AND back_factor=1.1) THEN RAISE EXCEPTION 'valid factor not loaded'; END IF;
END $$;
