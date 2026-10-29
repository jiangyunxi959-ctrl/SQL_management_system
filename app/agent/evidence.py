"""核对模型引用的数值。语义不能完全自动验证，所以不能称为零幻觉。"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation

MISSING = object()


def parse_draft(content: str) -> dict | None:
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        draft = json.loads(text)
    except json.JSONDecodeError:
        # Some tool-enabled provider responses contain literal newlines inside
        # JSON strings. This only relaxes control-character escaping, never eval.
        try:
            draft = json.loads(text, strict=False)
        except json.JSONDecodeError:
            return None
    if not isinstance(draft, dict) or not isinstance(draft.get("answer"), str):
        return None
    draft.setdefault("citations", [])
    draft.setdefault("claims", [])
    draft.setdefault("expression_draft", None)
    draft.setdefault("limitations", [])
    return draft


def check_draft(draft: dict, evidence: dict) -> tuple[bool, str]:
    if not isinstance(draft.get("claims"), list) or not isinstance(draft.get("citations"), list):
        return False, "回答结构不完整"
    if any(not isinstance(ref,str) or ref not in evidence for ref in draft['citations']):
        return False, "引用了本回合不存在的依据"
    if any(ref not in draft['citations'] for ref in re.findall(r'\[(E\d+)\]',draft['answer'])):
        return False, "文字引用未列入依据"
    for claim in draft["claims"]:
        if not isinstance(claim, dict):
            return False, "数值声明格式不正确"
        item = evidence.get(claim.get("evidence_id"))
        if item is None:
            return False, "引用了本回合不存在的依据"
        actual = _lookup(item, str(claim.get("path") or ""))
        if actual is MISSING and isinstance(claim.get('path'),str):
            # Accept an omitted data prefix only when the exact field exists;
            # persist the canonical path so the checked reference is explicit.
            canonical = 'data.'+claim['path']
            actual = _lookup(item,canonical)
            if actual is not MISSING:
                claim['path'] = canonical
        if actual is MISSING:
            return False, "引用字段不存在"
        if not _same(actual, claim.get("raw_value")):
            return False, f"引用{claim.get('evidence_id')}的{claim.get('path')}实际为{actual}，不是{claim.get('raw_value')}；修正索引、引用字段或原值，不得改写事实"
        if claim.get("display_unit") not in {"raw","count","percent"}:
            return False, "展示单位无效"
        if claim.get("display_unit") == "percent" and (
            not _percent_field(item,claim.get('path','')) or not _percent_ok(claim.get("raw_value"))
        ):
            return False, "百分比必须由小数换算"
    numerical_results=any(item.get('context',{}).get('tool') in {
        'run_factor','factor_values','factor_statistics','compare_factor_runs','price_history','fundamentals','screen',
        'evaluation_report','compare_evaluations','estimate_strategy_cost','backtest_report','financial_report_context'
    } for item in evidence.values())
    if _has_number(draft["answer"]) and not draft["claims"] and numerical_results:
        return False, "文字中的数字没有对应依据"
    formula = draft.get('expression_draft')
    if formula:
        if not isinstance(formula,dict):
            return False, "表达式草稿结构不正确"
        ref = evidence.get(formula.get('validation_evidence_id'),{})
        if not ref.get('data',{}).get('ok') or ref.get('context',{}).get('tool') != 'validate_expression':
            return False, "表达式未通过检查"
        if ref['data'].get('expression') != formula.get('expression'):
            return False, "草稿与已检查的表达式不一致"
    return True, ""


def _lookup(payload: dict, path: str):
    if not re.fullmatch(r'\w+(?:(?:\.\w+)|(?:\[\d+\]))*',path):
        return MISSING
    path=re.sub(r'\[(\d+)\]',r'.\1',path)
    current = payload
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current,list) and part.isdigit() and int(part)<len(current):
            current = current[int(part)]
        else:
            return MISSING
    return current


def _same(actual, claimed) -> bool:
    if actual is None or claimed is None:
        return actual is None and claimed is None
    try:
        a,b=Decimal(str(actual)),Decimal(str(claimed))
        return a.is_finite() and b.is_finite() and abs(a-b) <= Decimal("0.000001")
    except (InvalidOperation, ValueError):
        return str(actual) == str(claimed)


def _percent_ok(raw) -> bool:
    try:
        Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return False
    return True


def _percent_field(payload,path):
    if path.endswith('.valid_ratio'):
        return True
    tool = (payload.get('context') or {}).get('tool')
    research_ratio = (
        tool == 'evaluation_report' and re.fullmatch(
            r'data\.(?:headline\.h(?:1|5|20)_positive_ratio|summary\.(?:1|5|20)\.positive_ratio)', path,
        )
    ) or (
        tool == 'compare_evaluations' and re.fullmatch(
            r'data\.(?:left|right)_headline\.h(?:1|5|20)_positive_ratio', path,
        )
    )
    if research_ratio:
        try:
            ratio = Decimal(str(_lookup(payload, path)))
            return ratio.is_finite() and Decimal('0') <= ratio <= Decimal('1')
        except (InvalidOperation, ValueError):
            return False
    parent,_,field=path.rpartition('.')
    stats=_lookup(payload,parent)
    return isinstance(stats,dict) and stats.get('unit_label')=='无单位' and field in {
        'mean','median','stddev','minimum','maximum','p05','p25','p75','p95'
    }


def _has_number(text: str) -> bool:
    return any(char.isdigit() for char in text)
