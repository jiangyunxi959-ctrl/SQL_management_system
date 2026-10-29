"""对本人一次已保存运行作截面统计；聚合与分位数在数据库中完成。"""
from psycopg.rows import dict_row

from app.db import transaction

_SUMMARY = """
WITH vals AS (
    SELECT *, quality='OK' AND value IS NOT NULL
        AND value NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric) AS valid
    FROM factor.value WHERE run_id=%s
), good AS (SELECT value FROM vals WHERE valid),
reason_rows AS (
    SELECT DISTINCT stock_code, trade_date, reason
    FROM vals CROSS JOIN LATERAL unnest(
        CASE WHEN quality='OK' AND NOT valid THEN ARRAY['NON_FINITE_RESULT']::text[]
             ELSE reason_codes END) AS reasons(reason)
    WHERE NOT valid
), counts AS (SELECT reason,count(*) AS n FROM reason_rows GROUP BY reason)
SELECT (SELECT count(*) FROM vals) AS total_count,
       count(*) AS valid_count,
       (SELECT count(*) FROM vals)-count(*) AS missing_count,
       avg(value) AS mean, stddev_samp(value) AS stddev,
       min(value) AS minimum, max(value) AS maximum,
       percentile_cont(ARRAY[0.05,0.25,0.5,0.75,0.95])
           WITHIN GROUP (ORDER BY value::double precision) AS quantiles,
       count(*) FILTER (WHERE value>0) AS positive_count,
       count(*) FILTER (WHERE value<0) AS negative_count,
       count(*) FILTER (WHERE value=0) AS zero_count,
       coalesce((SELECT jsonb_object_agg(reason,n) FROM counts),'{}'::jsonb) AS reason_counts
FROM good
"""


def summarize_run(identity, run_id: int) -> dict:
    if type(run_id) is not int or run_id < 1:
        raise ValueError('运行编号不合法')
    with transaction(user_id=identity.user_id, read_only=True, isolation='repeatable_read', timeout_ms=5000) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""SELECT r.run_id,r.as_of,r.universe,r.factor_id,r.version_no,d.name AS factor_name,
                                  v.unit_label
                           FROM factor.run r JOIN factor.definition d USING(factor_id)
                           JOIN factor.version v USING(factor_id,version_no)
                           WHERE r.run_id=%s AND r.status='SUCCEEDED'""", [run_id])
            result = cur.fetchone()
            if result is None:
                raise ValueError('运行不存在或无权查看')
            cur.execute(_SUMMARY, [run_id])
            result.update(cur.fetchone())
    quantiles = result.pop('quantiles') or [None]*5
    result.update(zip(('p05','p25','median','p75','p95'), quantiles))
    result['valid_ratio'] = (result['valid_count']/result['total_count']) if result['total_count'] else None
    return result
