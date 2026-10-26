SET LOCAL ROLE agent_reader;
SELECT set_config('app.as_of_date','2025-05-01',true);
DO $$
BEGIN
    PERFORM * FROM pit.fn_fundamentals('sh.600001',4);
    PERFORM * FROM pit.fn_screen('SYNTH300','[]','roe',5);
    PERFORM * FROM pit.fn_industry_peers('sh.600001');
    BEGIN PERFORM * FROM fin.report; RAISE EXCEPTION 'Agent 能读取财务原表';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
    BEGIN PERFORM * FROM mkt.daily_bar; RAISE EXCEPTION 'Agent 能读取行情原表';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
    BEGIN PERFORM * FROM ref.security; RAISE EXCEPTION 'Agent 能读取参考原表';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
    BEGIN DELETE FROM pit.security; RAISE EXCEPTION 'Agent 能写 PIT';
    EXCEPTION WHEN insufficient_privilege OR object_not_in_prerequisite_state THEN NULL; END;
END $$;
RESET ROLE;
