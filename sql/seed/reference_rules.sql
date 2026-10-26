-- 已核对的历史规则；区间表示本项目覆盖范围，不表示法规首次生效日期。
-- 只覆盖普通交易日。新股初期、复牌特殊情形由 C 的撮合层单独识别。
INSERT INTO ref.price_limit_rule(board,status_class,limit_pct,valid,source) VALUES
('MAIN','NORMAL',0.10,'[2024-01-01,2026-01-01)','深交所2023交易规则3.3节 https://docs.static.szse.cn/www/lawrules/rule/stock/trade/W020230217564423808793.pdf'),
-- 主板 ST 调整于 2026-07-06 生效；2025 年征求意见日不是生效日。
-- 生效说明：https://www.szse.cn/lawrules/service/share/t20260703_621505.html
('MAIN','ST',0.05,'[2024-01-01,2026-01-01)','深交所2023交易规则4.5.5 https://docs.static.szse.cn/www/lawrules/rule/stock/trade/W020230217564423808793.pdf'),
('GEM','NORMAL',0.20,'[2024-01-01,2026-01-01)','深交所2023交易规则 https://docs.static.szse.cn/www/lawrules/rule/stock/trade/W020230217564423808793.pdf'),
('GEM','ST',0.20,'[2024-01-01,2026-01-01)','深交所2023交易规则4.5.5 https://docs.static.szse.cn/www/lawrules/rule/stock/trade/W020230217564423808793.pdf'),
('STAR','NORMAL',0.20,'[2024-01-01,2026-01-01)','上交所科创板规则说明 https://star.sse.com.cn/listing/announcement/notification/c/c_20240412_10764653.shtml'),
('STAR','ST',0.20,'[2024-01-01,2026-01-01)','上交所科创板规则说明 https://star.sse.com.cn/listing/announcement/notification/c/c_20240412_10764653.shtml'),
('BSE','NORMAL',0.30,'[2024-01-01,2026-01-01)','北交所交易规则3.3.11 https://www.bse.cn/jygl_list/200010919.html'),
('BSE','ST',0.30,'[2024-01-01,2026-01-01)','北交所交易规则3.3.11 https://www.bse.cn/jygl_list/200010919.html')
ON CONFLICT DO NOTHING;

INSERT INTO ref.fee_rule(fee_type,side,rate,min_amount,valid,source) VALUES
('COMMISSION','B',0.0003,5,'[2024-01-01,2026-01-01)','账户假设：万三，最低5元，含经手/证管费、不含印花税/过户费；非统一法定佣金'),
('COMMISSION','S',0.0003,5,'[2024-01-01,2026-01-01)','账户假设：万三，最低5元，含经手/证管费、不含印花税/过户费；非统一法定佣金'),
('STAMP_DUTY','B',0,0,'[2024-01-01,2026-01-01)','证券交易印花税卖方征收 https://www.mof.gov.cn/zhengwuxinxi/caijingshidian/xinhuanet/202308/t20230830_3904782.htm'),
('STAMP_DUTY','S',0.0005,0,'[2024-01-01,2026-01-01)','2023-08-28起减半 https://jiangsu.chinatax.gov.cn/art/2023/8/28/art_8349_432258.html'),
('TRANSFER','B',0.00001,0,'[2024-01-01,2026-01-01)','2022-04-29起双向0.01千分比；中国银河证券投教 https://edu.chinastock.com.cn/yhwz/tzzjy/detail.jsp?id=7848614'),
('TRANSFER','S',0.00001,0,'[2024-01-01,2026-01-01)','2022-04-29起双向0.01千分比；中国银河证券投教 https://edu.chinastock.com.cn/yhwz/tzzjy/detail.jsp?id=7848614')
ON CONFLICT DO NOTHING;
