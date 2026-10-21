#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 query-meals 的原始菜单翻译成「可组合的购物篮单元」。

核心概念 —— Unit（单元）：
    购物篮 = 若干 Unit 的和。每个 Unit 带有「它能提供多少个主食/小食/饮料/甜点」，
    这样"一篮子有几个汉堡、几杯可乐"就能精确统计，而不是靠 agent 猜。

角色判定优先级（顺序不能乱）：
    非食品 → 套餐/组合 → 甜点 → 饮料 → 主食(堡/卷) → 小食 → 其他
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

ROLE_STAPLE = "staple"
ROLE_SNACK = "snack"
ROLE_DRINK = "drink"
ROLE_DESSERT = "dessert"
ROLE_OTHER = "other"

ROLE_LABEL = {
    ROLE_STAPLE: "主食",
    ROLE_SNACK: "小食",
    ROLE_DRINK: "饮料",
    ROLE_DESSERT: "甜点",
    ROLE_OTHER: "其他",
}

# --- 关键词表（按真实菜单校准过，见 references/rules.md） -------------------- #
NON_FOOD_KW = ("按摩", "痒痒挠", "玩具", "周边", "毛绒", "贴纸", "盲盒", "卡牌")

COMBO_KW = ("件套", "套餐", "随心配", "随心选", "随心拼", "双人餐", "分享餐",
            "亲子餐", "乐园餐", "四宫格", "单人餐", "精选")

DESSERT_KW = ("麦旋风", "新地", "圆筒", "冰淇淋", "甜筒", "圣代", "派", "冰沙")

DRINK_KW = ("可乐", "雪碧", "红茶", "牛奶", "咖啡", "果汁", "橙橙", "柠", "怡泉",
            "麦炫酷", "奶昔", "豆浆", "纯悦", "饮用水", "奶茶", "茶", "水")

# 「麦香鸡」是汉堡名（不含"堡"字），必须排在 SNACK 的"鸡"之前
STAPLE_KW = ("堡", "卷", "汉堡", "麦香鸡")

SNACK_KW = ("薯条", "薯饼", "鸡翅", "麦乐鸡", "鸡块", "鸡排", "玉米杯", "苹果片",
            "芝士条", "鸡肉", "鸡", "翅", "肉", "饼", "沙拉", "汤", "薯", "蘸酱", "酱")

SPICY_KW = ("麦辣", "香辣", "麻辣", "辣味", "龙焰", "藤椒", "川辣", "韩式辣",
            "辣椒", "辣")
MILD_KW = ("不辣", "微辣", "原味")

# 儿童餐：默认不给成人推荐，用户带了几个小孩就开放几份
KIDS_KW = ("开心乐园", "亲子餐", "儿童餐")

# 套餐件数推断：n 件套 = 1 主食 + (n-2) 小食 + 1 饮料
PIECE_PAT = re.compile(r"([三四五六七八九十\d]+)\s*件套")
CN_NUM = {"三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _to_price(value) -> float:
    try:
        return round(float(str(value).strip()), 2)
    except (TypeError, ValueError):
        return 0.0


def is_spicy(name: str) -> bool:
    if any(k in name for k in MILD_KW):
        return False
    return any(k in name for k in SPICY_KW)


def _is_condiment(name: str) -> bool:
    """蘸酱/调味酱：可单点但不算「食物品类」，否则会被品类多模式当成便宜小食加满。"""
    if not name.endswith("酱"):
        return False
    food = ("薯", "鸡", "堡", "卷", "肉", "翅", "块", "条", "饼", "玉米", "苹果", "派", "蛋")
    return not any(k in name for k in food)


def classify_single(name: str) -> str:
    """单品角色分类。"""
    n = name or ""
    if any(k in n for k in NON_FOOD_KW):
        return ROLE_OTHER
    if _is_condiment(n):
        return ROLE_OTHER
    if any(k in n for k in DESSERT_KW):
        return ROLE_DESSERT
    if any(k in n for k in DRINK_KW):
        return ROLE_DRINK
    if any(k in n for k in STAPLE_KW):
        return ROLE_STAPLE
    if any(k in n for k in SNACK_KW):
        return ROLE_SNACK
    return ROLE_OTHER


def is_combo_name(name: str) -> bool:
    n = name or ""
    if any(k in n for k in NON_FOOD_KW):
        return False
    return any(k in n for k in COMBO_KW)


def combo_pieces_from_name(name: str) -> int | None:
    m = PIECE_PAT.search(name or "")
    if not m:
        return None
    raw = m.group(1)
    if raw.isdigit():
        return int(raw)
    return CN_NUM.get(raw)


# --------------------------------------------------------------------------- #
@dataclass
class Unit:
    code: str
    name: str
    price: float
    orig_price: float
    role: str                                  # staple/snack/drink/dessert/combo/other
    provides: tuple[int, int, int, int]        # (staple, snack, drink, dessert)
    is_combo: bool = False
    spicy: bool = False
    tags: tuple[str, ...] = ()
    default_contents: tuple[str, ...] = ()     # 套餐默认含的子项名称
    requires_card: bool = False                # 菜单价依赖「随单购麦金卡」
    card_price: float | None = None            # 有卡时的优惠价
    is_kids: bool = False                      # 开心乐园餐/亲子餐等儿童餐

    # -- 派生属性 ---------------------------------------------------------- #
    @property
    def has_staple(self) -> bool:
        return self.provides[0] > 0

    @property
    def total_items(self) -> int:
        return sum(self.provides)

    @property
    def staple_count(self) -> int:
        return self.provides[0]

    def key(self) -> str:
        return self.code

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "price": self.price,
            "originalPrice": self.orig_price,
            "role": self.role,
            "isCombo": self.is_combo,
            "spicy": self.spicy,
            "requiresCard": self.requires_card,
            "cardPrice": self.card_price,
            "isKids": self.is_kids,
            "provides": {
                "staple": self.provides[0],
                "snack": self.provides[1],
                "drink": self.provides[2],
                "dessert": self.provides[3],
            },
            "defaultContents": list(self.default_contents),
        }


# --------------------------------------------------------------------------- #
def _round_role(round_obj: dict) -> str:
    """判定一个选配轮次提供什么角色：优先看默认选中的子项名称。

    「蓝区」是 1+1 随心配里的主商品位，默认选项决定它算主食还是小食
    （默认吉士汉堡 → 主食；默认鸡排 → 小食），判不出来时按主食兜底。
    """
    rname = round_obj.get("name") or ""
    is_blue = "蓝区" in rname

    choices = round_obj.get("choices") or []
    picked = [c for c in choices if int(c.get("quantity") or 0) > 0]
    if not picked:
        picked = [c for c in choices if int(c.get("isDefault") or 0) == 1]
    if not picked and choices:
        picked = choices[:1]

    for choice in picked:
        role = classify_single(choice.get("name") or "")
        if role != ROLE_OTHER:
            return role

    if is_blue:
        return ROLE_STAPLE
    # 选项名判不出来时回退到轮次名
    if any(k in rname for k in ("饮料", "饮品")):
        return ROLE_DRINK
    if any(k in rname for k in NON_FOOD_KW):
        return ROLE_OTHER
    if any(k in rname for k in ("小食", "甜点", "配餐", "蘸酱", "酱")):
        return ROLE_SNACK
    return ROLE_STAPLE


def _provides_from_detail(detail: dict) -> tuple[tuple[int, int, int, int], tuple[str, ...], bool]:
    counters = {ROLE_STAPLE: 0, ROLE_SNACK: 0, ROLE_DRINK: 0, ROLE_DESSERT: 0}
    names: list[str] = []
    spicy_flag = False

    for rnd in detail.get("rounds") or []:
        qty = int(rnd.get("quantity") or 1) or 1
        role = _round_role(rnd)
        if role in counters:
            counters[role] += qty
        # ROLE_OTHER（玩具/周边/蘸酱等）不计入食物件数

        choices = rnd.get("choices") or []
        picked = [c for c in choices if int(c.get("quantity") or 0) > 0] or \
                 [c for c in choices if int(c.get("isDefault") or 0) == 1]
        for choice in picked:
            cname = choice.get("name") or ""
            if cname:
                names.append(cname)
                if role == ROLE_STAPLE and is_spicy(cname):
                    spicy_flag = True

    provides = (counters[ROLE_STAPLE], counters[ROLE_SNACK],
                counters[ROLE_DRINK], counters[ROLE_DESSERT])
    return provides, tuple(names), spicy_flag


def _provides_from_name(name: str) -> tuple[int, int, int, int]:
    pieces = combo_pieces_from_name(name)
    if pieces and pieces >= 2:
        return (1, max(0, pieces - 2), 1, 0)
    if any(k in name for k in ("随心配", "随心选")):
        return (1, 0, 1, 0)
    if any(k in name for k in ("双人", "分享", "亲子")):
        return (2, 2, 2, 0)
    return (1, 1, 1, 0)


# --------------------------------------------------------------------------- #
class Cache:
    def __init__(self, directory: Path, ttl: int = 6 * 3600):
        self.dir = directory
        self.ttl = ttl
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe = re.sub(r"[^0-9A-Za-z_.-]", "_", key)
        return self.dir / f"{safe}.json"

    def get(self, key: str):
        p = self._path(key)
        try:
            if p.is_file() and (time.time() - p.stat().st_mtime) < self.ttl:
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
        return None

    def set(self, key: str, value):
        try:
            self._path(key).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass


# --------------------------------------------------------------------------- #
def build_units(client, store_code: str, menu_payload: dict, *, be_type: int = 1,
                order_type: int | None = None, cache: Cache | None = None,
                refresh: bool = False, workers: int = 6, with_card: bool = False,
                log=None) -> list[Unit]:
    """把菜单翻译成 Unit 列表。套餐会调用 meal-detail 拿真实组成。

    with_card=False（默认）时，「随单购麦金卡」类餐品按原价计入 —— 菜单上的
    currentPrice 只是有卡价（例如四件套标 28，实算 57），不校正会让推荐价虚低。
    """
    data = menu_payload.get("data") or {}
    meal_map: dict = data.get("meals") or {}
    categories: list = data.get("categories") or []

    # code -> tags 聚合
    tag_map: dict[str, set[str]] = {}
    for cat in categories:
        for m in cat.get("meals") or []:
            code = str(m.get("code") or "")
            if not code:
                continue
            tag_map.setdefault(code, set()).update(m.get("tags") or [])

    base_rows: dict[str, dict] = {}
    for code, info in meal_map.items():
        name = str(info.get("name") or "").strip()
        if not name:
            continue
        cur = _to_price(info.get("currentPrice"))
        orig = _to_price(info.get("originalPrice")) or cur
        discount = str(info.get("discountType") or "")
        requires_card = ("随单购" in discount) and cur < orig
        base_rows[str(code)] = {
            "name": name,
            # 有卡价的餐品：用户没说有卡时按原价算，避免推荐价虚低
            "price": cur if (with_card or not requires_card) else orig,
            "orig": orig,
            "core": cur,
            "card": requires_card,
        }

    combo_codes = [c for c, r in base_rows.items() if is_combo_name(r["name"])]

    # 套餐组成：先查缓存，再并发补拉
    details: dict[str, dict] = {}
    todo: list[str] = []
    for code in combo_codes:
        cached = None if refresh else (cache.get(f"detail_{store_code}_{code}") if cache else None)
        if cached:
            details[code] = cached
        else:
            todo.append(code)

    if todo and client is not None:
        if log:
            log(f"查询 {len(todo)} 个套餐的组成…")

        def _fetch(code: str):
            try:
                payload = client.meal_detail(store_code, code, be_type=be_type, order_type=order_type)
                dd = payload.get("data") or {}
                if dd:
                    if cache:
                        cache.set(f"detail_{store_code}_{code}", dd)
                    return code, dd
            except Exception:
                pass
            return code, {}

        with ThreadPoolExecutor(max_workers=workers) as pool:
            failed = 0
            for code, dd in pool.map(_fetch, todo):
                if dd:
                    details[code] = dd
                else:
                    failed += 1
            if log and failed:
                log(f"{failed} 个套餐查不到详情，已按名称推断组成")

    units: list[Unit] = []
    for code, row in base_rows.items():
        name, price, orig = row["name"], row["price"], row["orig"]
        tags = tuple(sorted(tag_map.get(code, ())))

        if is_combo_name(name):
            detail = details.get(code) or {}
            if detail.get("rounds"):
                provides, contents, spicy = _provides_from_detail(detail)
            else:
                provides = _provides_from_name(name)
                contents, spicy = (), is_spicy(name)
            role = "combo"
            is_combo = True
        else:
            role = classify_single(name)
            provides = {
                ROLE_STAPLE: (1, 0, 0, 0),
                ROLE_SNACK: (0, 1, 0, 0),
                ROLE_DRINK: (0, 0, 1, 0),
                ROLE_DESSERT: (0, 0, 0, 1),
            }.get(role, (0, 0, 0, 0))
            contents = ()
            spicy = is_spicy(name) if role == ROLE_STAPLE else False
            is_combo = False

        if provides == (0, 0, 0, 0):
            continue

        units.append(Unit(
            code=code, name=name, price=price, orig_price=orig or price,
            role=role, provides=provides, is_combo=is_combo, spicy=spicy,
            tags=tags, default_contents=contents,
            requires_card=row["card"],
            card_price=row["core"] if row["card"] else None,
            is_kids=any(k in name for k in KIDS_KW),
        ))

    return units
