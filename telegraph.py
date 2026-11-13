"""Publish translated articles to Telegraph (telegra.ph) for in-app Instant View reading.

Note: Telegraph pages are public to anyone with the link and cannot be deleted (only edited).
Only CC-licensed open-access articles are published; each page carries attribution.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import requests

API = "https://api.telegra.ph"
ENV_FILE = Path(__file__).parent / ".env"
AUTHOR_NAME = "Think 中文译文"
MAX_BYTES = 60_000  # Telegraph limit is 64 KB of content JSON per page


def _call(method: str, **params) -> dict:
    r = requests.post(f"{API}/{method}", data=params, timeout=60)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegraph {method} failed: {data.get('error')}")
    return data["result"]


def _token() -> str:
    tok = os.environ.get("TELEGRAPH_TOKEN")
    if tok:
        return tok
    acc = _call("createAccount", short_name="ThinkZH", author_name=AUTHOR_NAME)
    tok = acc["access_token"]
    with ENV_FILE.open("a") as f:  # persist so all pages live under one account
        f.write(f"\nTELEGRAPH_TOKEN={tok}\n")
    os.environ["TELEGRAPH_TOKEN"] = tok
    return tok


# ---- Markdown subset -> Telegraph nodes -------------------------------------------

_INLINE = re.compile(r"\*\*(.+?)\*\*|\*(.+?)\*|\[([^\]]+)\]\((https?://[^)\s]+)\)")


def _inline(text: str) -> list:
    out, pos = [], 0
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            out.append(text[pos:m.start()])
        if m.group(1) is not None:
            out.append({"tag": "strong", "children": _inline(m.group(1))})
        elif m.group(2) is not None:
            out.append({"tag": "em", "children": _inline(m.group(2))})
        else:
            out.append({"tag": "a", "attrs": {"href": m.group(4)}, "children": [m.group(3)]})
        pos = m.end()
    if pos < len(text):
        out.append(text[pos:])
    return out


def md_to_nodes(md: str) -> list:
    nodes: list = []
    bullets: list = []

    def flush():
        if bullets:
            nodes.append({"tag": "ul", "children": list(bullets)})
            bullets.clear()

    for block in re.split(r"\n\s*\n", md.strip()):
        block = " ".join(l.strip() for l in block.strip().splitlines())
        if not block:
            continue
        if block.startswith("- "):
            bullets.append({"tag": "li", "children": _inline(block[2:])})
            continue
        flush()
        if block == "---":
            nodes.append({"tag": "hr"})
        elif m := re.match(r"^(#{1,4})\s+(.*)", block):
            tag = "h3" if len(m.group(1)) <= 2 else "h4"  # Telegraph only has h3/h4
            nodes.append({"tag": tag, "children": _inline(m.group(2))})
        elif block.startswith(">"):
            nodes.append({"tag": "blockquote", "children": _inline(block.lstrip("> "))})
        elif m := re.match(r"^((?:[A-Z]\.){2,3})\s+(.*)", block):  # interview speaker, e.g. "S.L."
            nodes.append({"tag": "p", "children": [{"tag": "strong", "children": [m.group(1)]}, " "]
                          + _inline(m.group(2))})
        else:
            nodes.append({"tag": "p", "children": _inline(block)})
    flush()
    return nodes


def _size(nodes: list) -> int:
    return len(json.dumps(nodes, ensure_ascii=False).encode())


def _split(nodes: list, limit: int) -> list[list]:
    parts, cur = [], []
    for n in nodes:
        if cur and _size(cur + [n]) > limit:
            parts.append(cur)
            cur = []
        cur.append(n)
    parts.append(cur)
    return parts


def publish(meta: dict, title_zh: str, abstract_zh: str, body_zh: str, issue_label: str) -> str:
    """Create the page(s); returns the URL of the first page."""
    head = [
        {"tag": "p", "children": [{"tag": "em", "children": [meta["title"]]}]},
        {"tag": "p", "children": [
            f"作者：{', '.join(meta.get('authors', []))}", {"tag": "br"},
            f"刊载：Think · {issue_label} · {meta.get('date', '')}",
        ]},
    ]
    if abstract_zh:
        head.append({"tag": "blockquote", "children": _inline(abstract_zh)})
    head.append({"tag": "hr"})
    foot = [
        {"tag": "hr"},
        {"tag": "aside", "children": [
            "原文：", {"tag": "a", "attrs": {"href": meta["url"]}, "children": [meta["title"]]},
            f"，{', '.join(meta.get('authors', []))}，Think（Cambridge University Press）。",
            {"tag": "br"},
            "原文以 CC BY 4.0 许可发布；中文为 AI 翻译，仅供阅读参考，以原文为准。",
        ]},
    ]
    body = md_to_nodes(body_zh)
    token = _token()
    parts = _split(body, MAX_BYTES - _size(head) - _size(foot) - 2000)
    urls: list[str] = []
    # Create in reverse so each part can link to the next one.
    for i in range(len(parts) - 1, -1, -1):
        content = (head if i == 0 else []) + parts[i]
        if i + 1 < len(parts):
            content = content + [{"tag": "p", "children": [
                {"tag": "a", "attrs": {"href": urls[0]}, "children": [f"→ 续读（{i + 2}/{len(parts)}）"]}]}]
        else:
            content = content + foot
        title = title_zh if len(parts) == 1 else f"{title_zh}（{i + 1}/{len(parts)}）"
        page = _call("createPage", access_token=token, title=title[:256], author_name=AUTHOR_NAME,
                     author_url=meta["url"], content=json.dumps(content, ensure_ascii=False))
        urls.insert(0, page["url"])
    return urls[0]


# ---- Fixed index page --------------------------------------------------------------
# One page whose URL never changes; it is rebuilt (editPage) after every run so you can
# bookmark it. Its path is stored in .env as TELEGRAPH_INDEX_PATH.

INDEX_TITLE = "Think 中文译文 · 目录"


def _issue_key(label: str) -> tuple[int, int]:
    m = re.match(r"vol(\d+)-issue(\d+)", label)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _teaser(text: str, n: int = 90) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= n:
        return text
    cut = text[:n]
    i = max(cut.rfind(c) for c in "。，；：？！")
    if i < n // 3:  # no good break: avoid cutting inside a Latin word
        i = len(cut.rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")) - 1
    return cut[: i + 1].rstrip("，；：") + "……"


def _index_nodes(out_dir: Path) -> list:
    issues = sorted((p for p in out_dir.glob("*/vol*-issue*") if p.is_dir()),
                    key=lambda p: _issue_key(p.name), reverse=True)
    sections: list = []
    total = 0
    for issue in issues:
        items = []
        for d in sorted(p for p in issue.iterdir() if p.is_dir()):
            page_f, tr_f, meta_f = d / "telegraph.json", d / "translation.json", d / "meta.json"
            if not (page_f.exists() and tr_f.exists() and meta_f.exists()):
                continue
            url = json.loads(page_f.read_text())["url"]
            tr, meta = json.loads(tr_f.read_text()), json.loads(meta_f.read_text())
            items.append((meta.get("date", ""), url, tr, meta))
        if not items:
            continue
        items.sort(key=lambda x: x[0], reverse=True)
        total += len(items)
        vol, no = _issue_key(issue.name)
        dates = sorted(x[0] for x in items)
        sec = [{"tag": "h3", "children": [f"📚 第 {vol} 卷 · 第 {no} 期"]},
               {"tag": "aside", "children": [f"{len(items)} 篇译文 · 本期刊出 {dates[-1]}"]}]
        for k, (_, url, tr, meta) in enumerate(items, 1):
            sec.append({"tag": "h4", "children": [
                {"tag": "a", "attrs": {"href": url}, "children": [f"{k:02d}｜{tr['title_zh']}"]}]})
            sec.append({"tag": "p", "children": [
                {"tag": "em", "children": [meta["title"]]}, {"tag": "br"},
                f"✍️ {', '.join(meta.get('authors', []))}"]})
            if tr.get("abstract_zh"):
                sec.append({"tag": "blockquote", "children": [_teaser(tr["abstract_zh"])]})
            sec.append({"tag": "p", "children": [
                {"tag": "a", "attrs": {"href": url}, "children": [{"tag": "strong", "children": ["阅读全文 →"]}]}]})
        sec.append({"tag": "hr"})
        sections.append(sec)

    head = [
        {"tag": "aside", "children": [{"tag": "strong", "children": ["每期精选 · 哲学思考的中文入口"]}]},
        {"tag": "p", "children": [
            {"tag": "em", "children": ["Think"]},
            " 是英国皇家哲学研究所（Royal Institute of Philosophy）出版的大众哲学期刊，"
            "文章短小、通俗、有趣。这里收录其开放获取文章的中文译文，最新一期在前。"]},
        {"tag": "p", "children": [f"🗂 已收录 {total} 篇　·　🔔 新文章将通过 Telegram 推送"]},
        {"tag": "hr"},
    ]
    foot = [{"tag": "aside", "children": [
        "原文版权归作者及 Cambridge University Press 所有，以 CC BY 4.0 许可发布。", {"tag": "br"},
        "中文为 AI 翻译，仅供阅读参考，请以原文为准。"]}]
    while sections and _size(head + [n for s in sections for n in s] + foot) > MAX_BYTES:
        sections.pop()  # drop oldest issues if it ever gets too big
    return head + [n for s in sections for n in s] + foot


def update_index(out_dir: Path) -> str:
    """Create the index page on first use, otherwise edit it in place. Returns its URL."""
    token = _token()
    content = json.dumps(_index_nodes(out_dir), ensure_ascii=False)
    path = os.environ.get("TELEGRAPH_INDEX_PATH")
    if path:
        page = _call("editPage", access_token=token, path=path, title=INDEX_TITLE,
                     author_name=AUTHOR_NAME, content=content)
    else:
        page = _call("createPage", access_token=token, title=INDEX_TITLE,
                     author_name=AUTHOR_NAME, content=content)
        with ENV_FILE.open("a") as f:
            f.write(f"\nTELEGRAPH_INDEX_PATH={page['path']}\n")
        os.environ["TELEGRAPH_INDEX_PATH"] = page["path"]
    return page["url"]
