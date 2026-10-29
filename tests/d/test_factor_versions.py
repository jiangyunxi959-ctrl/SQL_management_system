from concurrent.futures import ThreadPoolExecutor

from app.factors.service import copy_public, create_private, list_definitions, new_version, run_factor
from datetime import date


def test_new_version_does_not_change_the_old_run(identities):
    identity, _token = identities[0]
    factor_id = create_private(identity, "两版收益", "close_raw / open_raw - 1")
    first, _plan = run_factor(identity, factor_id, 1, "SYNTH300", date(2025, 5, 30))
    version = new_version(identity, factor_id, "close_hfq / open_hfq - 1")
    assert version == 2
    versions = [row[5] for row in list_definitions(identity) if row[0] == factor_id]
    assert versions == [1, 2]
    from app.db import transaction
    with transaction(user_id=identity.user_id, read_only=True) as conn:
        bound = conn.execute("SELECT version_no FROM factor.run WHERE run_id=%s", (first,)).fetchone()[0]
    assert bound == 1


def test_copy_does_not_follow_later_public_edits(identities):
    identity, _token = identities[0]
    public_id = next(row[0] for row in list_definitions(identity) if row[2] == "RANGE_D")
    copied = copy_public(identity, public_id, 1, "我的幅度")
    expression = next(row[6] for row in list_definitions(identity) if row[0] == copied)
    assert expression == "high_raw / low_raw - 1"


def test_concurrent_versions_are_unique(identities):
    identity, _token = identities[0]
    factor_id = create_private(identity, "并发版本", "volume")

    def add(_n):
        return new_version(identity, factor_id, "volume + volume")

    with ThreadPoolExecutor(max_workers=4) as pool:
        numbers = list(pool.map(add, range(4)))
    assert sorted(numbers) == [2, 3, 4, 5]
