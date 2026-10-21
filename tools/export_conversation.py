#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 WorkBuddy 的会话 JSONL 导出成可读的 markdown（用于 workbuddy.md）。

用法：
    python tools/export_conversation.py \
        --jsonl "C:/Users/you/.workbuddy/projects/xxx/session.jsonl" \
        --out workbuddy.md

只读取、不改动原文件。工具输出会截断，并在文首说明。
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

REMINDER_RE = re.compile(r"<system-reminder[^>]*>.*?</system-reminder>", re.S)
TAG_RE = re.compile(r"</?(?:additional-data|user-context|user_query|image_local_path)>")


def clean(text: str) -> str:
    text = REMINDER_RE.sub("", text or "")
    text = TAG_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def content_text(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, dict):
            if block.get("text"):
                parts.append(block["text"])
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(parts)


def brief_args(name: str, args) -> str:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return args[:140]
    args = args or {}
    if not isinstance(args, dict):
        return str(args)[:140]
    if name == "Bash":
        cmd = str(args.get("command") or "").strip().splitlines()
        head = cmd[0] if cmd else ""
        return head[:140]
    if name in ("Write", "Edit", "Read"):
        return str(args.get("file_path") or "")[:140]
    if name == "present_files":
        return "%d 个文件" % len(args.get("files") or [])
    for key in ("query", "tool_names", "toolName", "pattern"):
        if args.get(key):
            return str(args[key])[:140]
    return json.dumps(args, ensure_ascii=False)[:140]


def _unwrap(output):
    """工具结果常包成 {"type":"text","text":...} 或 [该结构...]，取出纯文本。"""
    if isinstance(output, str):
        stripped = output.strip()
        if stripped[:1] in "[{":
            try:
                output = json.loads(stripped)
            except json.JSONDecodeError:
                return output
        else:
            return output
    if isinstance(output, dict):
        if isinstance(output.get("text"), str):
            return output["text"]
        for key in ("output", "content", "result"):
            if key in output:
                return _unwrap(output[key])
        return json.dumps(output, ensure_ascii=False)
    if isinstance(output, list):
        parts = [_unwrap(item) for item in output]
        return "\n".join(p for p in parts if p)
    return str(output)


def brief_output(output, limit: int = 300) -> str:
    if output is None:
        return ""
    text = _unwrap(output)
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    text = re.sub(r"\s+\n", "\n", text).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " …（已截断）"


def load(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    rows.sort(key=lambda r: r.get("timestamp") or 0)
    return rows


def export(rows: list[dict]) -> tuple[str, dict]:
    stats = {"user": 0, "assistant": 0, "tool": 0, "reasoning": 0}
    out: list[str] = []
    title = "WorkBuddy 开发对话记录"

    # 先按 callId 把「工具调用」和「工具结果」配对，避免顺序错位
    results: dict[str, str] = {}
    for row in rows:
        if row.get("type") == "function_call_result" and row.get("callId"):
            results[str(row["callId"])] = brief_output(row.get("output"))

    for row in rows:
        kind = row.get("type")
        if kind == "ai-title" and not out:
            title = str(row.get("title") or title)

        if kind == "message":
            role = row.get("role")
            text = clean(content_text(row.get("content")))
            if not text:
                continue
            stamp = datetime.fromtimestamp((row.get("timestamp") or 0) / 1000).strftime("%H:%M")
            if role == "user":
                stats["user"] += 1
                out.append("### 👤 用户 · %s\n\n%s\n" % (stamp, text))
            else:
                stats["assistant"] += 1
                out.append("### 🤖 WorkBuddy · %s\n\n%s\n" % (stamp, text))

        elif kind == "function_call":
            stats["tool"] += 1
            name = row.get("name") or "?"
            desc = row.get("message")
            desc = desc.strip().splitlines()[0][:80] if isinstance(desc, str) and desc.strip() else ""
            line = "- 🔧 **%s**" % name
            if desc:
                line += " — %s" % desc
            line += "\n  - 参数：`%s`" % brief_args(name, row.get("arguments")).replace("`", "'")
            body = results.get(str(row.get("callId")))
            if body:
                line += "\n  - 结果：`%s`" % body.replace("`", "'").replace("\n", " ⏎ ")
            out.append(line)

        elif kind == "reasoning":
            stats["reasoning"] += 1

    head = [
        "# %s" % title,
        "",
        "> 本文件由 `tools/export_conversation.py` 从 WorkBuddy 会话记录（JSONL）自动导出，",
        "> 用于核验本项目确实使用 WorkBuddy 开发。原始记录未做内容修改，",
        "> 仅去除系统提示块，并对过长的工具输出做截断（标注「已截断」）。",
        "",
        "| 项目 | 数量 |",
        "|---|---|",
        "| 用户发言 | %d |" % stats["user"],
        "| WorkBuddy 回复 | %d |" % stats["assistant"],
        "| 工具调用 | %d |" % stats["tool"],
        "| 内部推理段（仅计数，未导出原文） | %d |" % stats["reasoning"],
        "",
        "---",
        "",
    ]
    return "\n".join(head + out), stats


def main() -> int:
    ap = argparse.ArgumentParser(description="导出 WorkBuddy 会话为 markdown")
    ap.add_argument("--jsonl", required=True, help="会话 JSONL 路径")
    ap.add_argument("--out", default="workbuddy.md", help="输出 markdown 路径")
    args = ap.parse_args()

    src = Path(args.jsonl)
    if not src.is_file():
        print("找不到会话文件：%s" % src)
        return 2
    text, stats = export(load(src))
    Path(args.out).write_text(text, encoding="utf-8")
    print("已导出 %s（用户 %d / 回复 %d / 工具 %d）" % (
        args.out, stats["user"], stats["assistant"], stats["tool"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
