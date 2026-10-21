#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦当劳 MCP 的轻量 HTTP 客户端（零第三方依赖）。

MCP 端点与凭证从 ~/.workbuddy/mcp.json 的 mcpServers.mcd-mcp 读取，
也可用环境变量 MCD_MCP_URL / MCD_MCP_TOKEN 覆盖。
不把凭证写死在任何脚本里。

用法：
    from mcd_client import MCDClient
    cli = MCDClient()
    stores = cli.stores(be_type=1, search_type=2, city="上海", keyword="徐家汇")
    menu = cli.meals(store_code)          # 菜单
    detail = cli.meal_detail(store_code, code)
    price = cli.calculate_price(store_code, [("9900005456", 1)])
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path


class MCDError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# 配置解析
# --------------------------------------------------------------------------- #
def _config_candidates():
    """按平台给出 mcp.json 的可能位置（Windows / macOS / Linux 都试）。"""
    home = Path.home()
    yield home / ".workbuddy" / "mcp.json"
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            yield Path(appdata) / "workbuddy" / "mcp.json"
    else:
        yield home / ".config" / "workbuddy" / "mcp.json"
        yield home / "Library" / "Application Support" / "workbuddy" / "mcp.json"


def load_endpoint() -> tuple[str, str]:
    """返回 (url, token)。环境变量优先，其次读 mcp.json。"""
    url = (os.environ.get("MCD_MCP_URL") or "").strip()
    token = (os.environ.get("MCD_MCP_TOKEN") or "").strip()
    if url and token:
        return url, token

    for path in _config_candidates():
        try:
            if not path or not path.is_file():
                continue
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        servers = cfg.get("mcpServers") or {}
        srv = servers.get("mcd-mcp") or {}
        if not srv:
            continue
        u = url or str(srv.get("url") or "").strip()
        auth = token
        if not auth:
            headers = srv.get("headers") or {}
            raw = str(headers.get("Authorization") or headers.get("authorization") or "")
            auth = raw[7:].strip() if raw.lower().startswith("bearer ") else raw.strip()
        if u and auth:
            return u, auth

    raise MCDError(
        "未找到麦当劳 MCP 端点/凭证。二选一：\n"
        "  A. 在 WorkBuddy 里连接「麦当劳」MCP 连接器（会自动写入 ~/.workbuddy/mcp.json）；\n"
        "  B. 手动配置 ~/.workbuddy/mcp.json：\n"
        '     {"mcpServers":{"mcd-mcp":{"type":"streamablehttp","url":"https://mcp.mcd.cn",\n'
        '       "headers":{"Authorization":"Bearer <你的TOKEN>"}}}}\n'
        "  也可用环境变量 MCD_MCP_URL / MCD_MCP_TOKEN 覆盖。"
    )


# --------------------------------------------------------------------------- #
# 响应解析
# --------------------------------------------------------------------------- #
_DECODER = json.JSONDecoder()


def _parse_body(raw: str) -> dict:
    """支持普通 JSON 与 SSE 两种响应体。"""
    text = raw.strip()
    if not text:
        raise MCDError("空响应")
    if text.startswith("event:") or text.startswith("data:"):
        chunks = []
        for line in text.splitlines():
            if line.startswith("data:"):
                chunks.append(line[5:].strip())
        text = "".join(chunks) or text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # SSE 多帧 / 尾部附加说明：取第一个可解析对象
        idx = text.find("{")
        if idx < 0:
            raise MCDError(f"无法解析响应: {text[:200]}")
        obj, _ = _DECODER.raw_decode(text, idx)
        return obj


def _extract_payload(text: str) -> dict:
    """从工具返回的说明性文本里挖出原始 JSON 业务数据。"""
    if not text:
        return {}
    for marker in ('{"success"', '## Original Response'):
        idx = text.find(marker)
        if idx >= 0:
            start = text.find("{", idx)
            if start >= 0:
                try:
                    obj, _ = _DECODER.raw_decode(text, start)
                    return obj
                except json.JSONDecodeError:
                    continue
    start = text.find("{")
    if start >= 0:
        try:
            obj, _ = _DECODER.raw_decode(text, start)
            return obj
        except json.JSONDecodeError:
            pass
    return {"_raw_text": text}


# --------------------------------------------------------------------------- #
# 客户端
# --------------------------------------------------------------------------- #
class MCDClient:
    def __init__(self, timeout: int = 40, retries: int = 2, verbose: bool = False):
        self.url, self.token = load_endpoint()
        self.timeout = timeout
        self.retries = retries
        self.verbose = verbose
        self._seq = 0

    # -- 底层 ---------------------------------------------------------------
    def _post(self, payload: dict, timeout: float | None = None,
              retries: int | None = None) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.token}",
        }
        tmo = self.timeout if timeout is None else timeout
        tries = self.retries if retries is None else retries
        last_err: Exception | None = None
        for attempt in range(tries + 1):
            try:
                req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=tmo) as resp:
                    raw = resp.read().decode("utf-8", "replace")
                return _parse_body(raw)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_err = exc
                if attempt < tries:
                    time.sleep(0.8 * (attempt + 1))
        raise MCDError(f"MCP 请求失败: {last_err}")

    def call(self, tool: str, args: dict, timeout: float | None = None,
             retries: int | None = None) -> dict:
        self._seq += 1
        resp = self._post({
            "jsonrpc": "2.0",
            "id": self._seq,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }, timeout=timeout, retries=retries)
        if "error" in resp:
            raise MCDError(f"{tool} 调用错误: {resp['error']}")
        result = resp.get("result") or {}
        content = result.get("content") or []
        text = "\n".join(
            c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"
        )
        if not text and isinstance(result.get("structuredContent"), dict):
            return result["structuredContent"]
        return _extract_payload(text)

    # -- 业务封装 -----------------------------------------------------------
    @staticmethod
    def _order_args(store_code: str, be_type: int, order_type: int | None = None,
                    be_code: str | None = None, reservation: str | None = None) -> dict:
        if order_type is None:
            order_type = 2 if be_type in (2, 6) else 1
        args: dict = {"storeCode": str(store_code), "orderType": order_type, "beType": be_type}
        if be_code:
            args["beCode"] = be_code
        if reservation:
            args["reservationDate"] = reservation
        return args

    def stores(self, be_type: int = 1, search_type: int = 1,
               city: str | None = None, keyword: str | None = None) -> list[dict]:
        args: dict = {"beType": be_type, "searchType": search_type}
        if search_type == 2:
            args["city"] = city or ""
            args["keyword"] = keyword or ""
        payload = self.call("query-nearby-stores", args)
        return payload.get("data") or []

    def meals(self, store_code: str, be_type: int = 1, order_type: int | None = None,
              be_code: str | None = None, reservation: str | None = None) -> dict:
        return self.call("query-meals", self._order_args(store_code, be_type, order_type, be_code, reservation))

    def meal_detail(self, store_code: str, code: str, be_type: int = 1,
                    order_type: int | None = None, be_code: str | None = None,
                    timeout: float | None = 15, retries: int | None = 0) -> dict:
        args = self._order_args(store_code, be_type, order_type, be_code)
        args["code"] = str(code)
        return self.call("query-meal-detail", args, timeout=timeout, retries=retries)

    def calculate_price(self, store_code: str, items: list[tuple[str, int]], be_type: int = 1,
                        order_type: int | None = None, be_code: str | None = None) -> dict:
        args = self._order_args(store_code, be_type, order_type, be_code)
        args["items"] = [{"productCode": str(c), "quantity": int(q)} for c, q in items]
        return self.call("calculate-price", args)

    def store_coupons(self, store_code: str, be_type: int = 1, order_type: int | None = None) -> dict:
        return self.call("query-store-coupons", self._order_args(store_code, be_type, order_type))


if __name__ == "__main__":
    c = MCDClient()
    for s in c.stores():
        print(s.get("storeCode"), s.get("storeName"), s.get("businessStatus"))
