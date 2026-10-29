import pytest

from app.db import transaction
from app.factors.catalog import PUBLIC_FACTORS, register_public
from app.factors.service import copy_public, create_private, list_definitions
from app.setup_d import install


def test_public_catalog_is_visible_and_repeat_install_does_not_duplicate(identities):
    identity, _token = identities[0]
    other, _other_token = identities[1]
    rows = list_definitions(identity)
    codes = {row[2] for row in rows if row[1] == "SYSTEM_PUBLIC"}
    assert {item["code"] for item in PUBLIC_FACTORS} <= codes
    assert all(row[5] == 1 for row in rows if row[1] == "SYSTEM_PUBLIC" and row[2] in {item["code"] for item in PUBLIC_FACTORS})
    install()
    again = list_definitions(other)
    again_codes = {row[2] for row in again if row[1] == "SYSTEM_PUBLIC"}
    assert {item["code"] for item in PUBLIC_FACTORS} <= again_codes
    with pytest.raises(Exception):
        with transaction(user_id=identity.user_id) as conn:
            conn.execute(
                "UPDATE factor.definition SET name='被改掉' WHERE visibility='SYSTEM_PUBLIC'"
            )


def test_private_formula_is_hidden_from_the_other_user(identities):
    owner, _token = identities[0]
    other, _other_token = identities[1]
    factor_id = create_private(owner, "我的均价", "amount / volume")
    copied = copy_public(owner, _public_id(owner, "VWAP_D"), 1, "复制均价")
    own = {(row[0], row[3]) for row in list_definitions(owner)}
    theirs = {(row[0], row[3]) for row in list_definitions(other)}
    assert (factor_id, "我的均价") in own
    assert (copied, "复制均价") in own
    assert factor_id not in {item[0] for item in theirs}
    assert copied not in {item[0] for item in theirs}
    with transaction(user_id=other.user_id, read_only=True) as conn:
        assert conn.execute(
            "SELECT expression FROM factor.version WHERE factor_id=%s", (factor_id,)
        ).fetchall() == []


def _public_id(identity, code):
    return next(row[0] for row in list_definitions(identity) if row[2] == code)
