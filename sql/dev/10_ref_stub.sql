-- 仅供 B 在 A 的 10_ref.sql 尚未合并前自测。
-- 正式集成时 rebuild_db.py 会优先使用 sql/10_ref.sql，并跳过本文件。

CREATE TABLE IF NOT EXISTS ref.trading_calendar (
    cal_date date PRIMARY KEY,
    is_open  boolean NOT NULL
);
ALTER TABLE ref.trading_calendar OWNER TO quant_owner;
COMMENT ON TABLE ref.trading_calendar IS '开发夹具：交易日历；正式定义由 A 的 10_ref.sql 提供。';
COMMENT ON COLUMN ref.trading_calendar.cal_date IS '自然日。';
COMMENT ON COLUMN ref.trading_calendar.is_open IS '该日是否为交易日。';

CREATE TABLE IF NOT EXISTS ref.security (
    stock_code  varchar(12) PRIMARY KEY,
    exchange    char(2)    NOT NULL CHECK (exchange IN ('SH', 'SZ', 'BJ')),
    board       varchar(8) NOT NULL CHECK (board IN ('MAIN', 'GEM', 'STAR', 'BSE')),
    list_date   date       NOT NULL,
    delist_date date,
    CHECK (delist_date IS NULL OR delist_date > list_date)
);
ALTER TABLE ref.security OWNER TO quant_owner;
COMMENT ON TABLE ref.security IS '开发夹具：证券主表；正式定义由 A 的 10_ref.sql 提供。';
COMMENT ON COLUMN ref.security.stock_code IS '证券代码，如 sh.600000。';
COMMENT ON COLUMN ref.security.exchange IS '交易所：SH/SZ/BJ。';
COMMENT ON COLUMN ref.security.board IS '板块：MAIN/GEM/STAR/BSE。';
COMMENT ON COLUMN ref.security.list_date IS '上市日期。';
COMMENT ON COLUMN ref.security.delist_date IS '退市日期；未退市为空。';
