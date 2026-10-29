"""IC、平均秩和分组。正式服务每天至少 10 个有效配对。"""
from __future__ import annotations

from decimal import Decimal


def pearson(xs, ys):
    pairs = [(Decimal(str(x)), Decimal(str(y))) for x, y in zip(xs, ys)]
    count = len(pairs)
    if count < 2:
        return None, "INSUFFICIENT_SAMPLE"
    mean_x = sum(item[0] for item in pairs) / count
    mean_y = sum(item[1] for item in pairs) / count
    var_x = sum((item[0] - mean_x) ** 2 for item in pairs)
    var_y = sum((item[1] - mean_y) ** 2 for item in pairs)
    if var_x == 0 or var_y == 0:
        return None, "ZERO_VARIANCE"
    cov = sum((item[0] - mean_x) * (item[1] - mean_y) for item in pairs)
    return cov / (var_x.sqrt() * var_y.sqrt()), None


def average_ranks(values):
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [Decimal(0)] * len(values)
    cursor = 0
    while cursor < len(values):
        end = cursor
        while end + 1 < len(values) and values[order[end + 1]] == values[order[cursor]]:
            end += 1
        rank = Decimal(cursor + 1 + end + 1) / 2
        for index in range(cursor, end + 1):
            ranks[order[index]] = rank
        cursor = end + 1
    return ranks


def rank_ic(xs, ys):
    if len(xs) < 2:
        return None, "INSUFFICIENT_SAMPLE"
    return pearson(average_ranks([Decimal(str(item)) for item in xs]), average_ranks([Decimal(str(item)) for item in ys]))


def official_ic(xs, ys, minimum=10):
    if len(xs) < minimum:
        return None, None, "INSUFFICIENT_SAMPLE"
    ic, reason = pearson(xs, ys)
    rank, rank_reason = rank_ic(xs, ys)
    return ic, rank, reason or rank_reason


def assign_groups(pairs, minimum_group=5):
    """pairs: (stock, factor). 低值在第 1 组。相同因子值不拆开。"""
    ordered = sorted(pairs, key=lambda item: (item[1], item[0]))
    count = len(ordered)
    if count < 15:
        return [], "SMALL_SAMPLE"
    groups = 5 if count >= 25 else 3
    blocks = []
    for stock, value in ordered:
        if blocks and blocks[-1][0] == value:
            blocks[-1][1].append(stock)
        else:
            blocks.append([value, [stock]])
    plan = _place(blocks, groups, minimum_group)
    if plan is None and groups == 5:
        groups = 3
        plan = _place(blocks, groups, minimum_group)
        if plan is None:
            return [], "TIED_GROUPS"
        return plan, "DOWNGRADED_TO_3"
    if plan is None:
        return [], "TIED_GROUPS"
    return plan, None


def _place(blocks, groups, minimum_group):
    sizes = [len(block[1]) for block in blocks]
    total = sum(sizes)
    target = Decimal(total) / Decimal(groups)
    chosen = None
    chosen_score = None

    def search(start, left, cuts):
        nonlocal chosen, chosen_score
        if left == 1:
            if total - sum(sizes[:start]) < minimum_group:
                return
            score = sum(
                (Decimal(sum(sizes[left_edge:right_edge])) - target) ** 2
                for left_edge, right_edge in zip([0, *cuts], [*cuts, len(sizes)])
            )
            if chosen_score is None or score < chosen_score:
                chosen_score = score
                chosen = cuts[:]
            return
        used = sum(sizes[:start])
        for end in range(start + 1, len(sizes)):
            group_size = sum(sizes[start:end])
            rest = total - used - group_size
            if group_size < minimum_group or rest < (left - 1) * minimum_group:
                continue
            cuts.append(end)
            search(end, left - 1, cuts)
            cuts.pop()

    search(0, groups, [])
    if chosen is None:
        return None
    cuts = chosen
    result = []
    previous = 0
    for group_no, end in enumerate(cuts + [len(sizes)], start=1):
        chosen = blocks[previous:end]
        stocks = [stock for _value, members in chosen for stock in members]
        result.append({
            "group_no": group_no,
            "lower": chosen[0][0],
            "upper": chosen[-1][0],
            "stocks": stocks,
            "count": len(stocks),
        })
        previous = end
    return result
