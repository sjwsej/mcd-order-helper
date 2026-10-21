#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""购物篮组合引擎：在约束下枚举「主食数量达标」的篮子，并按倾向排序。

约束（定义见 references/rules.md）：
    C1 主食数量 == 目标数量（推荐第 1/2 位对应「基准 N±1」两个档位）
    C2 饮料数 <= ceil(主食数 * 4/3)          # 可超主食数，但最多超 1/3
    C3 「不含主食的套餐」最多 2 个            # 仅品类多倾向生效
    C4 非主食小食/甜点件数 <= 主食数 + 2      # 防止无限堆叠

倾向：
    cheap    总价最低（同价看品类丰富度）
    variety  品类最多（不同食物种类数 → 食物总件数 → 价格）
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import combinations_with_replacement

from menu_model import ROLE_DESSERT, ROLE_DRINK, ROLE_SNACK, ROLE_STAPLE, Unit


# --------------------------------------------------------------------------- #
def drink_cap_for(staple_count: int) -> int:
    """C2：饮料上限 = 主食数 + 主食数的 1/3（向上取整）。"""
    if staple_count <= 0:
        return 0
    return max(staple_count, math.ceil(staple_count * 4 / 3))


def parse_preference(raw: str) -> str:
    t = (raw or "").strip().lower()
    if t in ("variety", "more", "品类多", "品类", "多", "diverse", "丰富"):
        return "variety"
    return "cheap"


def parse_spicy(raw: str) -> bool:
    """返回 True 表示要辣。"""
    t = (raw or "").strip().lower()
    return t in ("yes", "spicy", "true", "1", "辣", "要辣", "吃辣", "是")


# --------------------------------------------------------------------------- #
@dataclass
class Plan:
    items: list[tuple[Unit, int]] = field(default_factory=list)
    price: float = 0.0
    orig_price: float = 0.0
    staple: int = 0
    snack: int = 0
    drink: int = 0
    dessert: int = 0
    kinds: int = 0          # 不同单元贡献的食物种类数（同款只算一次）
    items_total: int = 0    # 食物总件数（同款按数量累加）
    distinct_sku: int = 0
    role_coverage: int = 0
    no_staple_combo: int = 0
    kids: int = 0           # 儿童餐份数
    total_units: int = 0
    verified_price: float | None = None
    verify_note: str = ""

    def codes(self) -> list[tuple[str, int]]:
        return [(u.code, q) for u, q in self.items]

    def to_dict(self) -> dict:
        return {
            "items": [
                {
                    "code": u.code,
                    "name": u.name,
                    "quantity": q,
                    "unitPrice": round(u.price, 2),
                    "subtotal": round(u.price * q, 2),
                    "role": u.role,
                    "isCombo": u.is_combo,
                    "spicy": u.spicy,
                    "requiresCard": u.requires_card,
                    "cardPrice": u.card_price,
                    "isKids": u.is_kids,
                    "provides": {
                        "staple": u.provides[0], "snack": u.provides[1],
                        "drink": u.provides[2], "dessert": u.provides[3],
                    },
                    "contents": list(u.default_contents),
                }
                for u, q in self.items
            ],
            "price": round(self.price, 2),
            "originalPrice": round(self.orig_price, 2),
            "save": round(max(0.0, self.orig_price - self.price), 2),
            "verifiedPrice": None if self.verified_price is None else round(self.verified_price, 2),
            "counts": {
                "staple": self.staple, "snack": self.snack,
                "drink": self.drink, "dessert": self.dessert,
            },
            "kinds": self.kinds,
            "itemsTotal": self.items_total,
            "distinctSku": self.distinct_sku,
            "roleCoverage": self.role_coverage,
            "noStapleCombo": self.no_staple_combo,
            "kidsMeals": self.kids,
            "verifyNote": self.verify_note,
        }


def _aggregate(items: list[tuple[Unit, int]]) -> Plan:
    # 先把同款合并成 (unit, 总数量)，避免小票里出现两行同一个汉堡
    merged: dict[str, tuple[Unit, int]] = {}
    order: list[str] = []
    for unit, qty in items:
        if unit.code in merged:
            merged[unit.code] = (unit, merged[unit.code][1] + qty)
        else:
            merged[unit.code] = (unit, qty)
            order.append(unit.code)
    items = [merged[c] for c in order]

    plan = Plan(items=items)
    roles = set()
    for unit, qty in plan.items:
        plan.price += unit.price * qty
        plan.orig_price += unit.orig_price * qty
        plan.staple += unit.provides[0] * qty
        plan.snack += unit.provides[1] * qty
        plan.drink += unit.provides[2] * qty
        plan.dessert += unit.provides[3] * qty
        plan.items_total += unit.total_items * qty
        plan.kinds += unit.total_items
        plan.distinct_sku += 1
        plan.total_units += qty
        if unit.is_combo and not unit.has_staple:
            plan.no_staple_combo += qty
        if unit.is_kids:
            plan.kids += qty
        for idx, cnt in enumerate(unit.provides):
            if cnt > 0:
                roles.add(idx)
    plan.role_coverage = len(roles)
    plan.price = round(plan.price, 2)
    plan.orig_price = round(plan.orig_price, 2)
    return plan


def _violates(plan: Plan, target: int, *, max_no_staple_combo: int,
              max_extra_snack: int, kids_limit: int = 0) -> bool:
    if plan.staple != target:
        return True
    if plan.drink > drink_cap_for(target):
        return True
    if plan.no_staple_combo > max_no_staple_combo:
        return True
    if (plan.snack + plan.dessert) > max_extra_snack:
        return True
    if plan.kids > kids_limit:
        return True
    return False


# --------------------------------------------------------------------------- #
def _candidate_pool(units: list[Unit], want_spicy: bool, target: int,
                    kids_limit: int = 0) -> dict:
    """按角色切池并做价格预筛，控制枚举规模。"""

    def spicy_ok(u: Unit) -> bool:
        # 辣度只约束「主食来源」（单品主食 / 含主食的套餐）
        if u.has_staple:
            return u.spicy if want_spicy else not u.spicy
        return True

    bucket: dict[str, list[Unit]] = {
        "combo_staple": [], "combo_no_staple": [],
        ROLE_STAPLE: [], ROLE_SNACK: [], ROLE_DRINK: [], ROLE_DESSERT: [],
    }
    for u in units:
        if u.is_kids and kids_limit <= 0:
            continue
        if not spicy_ok(u):
            continue
        if u.is_combo:
            bucket["combo_staple" if u.has_staple else "combo_no_staple"].append(u)
        elif u.role in bucket:
            bucket[u.role].append(u)

    bucket["combo_staple"].sort(key=lambda u: (u.price / max(1, u.staple_count), u.price))
    bucket["combo_no_staple"].sort(key=lambda u: u.price)
    for key in (ROLE_STAPLE, ROLE_SNACK, ROLE_DRINK, ROLE_DESSERT):
        bucket[key].sort(key=lambda u: u.price)

    staple_limit = min(8, max(4, target))
    return {
        "combo_staple": bucket["combo_staple"][:10],
        "combo_no_staple": bucket["combo_no_staple"][:6],
        ROLE_STAPLE: bucket[ROLE_STAPLE][:staple_limit],
        ROLE_SNACK: bucket[ROLE_SNACK][:12],
        ROLE_DRINK: bucket[ROLE_DRINK][:8],
        ROLE_DESSERT: bucket[ROLE_DESSERT][:6],
    }


def _fill(base: list[tuple[Unit, int]], pool: dict, target: int, pref: str,
          *, auto_drink: bool, max_no_staple_combo: int,
          max_extra_snack: int, price_limit: float = 0.0,
          kids_limit: int = 0) -> Plan:
    """在已达主食数的基底上补齐非主食内容。"""
    items = list(base)
    chosen = {u.code for u, _ in items}

    def current() -> Plan:
        return _aggregate(items)

    def try_add(cand: Unit) -> bool:
        nonlocal items
        trial = items + [(cand, 1)]
        plan = _aggregate(trial)
        if price_limit and plan.price > price_limit:
            return False
        if _violates(plan, target, max_no_staple_combo=max_no_staple_combo,
                     max_extra_snack=max_extra_snack, kids_limit=kids_limit):
            return False
        items = trial
        chosen.add(cand.code)
        return True

    if pref == "cheap":
        # 只补一杯必要饮料，不额外加小食
        if auto_drink and current().drink == 0 and target >= 1 and drink_cap_for(target) >= 1:
            for cand in pool[ROLE_DRINK]:
                if cand.code in chosen:
                    continue
                if try_add(cand):
                    break
        return current()

    # 品类多：先把饮料补到「人均一杯」，再按「每元换来的品类数」贪心加小食/甜点/无主食套餐
    drink_goal = min(target, drink_cap_for(target))
    while current().drink < drink_goal:
        added = False
        for cand in pool[ROLE_DRINK]:
            if cand.code in chosen:
                continue
            if try_add(cand):
                added = True
                break
        if not added:
            break

    cands = list(pool[ROLE_SNACK]) + list(pool[ROLE_DESSERT]) + list(pool["combo_no_staple"])
    while True:
        best, best_score = None, None
        for cand in cands:
            if cand.code in chosen:
                continue
            gain = max(1, cand.total_items)
            score = gain / max(1.0, cand.price)
            if _violates(_aggregate(items + [(cand, 1)]), target,
                         max_no_staple_combo=max_no_staple_combo,
                         max_extra_snack=max_extra_snack, kids_limit=kids_limit):
                continue
            if price_limit:
                if _aggregate(items + [(cand, 1)]).price > price_limit:
                    continue
            if best_score is None or score > best_score:
                best, best_score = cand, score
        if best is None:
            break
        items = items + [(best, 1)]
        chosen.add(best.code)

    return current()


def _sort_key(pref: str):
    if pref == "variety":
        return lambda p: (-p.kinds, -p.items_total, p.price)
    return lambda p: (p.price, -p.kinds, -p.items_total)


# --------------------------------------------------------------------------- #
def optimize(units: list[Unit], target: int, pref: str, *, want_spicy: bool = False,
             auto_drink: bool = True, max_no_staple_combo: int = 2,
             keep: int = 40, top: int = 1, price_limit: float = 0.0,
             kids_limit: int = 0) -> list[Plan]:
    """为单一目标主食数求解最优篮子，返回前 top 个（最优在前）。"""
    if target <= 0:
        return []

    pool = _candidate_pool(units, want_spicy, target, kids_limit)
    combos, staples = pool["combo_staple"], pool[ROLE_STAPLE]
    max_extra_snack = target + 2

    if not combos and not staples:
        return []

    bases: list[list[tuple[Unit, int]]] = []
    max_combo_use = min(3, target, len(combos))
    for k in range(0, max_combo_use + 1):
        for subset in combinations_with_replacement(combos, k):
            covered = sum(u.staple_count for u in subset)
            if covered > target:
                continue
            rest = target - covered
            if rest == 0:
                bases.append([(u, 1) for u in subset])
            elif rest <= len(staples):
                for extra in combinations_with_replacement(staples, rest):
                    bases.append([(u, 1) for u in subset] + [(u, 1) for u in extra])

    if not bases:
        return []

    cheapest_drink = pool[ROLE_DRINK][0].price if pool[ROLE_DRINK] else 0.0

    def rough(base: list[tuple[Unit, int]]) -> float:
        price = sum(u.price * q for u, q in base)
        drinks = sum(u.provides[2] * q for u, q in base)
        if auto_drink and drinks == 0 and target >= 1:
            price += cheapest_drink
        return price

    bases.sort(key=rough)
    if price_limit:
        affordable = [b for b in bases if rough(b) <= price_limit]
        if affordable:
            bases = affordable
    shortlist = bases[:keep] if pref == "cheap" else bases[: max(keep, 120)]
    if pref == "variety":
        # 品类多时，套餐型基底更可能胜出，额外保留一部分高件数基底
        extra = sorted(bases[:400], key=lambda b: -sum(u.total_items for u, _ in b))[:60]
        shortlist = shortlist + extra

    plans: list[Plan] = []
    seen: set[tuple] = set()
    for base in shortlist:
        plan = _fill(base, pool, target, pref, auto_drink=auto_drink,
                     max_no_staple_combo=max_no_staple_combo,
                     max_extra_snack=max_extra_snack, price_limit=price_limit,
                     kids_limit=kids_limit)
        if _violates(plan, target, max_no_staple_combo=max_no_staple_combo,
                     max_extra_snack=max_extra_snack, kids_limit=kids_limit):
            continue
        sig = tuple(sorted(plan.codes()))
        if sig in seen:
            continue
        seen.add(sig)
        plans.append(plan)

    plans.sort(key=_sort_key(pref))
    return plans[:top]


def build_ladder(units: list[Unit], staple_count: int, pref: str, *,
                 want_spicy: bool = False, auto_drink: bool = True,
                 max_no_staple_combo: int = 2, span: int = 3,
                 same_tier_extra: int = 2, price_ratio: float = 2.0,
                 kids_limit: int = 0) -> dict:
    """生成完整推荐阶梯。

    baseline  主食数 = N 的最优篮子
    rank1     主食数 = N-1 的最优篮子（对比 baseline 说明变化）
    rank2     主食数 = N+1 的最优篮子
    others    其余方案（N 档的第 2~3 名 + N±2 … N±span），按倾向统一排序

    price_ratio：品类多模式的价格护栏（同档位省钱价的倍数，0 表示不限），
    避免「品类多」无限堆叠把小票堆到离谱。
    """
    common = dict(want_spicy=want_spicy, auto_drink=auto_drink,
                  max_no_staple_combo=max_no_staple_combo, kids_limit=kids_limit)
    cheap_ref: dict[int, float] = {}

    def _opt(t: int, top: int = 1) -> list[Plan]:
        limit = 0.0
        if pref == "variety" and price_ratio and price_ratio > 0:
            if t not in cheap_ref:
                got = optimize(units, t, "cheap", top=1, **common)
                cheap_ref[t] = got[0].price if got else 0.0
            if cheap_ref[t]:
                limit = cheap_ref[t] * price_ratio
        return optimize(units, t, pref, top=top, price_limit=limit, **common)

    n_plans = _opt(staple_count, top=max(1, same_tier_extra + 1))
    baseline = n_plans[0] if n_plans else None

    rank1 = None
    if staple_count - 1 >= 1:
        got = _opt(staple_count - 1, top=1)
        rank1 = got[0] if got else None

    got = _opt(staple_count + 1, top=1)
    rank2 = got[0] if got else None

    others: list[tuple[str, Plan]] = []
    if baseline:
        base_sig = tuple(sorted(baseline.codes()))
        for p in n_plans[1:]:
            if tuple(sorted(p.codes())) == base_sig:
                continue
            # 剔除被基准方案完全支配的（更贵且品类没变多），它们没有推荐价值
            if p.price >= baseline.price and p.kinds <= baseline.kinds:
                continue
            others.append(("N", p))

    for delta in range(2, span + 1):
        for sign, tag in ((-1, "N-%d" % delta), (1, "N+%d" % delta)):
            t = staple_count + sign * delta
            if t < 1:
                continue
            got = _opt(t, top=1)
            if got:
                others.append((tag, got[0]))

    others.sort(key=lambda x: _sort_key(pref)(x[1]))
    return {"baseline": baseline, "rank1": rank1, "rank2": rank2, "others": others}
