#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对 order_helper.py 做端到端自测：逐组参数真跑，断言所有硬约束。

用法：
    python scripts/selftest.py --store 1450034
    python scripts/selftest.py --store 1450034 --quick
    python scripts/selftest.py --store 1450034 --matrix full

每个用例都会真实调用麦当劳 MCP（需要凭证），检查：
  1. 退出码为 0
  2. 篮子主食数 == 目标值
  3. 饮料数 <= ceil(主食数 × 4/3)
  4. 小食+甜点 <= 主食数 + 2
  5. 「不含主食的套餐」<= 2
  6. 儿童餐份数 <= 带的小孩数
  7. 前两条推荐必须是主食数 N-1 / N+1
  8. 排序符合倾向（省钱→价格升序；品类多→品类降序）
  9. 价格为正、数量为正、辣度符合要求
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from optimizer import drink_cap_for      # noqa: E402

PY = sys.executable
ENTRY = HERE / "order_helper.py"


def run_case(store: str, staple: int, spicy: str, pref: str,
             kids: int, card: str, verify: int = 1) -> tuple[int, dict | None, str]:
    """跑一次 CLI，返回 (退出码, 结果字典, stderr)。"""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as fh:
        out = Path(fh.name)
    cmd = [PY, str(ENTRY), "--store", store, "--staple", str(staple),
           "--spicy", spicy, "--pref", pref, "--kids", str(kids),
           "--card", card, "--json", str(out)]
    if verify:
        cmd += ["--verify", str(verify)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=300)
    data = None
    if out.is_file() and out.stat().st_size:
        try:
            data = json.loads(out.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = None
        out.unlink(missing_ok=True)
    return proc.returncode, data, proc.stderr.strip()


def check(name: str, cond: bool, detail: str = "") -> tuple[bool, str]:
    return (bool(cond), "%s %s%s" % ("PASS" if cond else "FAIL", name,
                                      ("：" + detail) if detail and not cond else ""))


def verify_plan(plan: dict, staple: int, kids: int, want_spicy: bool,
                label: str) -> list[tuple[bool, str]]:
    res: list[tuple[bool, str]] = []
    counts = plan.get("counts") or {}
    cap = drink_cap_for(staple)
    res.append(check("[%s] 主食数=%d" % (label, staple), counts.get("staple") == staple,
                     "实际 %s" % counts.get("staple")))
    res.append(check("[%s] 饮料<=%d" % (label, cap), counts.get("drink", 0) <= cap,
                     "实际 %s" % counts.get("drink")))
    res.append(check("[%s] 小食+甜点<=%d" % (label, staple + 2),
                     counts.get("snack", 0) + counts.get("dessert", 0) <= staple + 2,
                     "实际 %s" % (counts.get("snack", 0) + counts.get("dessert", 0))))
    res.append(check("[%s] 无主食套餐<=2" % label, plan.get("noStapleCombo", 0) <= 2,
                     "实际 %s" % plan.get("noStapleCombo")))
    res.append(check("[%s] 儿童餐<=%d" % (label, kids), plan.get("kidsMeals", 0) <= kids,
                     "实际 %s" % plan.get("kidsMeals")))
    res.append(check("[%s] 价格为正" % label, (plan.get("price") or 0) > 0,
                     "实际 %s" % plan.get("price")))
    items = plan.get("items") or []
    res.append(check("[%s] 数量均为正" % label,
                     bool(items) and all((it.get("quantity") or 0) > 0 for it in items)))
    # 辣度：只约束提供主食的单元
    if want_spicy:
        bad = [it["name"] for it in items
               if (it.get("provides") or {}).get("staple", 0) > 0 and not it.get("spicy")]
        res.append(check("[%s] 主食均为辣" % label, not bad,
                         "非辣主食 %s" % ",".join(bad[:3])))
    else:
        bad = [it["name"] for it in items
               if (it.get("provides") or {}).get("staple", 0) > 0 and it.get("spicy")]
        res.append(check("[%s] 主食均不辣" % label, not bad,
                         "辣主食 %s" % ",".join(bad[:3])))
    return res


def verify_order(result: dict, staple: int, pref: str) -> list[tuple[bool, str]]:
    res: list[tuple[bool, str]] = []
    recs = result.get("recommendations") or []
    res.append(check("推荐条数>=1", len(recs) >= 1, "实际 %d" % len(recs)))
    if not recs:
        return res

    if staple > 1:
        res.append(check("推荐1 = N-1 (主食%d)" % (staple - 1),
                         recs[0]["targetStaple"] == staple - 1,
                         "实际 %s" % recs[0]["targetStaple"]))
        idx = 1
    else:
        idx = 0
    if len(recs) > idx:
        res.append(check("推荐%d = N+1 (主食%d)" % (idx + 1, staple + 1),
                         recs[idx]["targetStaple"] == staple + 1,
                         "实际 %s" % recs[idx]["targetStaple"]))

    res.append(check("前两条均带变化说明",
                     all((r["plan"].get("delta") or {}).get("summary") for r in recs[:2])))

    rest = recs[idx + 1:]
    if len(rest) >= 2:
        if pref == "cheap":
            prices = [r["plan"]["price"] for r in rest]
            res.append(check("其余按价格升序", prices == sorted(prices), "实际 %s" % prices))
        else:
            kinds = [r["plan"]["kinds"] for r in rest]
            res.append(check("其余按品类降序", kinds == sorted(kinds, reverse=True),
                             "实际 %s" % kinds))
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description="order_helper 端到端自测")
    ap.add_argument("--store", required=True, help="用于测试的门店 storeCode")
    ap.add_argument("--quick", action="store_true", help="只跑核心 4 组")
    ap.add_argument("--warmup", action="store_true", help="先跑一次预热缓存")
    args = ap.parse_args()

    if args.warmup:
        print("预热缓存…", flush=True)
        run_case(args.store, 1, "no", "cheap", 0, "no", verify=0)

    cases: list[tuple[int, str, str, int, str]] = []
    for staple in (1, 2, 3):
        for pref in ("cheap", "variety"):
            cases.append((staple, "no", pref, 0, "no"))
    if not args.quick:
        cases += [
            (2, "辣", "cheap", 0, "no"),
            (2, "辣", "variety", 0, "no"),
            (2, "no", "variety", 2, "no"),
            (2, "no", "variety", 1, "yes"),
            (4, "no", "cheap", 0, "no"),
        ]

    total = passed = 0
    failures: list[str] = []
    for staple, spicy, pref, kids, card in cases:
        tag = "N=%d %s %s kids=%d card=%s" % (staple, spicy, pref, kids, card)
        print("── 用例 %s" % tag, flush=True)
        code, data, err = run_case(args.store, staple, spicy, pref, kids, card)
        checks: list[tuple[bool, str]] = [check("退出码=0", code == 0, "code=%d %s" % (code, err[-200:]))]
        if data:
            want_spicy = spicy != "no"
            base = data.get("baseline") or {}
            checks += verify_plan(base, staple, kids, want_spicy, "基准")
            for rec in data.get("recommendations") or []:
                checks += verify_plan(rec["plan"], rec["targetStaple"], kids, want_spicy,
                                      "R%d" % rec["rank"])
            checks += verify_order(data, staple, pref)
        for ok, msg in checks:
            total += 1
            if ok:
                passed += 1
            else:
                failures.append("%s | %s" % (tag, msg))
        bad = [m for ok, m in checks if not ok]
        print("   %s（%d 项）" % ("全部通过" if not bad else "发现 %d 项失败" % len(bad)
                                 , len(checks)))

    print("\n==== 汇总：%d/%d 项通过 ====" % (passed, total))
    if failures:
        print("失败明细：")
        for f in failures:
            print("  - " + f)
        return 1
    print("全部用例通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
