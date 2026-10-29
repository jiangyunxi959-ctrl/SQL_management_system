"""10 个公共因子。注册时走同一套校验，不因是种子而免检。"""
from __future__ import annotations

import json

from pathlib import Path

from psycopg.types.json import Json

from app.factors.validate import validate

ROOT = Path(__file__).resolve().parents[2]

SOURCE_DIGEST = "d0197d3f47acb0855b7f3d5a0276b955d901128b16a3d2e7fc4b0802ce54c676"
SOURCE_DOC = "通联日频因子说明"

PUBLIC_FACTORS = (
    {
        "code": "VWAP_D", "name": "当日成交均价", "expression": "amount / volume",
        "source_name": "VWAP_1D", "source_page": "39",
        "adaptation_note": "参考改编：用日成交额除以日成交股数。源文件公式缺失，不是供应商完整复现。",
    },
    {
        "code": "RET_INTRADAY_D", "name": "开盘至收盘收益", "expression": "close_raw / open_raw - 1",
        "source_name": "RET_INTRADAY", "source_page": "33",
        "adaptation_note": "参考改编：使用日开盘价，不冒充分钟 09:30 收盘起点。",
    },
    {
        "code": "RET_OVERNIGHT_D", "name": "昨收至今开收益", "expression": "open_hfq / lag(close_hfq,1) - 1",
        "source_name": "INTRADAY_MOMENTUM_0H", "source_page": "35",
        "adaptation_note": "参考改编：使用日开盘和同口径调整后昨收，不使用竞价虚拟价格。",
    },
    {
        "code": "RANGE_D", "name": "当天高低价幅度", "expression": "high_raw / low_raw - 1",
        "source_name": "AMP_STD", "source_page": "10",
        "adaptation_note": "主题参考改编：只是日高低价幅度，不是分钟振幅标准差。",
    },
    {
        "code": "VWAP20_D", "name": "20 日成交均价", "expression": "ts_sum(amount,20) / ts_sum(volume,20)",
        "source_name": None, "source_page": None,
        "adaptation_note": "项目补充。窗口含当天；跨除权事件不给出可比较数值。",
    },
    {
        "code": "VWAP_BIAS_D", "name": "收盘相对成交均价偏离", "expression": "close_raw / (amount / volume) - 1",
        "source_name": None, "source_page": None,
        "adaptation_note": "项目补充。原始价格口径一致，保存为小数。",
    },
    {
        "code": "VOLUME_REL20_D", "name": "相对此前20日的量比",
        "expression": "volume / ts_mean(lag(volume,1),20)",
        "source_name": None, "source_page": None,
        "adaptation_note": "项目补充。不是资料中的竞价量比，不沿用 VOLUME_RATIO 名称。",
    },
    {
        "code": "MOM20_D", "name": "20 日涨幅", "expression": "close_hfq / lag(close_hfq,20) - 1",
        "source_name": None, "source_page": None,
        "adaptation_note": "项目补充。与 B 的 20 日收益对照，不是供应商因子。",
    },
    {
        "code": "MA20_BIAS_D", "name": "相对20日均价偏离", "expression": "close_hfq / ts_mean(close_hfq,20) - 1",
        "source_name": None, "source_page": None,
        "adaptation_note": "项目补充。均价窗口含当天，可与 B 的均价对照。",
    },
    {
        "code": "VOL20_D", "name": "20 日波动程度",
        "expression": "ts_std(log(close_hfq / lag(close_hfq,1)),20) * sqrt(252)",
        "source_name": None, "source_page": None,
        "adaptation_note": "项目补充。20 个日对数收益的样本标准差换算到年度口径，与 B 对照。",
    },
)


class CatalogConflict(Exception):
    pass


def register_public(conn) -> int:
    """重复注册不新增；同版本内容不同则拒绝。返回公共定义数量。"""
    for item in PUBLIC_FACTORS:
        checked = validate(item["expression"])
        conn.execute(
            """
            INSERT INTO factor.definition(visibility, code, name)
            VALUES ('SYSTEM_PUBLIC', %s, %s)
            ON CONFLICT (code) WHERE visibility = 'SYSTEM_PUBLIC' DO NOTHING
            """,
            (item["code"], item["name"]),
        )
        factor_id = conn.execute(
            "SELECT factor_id FROM factor.definition WHERE code = %s",
            (item["code"],),
        ).fetchone()[0]
        existing = conn.execute(
            """
            SELECT content_digest, expression FROM factor.version
            WHERE factor_id = %s AND version_no = 1
            """,
            (factor_id,),
        ).fetchone()
        if existing:
            if existing[0] != checked.digest or existing[1] != item["expression"]:
                raise CatalogConflict(f"{item['code']} 的第 1 版内容与已安装版本不同")
            continue
        doc = SOURCE_DOC if item["source_name"] else "项目补充"
        conn.execute(
            """
            INSERT INTO factor.version(
                factor_id, version_no, expression, normalized_tree, syntax_version, content_digest,
                unit_label, unit_exponents, price_basis, lookback, fields, explanation, ca_sensitive,
                source_doc, source_name, source_page, adaptation_note, source_digest
            ) VALUES (
                %s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                factor_id, item["expression"], Json(checked.normalized), checked.syntax_version,
                checked.digest, checked.unit_label,
                Json({"p": checked.unit.p, "h": checked.unit.h, "q": checked.unit.q}),
                checked.price_basis, checked.lookback, list(checked.fields), checked.explanation,
                checked.ca_sensitive, doc, item["source_name"], item["source_page"],
                item["adaptation_note"], SOURCE_DIGEST if item["source_name"] else None,
            ),
        )
    return conn.execute(
        "SELECT count(*) FROM factor.definition WHERE visibility = 'SYSTEM_PUBLIC'"
    ).fetchone()[0]


ORIGINAL_CODES = tuple(item["code"] for item in PUBLIC_FACTORS)


def register_d2(conn) -> int:
    """登记新增 40 项。原有 10 项的表达式和摘要必须保持不变。"""
    path = ROOT / "docs" / "design" / "d2" / "public_factors_50.json"
    inventory = ROOT / "docs" / "design" / "d2" / "source_inventory_370.json"
    catalog = json.loads(path.read_text(encoding="utf-8"))
    if len(catalog["factors"]) != 50:
        raise CatalogConflict("候选清单不是 50 项")
    for item in catalog["factors"]:
        checked = validate(item["expression"])
        if checked.digest != item["digest"] or checked.lookback != item["lookback"]:
            raise CatalogConflict(f"{item['code']} 与设计清单不一致")
        existing = conn.execute(
            """
            SELECT v.content_digest, v.expression
            FROM factor.definition d
            JOIN factor.version v ON v.factor_id = d.factor_id AND v.version_no = 1
            WHERE d.code = %s AND d.visibility = 'SYSTEM_PUBLIC'
            """,
            (item["code"],),
        ).fetchone()
        if item["delivery"] == "EXISTING":
            if existing is None or existing[0] != item["digest"] or existing[1] != item["expression"]:
                raise CatalogConflict(f"原有公式 {item['code']} 不能被改写")
        elif existing is None:
            _insert_public(conn, item, checked)
        elif existing[0] != item["digest"] or existing[1] != item["expression"]:
            raise CatalogConflict(f"{item['code']} 的已登记内容与清单冲突")
        conn.execute(
            """
            INSERT INTO factor.public_meta(code, category, source_kind, delivery)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (code) DO NOTHING
            """,
            (item["code"], item["category"], item["source_kind"], item["delivery"]),
        )
    entries = json.loads(inventory.read_text(encoding="utf-8"))["entries"]
    if len(entries) != 370:
        raise CatalogConflict("来源清单不是 370 条")
    for entry in entries:
        conn.execute(
            """
            INSERT INTO factor.source_entry(source_id, document, code, name, page, source_line, status, review_note)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_id) DO NOTHING
            """,
            (
                entry["source_id"], entry["document"], entry.get("code"), entry.get("name"),
                entry.get("page"), entry.get("source_line"), entry["status"], entry.get("review_note"),
            ),
        )
    return conn.execute(
        "SELECT count(*) FROM factor.definition WHERE visibility = 'SYSTEM_PUBLIC'"
    ).fetchone()[0]


def _insert_public(conn, item, checked) -> None:
    conn.execute(
        """
        INSERT INTO factor.definition(visibility, code, name)
        VALUES ('SYSTEM_PUBLIC', %s, %s)
        ON CONFLICT (code) WHERE visibility = 'SYSTEM_PUBLIC' DO NOTHING
        """,
        (item["code"], item["name"]),
    )
    factor_id = conn.execute(
        "SELECT factor_id FROM factor.definition WHERE code = %s",
        (item["code"],),
    ).fetchone()[0]
    doc = SOURCE_DOC if item.get("source_name") else "项目补充"
    conn.execute(
        """
        INSERT INTO factor.version(
            factor_id, version_no, expression, normalized_tree, syntax_version, content_digest,
            unit_label, unit_exponents, price_basis, lookback, fields, explanation, ca_sensitive,
            source_doc, source_name, source_page, adaptation_note, source_digest
        ) VALUES (
            %s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        """,
        (
            factor_id, item["expression"], Json(checked.normalized), checked.syntax_version,
            checked.digest, checked.unit_label,
            Json({"p": checked.unit.p, "h": checked.unit.h, "q": checked.unit.q}),
            checked.price_basis, checked.lookback, list(checked.fields), checked.explanation,
            checked.ca_sensitive, doc, item.get("source_name"), item.get("source_page"),
            item["adaptation_note"], SOURCE_DIGEST if item.get("source_name") else None,
        ),
    )


def dump_seed(path) -> None:
    payload = []
    for item in PUBLIC_FACTORS:
        checked = validate(item["expression"])
        payload.append({**item, "digest": checked.digest, "lookback": checked.lookback, "unit": checked.unit_label})
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
