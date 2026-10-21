#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦当劳点单助手：一条命令跑完「取菜单 → 组装购物篮 → 排序 → 算价校验」。

门店必须先由用户指定，脚本不使用任何「默认/收藏门店」。

示例：
    python order_helper.py --list-stores --city 上海 --keyword 徐家汇
    python order_helper.py --store 1450034 --staple 2 --spicy no --pref cheap
    python order_helper.py --store 1450034 --staple 3 --spicy 辣 --pref variety --verify 2 --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcd_client import MCDClient, MCDError                       # noqa: E402
from menu_model import ROLE_LABEL, Cache, build_units           # noqa: E402
from optimizer import (Plan, build_ladder, drink_cap_for,       # noqa: E402
                       parse_preference, parse_spicy)

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"


# --------------------------------------------------------------------------- #
class MissingStore(MCDError):
    """门店未确定：必须先由用户选择，脚本不做默认推断。"""


STORES_HINT = (
    "请先确定门店（本工具不使用收藏/默认门店，必须由用户指定）：\n"
    "  1) 列候选：python order_helper.py --list-stores --city \"<城市>\" --keyword \"<商圈或地标>\"\n"
    "  2) 挑一家：python order_helper.py --store <storeCode> --staple N --spicy no|辣 --pref cheap|variety\n"
    "注：按城市搜索必须带关键词（商圈/路名/店名），只给城市会返回空。"
)


def search_stores(client: MCDClient, be_type: int, city: str | None,
                  keyword: str | None) -> list[dict]:
    """按「城市 + 关键词」搜索门店。不使用收藏门店接口。"""
    city = (city or "").strip()
    keyword = (keyword or "").strip()
    if not city:
        raise MissingStore("缺少 --city。\n" + STORES_HINT)
    if not keyword:
        raise MissingStore("缺少 --keyword（麦当劳只支持「城市 + 商圈/地标」搜索，只给城市会返回空）。\n" + STORES_HINT)
    stores = client.stores(be_type=be_type, search_type=2, city=city, keyword=keyword)
    return stores or []


def pick_store(client: MCDClient, args) -> dict:
    """只认 --store。没有就直接报错让用户选，绝不自动挑一家。"""
    code = (args.store or "").strip()
    if not code:
        raise MissingStore("未指定门店。\n" + STORES_HINT)

    info = {"storeCode": code, "storeName": "", "address": "", "beType": args.be_type}
    # 顺带补全店名/地址，仅用于展示；查不到不影响主流程
    if args.city:
        try:
            for s in search_stores(client, args.be_type, args.city, args.keyword):
                if str(s.get("storeCode")) == code:
                    info.update(storeName=s.get("storeName") or "",
                                address=s.get("address") or "",
                                beCode=s.get("beCode"),
                                distance=s.get("distance"))
                    break
        except MCDError:
            pass
    return info


def _delta(base: Plan | None, plan: Plan) -> dict:
    if base is None:
        return {"price": 0.0, "kinds": 0, "itemsTotal": 0, "counts": {}, "summary": "无基准可对比"}
    d_price = round(plan.price - base.price, 2)
    d_kinds = plan.kinds - base.kinds
    d_items = plan.items_total - base.items_total
    parts: list[str] = []
    parts.append("价格持平" if abs(d_price) < 0.005
                 else ("贵 ¥%.2f" % d_price if d_price > 0 else "便宜 ¥%.2f" % abs(d_price)))
    if d_kinds:
        parts.append("多 %d 种品类" % d_kinds if d_kinds > 0 else "少 %d 种品类" % abs(d_kinds))
    if d_items and d_items != d_kinds:
        parts.append("多 %d 件食物" % d_items if d_items > 0 else "少 %d 件食物" % abs(d_items))
    counts: dict[str, int] = {}
    for key, label in (("staple", "主食"), ("snack", "小食"), ("drink", "饮料"), ("dessert", "甜点")):
        diff = getattr(plan, key) - getattr(base, key)
        counts[key] = diff
        if diff:
            parts.append("多 %d 个%s" % (diff, label) if diff > 0 else "少 %d 个%s" % (abs(diff), label))
    return {
        "price": d_price, "kinds": d_kinds, "itemsTotal": d_items,
        "counts": counts, "summary": "、".join(parts),
    }


def _plan_payload(plan: Plan, base: Plan | None) -> dict:
    d = plan.to_dict()
    d["delta"] = _delta(base, plan)
    return d


def _fmt_items(plan: Plan) -> list[str]:
    lines = []
    for unit, qty in plan.items:
        suffix = ""
        if unit.is_combo and unit.total_items > 1:
            contents = [c for c in unit.default_contents if c and c != unit.name]
            if contents:
                suffix = "（含 " + "/".join(dict.fromkeys(contents)) + "）"
        if unit.requires_card and unit.card_price:
            suffix += "〔需麦金卡，有卡 ¥%.2f〕" % unit.card_price
        qty_txt = "" if qty == 1 else " ×%d" % qty
        lines.append("%s%s%s ¥%.2f" % (unit.name, qty_txt, suffix, unit.price * qty))
    return lines


# --------------------------------------------------------------------------- #
def _price_head(label: str, payload: dict) -> str:
    """价格标题行：官方算价与估算价差别大时两个都报，避免误导。"""
    head = "%s合计 ¥%.2f" % (label, payload["price"])
    vp = payload.get("verifiedPrice")
    if vp is not None:
        if abs(vp - payload["price"]) > max(0.5, payload["price"] * 0.01):
            head += "（官方算价 ¥%.2f，实付以结算页为准）" % vp
        else:
            head += "（官方实算 ¥%.2f）" % vp
    return head


def render_text(result: dict) -> str:
    p = result["params"]
    store = result["store"]
    out: list[str] = []
    code = store["storeCode"]
    name = (store.get("storeName") or "").strip()
    out.append(("门店：%s（%s）" % (name, code)) if name and name != code else ("门店：%s" % code))
    if store.get("address"):
        out.append("地址：%s" % store["address"])
    cond = "条件：主食 %d 个 ｜ %s ｜ %s ｜ 饮料上限 %d 杯" % (
        p["staple"], "要辣" if p["spicy"] else "不辣",
        "品类多优先" if p["preference"] == "variety" else "省钱优先", p["drinkCap"])
    cond += " ｜ 麦金卡：%s" % ("有" if p.get("withCard") else "无")
    if p.get("kids"):
        cond += " ｜ 儿童：%d 位" % p["kids"]
    out.append(cond)
    out.append("")

    base = result.get("baseline")
    if base:
        out.append("── 基准方案（%d 个主食）%s ──" % (p["staple"], _price_head("", base)))
        for line in _fmt_items(Plan_for(base)):
            out.append("  · " + line)
        out.append("  件数：主食 %d／小食 %d／饮料 %d／甜点 %d；共 %d 件食物、%d 种不同品类" % (
            base["counts"]["staple"], base["counts"]["snack"], base["counts"]["drink"],
            base["counts"]["dessert"], base["itemsTotal"], base["kinds"]))
        out.append("")

    for rec in result.get("recommendations") or []:
        diff = rec["targetStaple"] - p["staple"]
        if diff == 0:
            tier_txt = "同为主食 %d 个" % rec["targetStaple"]
        elif diff < 0:
            tier_txt = "少 %d 个主食（%d 个）" % (abs(diff), rec["targetStaple"])
        else:
            tier_txt = "多 %d 个主食（%d 个）" % (diff, rec["targetStaple"])
        head = "── 推荐 %d（%s）%s ──" % (rec["rank"], tier_txt, _price_head("", rec["plan"]))
        out.append(head)
        for line in _fmt_items(Plan_for(rec["plan"])):
            out.append("  · " + line)
        out.append("  品类 %d 种／%d 件，主食 %d 小食 %d 饮料 %d 甜点 %d" % (
            rec["plan"]["kinds"], rec["plan"]["itemsTotal"],
            rec["plan"]["counts"]["staple"], rec["plan"]["counts"]["snack"],
            rec["plan"]["counts"]["drink"], rec["plan"]["counts"]["dessert"]))
        out.append("  相比基准：%s" % rec["plan"]["delta"]["summary"])
        out.append("")

    for note in result.get("notes") or []:
        out.append("※ " + note)
    return "\n".join(out)


def Plan_for(d: dict) -> Plan:
    """把 dict 还原成轻量 Plan 以便复用 _fmt_items。"""
    from menu_model import Unit
    plan = Plan()
    for it in d.get("items") or []:
        pr = it.get("provides") or {}
        provides = (pr.get("staple", 0), pr.get("snack", 0),
                    pr.get("drink", 0), pr.get("dessert", 0))
        unit = Unit(
            code=it["code"], name=it["name"], price=it.get("unitPrice", 0.0),
            orig_price=it.get("unitPrice", 0.0), role=it.get("role", "other"),
            provides=provides, is_combo=it.get("isCombo", False),
            spicy=it.get("spicy", False), tags=(),
            default_contents=tuple(it.get("contents") or ()),
            requires_card=bool(it.get("requiresCard")),
            card_price=it.get("cardPrice"),
            is_kids=bool(it.get("isKids")),
        )
        plan.items.append((unit, it.get("quantity", 1)))
    return plan


# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="麦当劳点单助手：按主食数/辣度/倾向组装最划算的购物篮")
    ap.add_argument("--staple", type=int, default=1, help="主食数量（汉堡/鸡肉卷等）")
    ap.add_argument("--spicy", default="no", help="no/不辣 或 yes/辣，二选一")
    ap.add_argument("--pref", default="cheap", help="cheap/省钱 或 variety/品类多")
    ap.add_argument("--store", help="门店 storeCode（必填；由用户从 --list-stores 结果中选定）")
    ap.add_argument("--city", help="门店搜索城市，配合 --list-stores 使用")
    ap.add_argument("--keyword", help="门店搜索关键词：商圈/路名/店名（按城市搜索时必填）")
    ap.add_argument("--be-type", type=int, default=1, help="1=到店自取 5=得来速")
    ap.add_argument("--span", type=int, default=2, help="额外档位跨度 N±span")
    ap.add_argument("--kids", type=int, default=0,
                    help="带几个小孩：0=不推儿童餐，N=最多 N 份开心乐园餐/亲子餐")
    ap.add_argument("--card", choices=["yes", "no"], help="是否持有麦金卡：yes 按卡价算")
    ap.add_argument("--with-card", action="store_true", help="等同 --card yes")
    ap.add_argument("--max-no-staple-combo", type=int, default=2, help="品类多模式：不含主食的套餐上限")
    ap.add_argument("--price-limit", type=float, default=2.0,
                    help="品类多模式价格护栏：不超过同档位省钱价的 N 倍，0 表示不限")
    ap.add_argument("--no-auto-drink", action="store_true", help="省钱模式也不自动配饮料")
    ap.add_argument("--verify", type=int, default=0, help="用官方算价校验前 K 个方案")
    ap.add_argument("--json", dest="json_path", help="输出结构化 JSON 的路径")
    ap.add_argument("--list-stores", action="store_true",
                    help="按 --city + --keyword 列出候选门店，供用户选择")
    ap.add_argument("--dump-classify", action="store_true", help="打印分类结果用于校准")
    ap.add_argument("--refresh", action="store_true", help="忽略缓存")
    args = ap.parse_args(argv)

    try:
        client = MCDClient()
    except MCDError as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 2

    if args.list_stores:
        try:
            stores = search_stores(client, args.be_type, args.city, args.keyword)
        except MCDError as exc:
            print("ERROR:", exc, file=sys.stderr)
            return 5
        if not stores:
            print("没有搜到门店：城市=%s 关键词=%s。请换商圈/地标再试。" % (
                args.city, args.keyword), file=sys.stderr)
            return 5

        stores.sort(key=lambda s: (0 if s.get("businessStatus") else 1,
                                  s.get("distance") or 0))
        print("在「%s · %s」附近搜到 %d 家门店（打 * 为营业中）：" % (
            args.city, args.keyword, len(stores)))
        for idx, s in enumerate(stores, 1):
            dist = s.get("distance")
            print("%s%2d. [%s] %s ｜ %s%s" % (
                "*" if s.get("businessStatus") else " ",
                idx, s.get("storeCode"), s.get("storeName") or "-",
                s.get("address") or "-",
                ("（%dm）" % dist) if isinstance(dist, (int, float)) and dist else ""))
        print("\n请用户选定门店后，用 --store <storeCode> 重跑。")
        return 0

    pref = parse_preference(args.pref)
    want_spicy = parse_spicy(args.spicy)
    staple = max(1, int(args.staple))
    kids = max(0, int(args.kids or 0))
    with_card = bool(args.with_card) or args.card == "yes"

    try:
        store = pick_store(client, args)
        menu = client.meals(store["storeCode"], be_type=args.be_type,
                            be_code=store.get("beCode"))
    except MissingStore as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 5
    except MCDError as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 2

    if not (menu.get("success") and menu.get("data")):
        print("ERROR: 取菜单失败：%s" % menu.get("message"), file=sys.stderr)
        return 3

    cache = Cache(CACHE_DIR)
    units = build_units(client, store["storeCode"], menu, be_type=args.be_type,
                        cache=cache, refresh=args.refresh, with_card=with_card,
                        log=lambda m: print(m, file=sys.stderr))

    if args.dump_classify:
        for u in sorted(units, key=lambda x: (x.role, x.price)):
            print("%-9s %-8s ¥%-7.1f %s%s%s" % (
                u.role, ROLE_LABEL.get(u.role, "套餐" if u.is_combo else "其他"),
                u.price, u.name,
                " [辣]" if u.spicy else "",
                " {主食%d 小食%d 饮料%d 甜点%d}" % u.provides))
        return 0

    ladder = build_ladder(units, staple, pref, want_spicy=want_spicy,
                          auto_drink=not args.no_auto_drink,
                          max_no_staple_combo=args.max_no_staple_combo,
                          span=args.span, price_ratio=args.price_limit,
                          kids_limit=kids)

    base: Plan | None = ladder["baseline"]
    if base is None:
        print("ERROR: 没有满足条件的组合（可能是辣度过滤后主食候选为空）。", file=sys.stderr)
        return 4

    # 组装推荐序列（先拿 Plan 对象，算价校验后再转 payload）
    recs_raw: list[dict] = []
    rank = 0
    for tag, plan in (("N-1", ladder["rank1"]), ("N+1", ladder["rank2"])):
        if plan is None:
            continue
        rank += 1
        recs_raw.append({"rank": rank, "tier": tag, "targetStaple": plan.staple, "plan": plan})
    for tag, plan in ladder["others"]:
        rank += 1
        recs_raw.append({"rank": rank, "tier": tag, "targetStaple": plan.staple, "plan": plan})

    # 官方算价校验（含基准，取前 K 个）
    if args.verify > 0:
        checks: list[tuple[str, list[tuple[str, int]], Plan]] = [("基准", base.codes(), base)]
        for item in recs_raw[: max(0, args.verify - 1)]:
            checks.append(("推荐%d" % item["rank"], item["plan"].codes(), item["plan"]))
        for label, codes, plan in checks:
            try:
                priced = client.calculate_price(store["storeCode"], codes,
                                                be_type=args.be_type, be_code=store.get("beCode"))
                data = priced.get("data") or {}
                if data.get("price") is not None:
                    plan.verified_price = round(float(data["price"]) / 100, 2)
                    plan.verify_note = "官方算价"
            except Exception as exc:      # 算价失败不阻断主流程
                print("WARN 算价失败(%s): %s" % (label, exc), file=sys.stderr)

    recs = [{"rank": r["rank"], "tier": r["tier"], "targetStaple": r["targetStaple"],
             "plan": _plan_payload(r["plan"], base)} for r in recs_raw]

    notes: list[str] = []
    if want_spicy and not any(u.spicy and u.has_staple for u in units):
        notes.append("该门店当前没有辣味主食，已就近降级为不辣方案。")
    if not ladder["rank1"]:
        notes.append("主食数已为 1，无法再给出「少一个主食」的方案。")
    if kids > 0:
        notes.append("已按 %d 位儿童开放最多 %d 份儿童餐（开心乐园餐/亲子餐）。" % (kids, kids))

    result = {
        "store": store,
        "params": {
            "staple": staple,
            "spicy": want_spicy,
            "preference": pref,
            "drinkCap": drink_cap_for(staple),
            "maxNoStapleCombo": args.max_no_staple_combo,
            "autoDrink": not args.no_auto_drink,
            "withCard": with_card,
            "kids": kids,
        },
        "baseline": _plan_payload(base, None) if base else None,
        "recommendations": recs,
        "notes": notes,
    }

    has_card_item = any(
        it.get("requiresCard") for it in (result["baseline"] or {}).get("items", [])
    ) or any(it.get("requiresCard") for r in recs for it in r["plan"]["items"])
    if has_card_item:
        if with_card:
            result["notes"].append(
                "方案含「随单购麦金卡」餐品：卡价需在结算时把麦金卡一起加入购物车才生效，"
                "否则按原价计，实付以结算页为准。")
        else:
            result["notes"].append(
                "方案含「随单购麦金卡」餐品：本次按原价计算，持卡在结算时可更低。")

    print(render_text(result))
    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
