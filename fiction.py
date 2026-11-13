"""Fetch, translate and publish public-domain short stories listed in fiction_sources.json.

  .venv/bin/python fiction.py --list                 # show the reading list and status
  .venv/bin/python fiction.py --fetch                # download originals only (no translation)
  .venv/bin/python fiction.py gogol-the-nose ...     # translate + publish (+ push) these stories
  .venv/bin/python fiction.py --next 1               # translate + publish the next N untranslated
  options: --no-push

Stories are stored like journal articles, under output/fiction/<collection>/<story>/, so the
website and Telegram push treat them the same way (category "魔幻现实短篇").
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")

from bs4 import BeautifulSoup, NavigableString, Tag  # noqa: E402

import telegram_push as tg  # noqa: E402
from scraper import fetch  # noqa: E402

ROOT = Path(__file__).parent
OUT = ROOT / "output" / "fiction"
SOURCES = ROOT / "fiction_sources.json"
SE = "https://standardebooks.org/ebooks/"
CATEGORY, CATEGORY_SLUG = "魔幻现实短篇", "fiction"
_cache: dict[str, BeautifulSoup] = {}


def catalog() -> list[dict]:
    items = []
    for c in json.loads(SOURCES.read_text())["collections"]:
        author_slug = c["book"].split("/")[0]
        for sid in c["stories"]:
            key = f"{c.get('prefix') or author_slug.split('-')[-1]}-{sid}"  # e.g. gogol-the-nose
            items.append({**c, "story": sid, "key": key,
                          "dir": OUT / author_slug / sid})
    return items


def _md(el: Tag) -> str:
    """Inline HTML -> Markdown (italics only; drop note references)."""
    out = []
    for n in el.children:
        if isinstance(n, NavigableString):
            out.append(str(n))
        elif n.name == "a" and "noteref" in (n.get("epub:type") or n.get("data-epub-type") or ""):
            continue
        elif n.name in ("i", "em"):
            t = _md(n).strip()
            out.append(f"*{t}*" if t else "")
        elif n.name == "br":
            out.append("  \n")
        else:
            out.append(_md(n))
    return re.sub(r"[ \t]+", " ", "".join(out))


def extract(book: str, story_id: str) -> tuple[str, str]:
    """Return (english_title, markdown_body) for one story of a Standard Ebooks book."""
    if book not in _cache:
        _cache[book] = BeautifulSoup(fetch(SE + book + "/text/single-page"), "html.parser")
        time.sleep(1)
    art = _cache[book].find("article", id=story_id)
    if art is None:
        raise KeyError(f"{story_id} not found in {book}")
    head = art.find(["hgroup", "h2", "h3"])
    title_el = head.find(["h2", "h3"]) if head and head.name == "hgroup" else head
    title = title_el.get_text(" ", strip=True) if title_el else story_id
    if head:
        head.decompose()
    blocks = []
    for el in art.find_all(["p", "h3", "h4", "blockquote", "hr"]):
        if el.find_parent("blockquote") and el.name != "blockquote":
            continue
        if el.name == "hr":
            blocks.append("---")
        elif el.name in ("h3", "h4"):
            blocks.append("## " + el.get_text(" ", strip=True))
        elif el.name == "blockquote":
            lines = [_md(p).strip() for p in el.find_all("p")] or [_md(el).strip()]
            blocks.append("\n".join("> " + l for l in lines if l))
        else:
            t = _md(el).strip()
            if t:
                blocks.append(t)
    return title, "\n\n".join(blocks)


def fetch_story(it: dict) -> dict:
    d = it["dir"]
    meta_f = d / "meta.json"
    if meta_f.exists():
        return json.loads(meta_f.read_text())
    title, body = extract(it["book"], it["story"])
    meta = {
        "id": it["key"], "kind": "fiction",
        "url": f"{SE}{it['book']}/text/single-page#{it['story']}",
        "title": title, "authors": [it["author"]], "author_zh": it["author_zh"],
        "journal": CATEGORY, "journal_slug": CATEGORY_SLUG, "source_label": it["label_zh"],
        "date": time.strftime("%Y/%m/%d"),  # date added; used for "newest first"
        "abstract": "", "body_md": body,
        "license": "pd",
    }
    d.mkdir(parents=True, exist_ok=True)
    meta_f.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    (d / "original.md").write_text(f"# {title}\n\n*{it['author']}*\n\n{body}\n")
    return meta


def teaser(body_zh: str, n: int = 120) -> str:
    text = re.sub(r"[*#>\s]+", " ", body_zh).strip()
    if len(text) <= n:
        return text
    cut = text[:n]
    i = max(cut.rfind(c) for c in "。！？")
    return cut[: i + 1] if i > n // 3 else cut + "……"


def translate_story(d: Path, meta: dict) -> dict:
    tr_f = d / "translation.json"
    if tr_f.exists():
        return json.loads(tr_f.read_text())
    from translator import translate

    t0 = time.time()
    title_zh = translate(meta["title"], style="fiction").strip("《》 ")
    body_zh = translate(meta["body_md"], style="fiction")
    tr = {"title_zh": title_zh, "abstract_zh": "", "body_zh": body_zh,
          "teaser_zh": teaser(body_zh)}
    tr_f.write_text(json.dumps(tr, ensure_ascii=False, indent=2))
    (d / "translated.md").write_text(f"# {title_zh}\n\n{meta['author_zh']}\n\n{body_zh}\n")
    print(f"    translated in {time.time() - t0:.0f}s")
    return tr


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("keys", nargs="*")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--next", type=int, default=0)
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--once-per-day", action="store_true",
                    help="do nothing if today's (UTC+8) daily release already happened")
    args = ap.parse_args()
    tg.load_env()
    items = catalog()

    if args.list or args.fetch:
        for it in items:
            if args.fetch:
                fetch_story(it)
            m = it["dir"] / "meta.json"
            words = len(json.loads(m.read_text())["body_md"].split()) if m.exists() else 0
            state = ("已推送" if (it["dir"] / ".pushed").exists() else
                     "已翻译" if (it["dir"] / "translation.json").exists() else
                     "已抓取" if m.exists() else "—")
            print(f"{it['key']:<48} {it['author_zh']:<10} {words:>6} 词  {state}")
        return 0

    import datetime
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d")
    daily_f = OUT.parent / ".last_daily_release"
    if args.once_per_day and daily_f.exists() and daily_f.read_text().strip() == today:
        print(f"today's release ({today}) already done, skipping")
        return 0

    if args.keys:
        chosen = [it for it in items if it["key"] in args.keys]
        missing = set(args.keys) - {it["key"] for it in chosen}
        if missing:
            print(f"unknown: {', '.join(missing)} (see --list)")
            return 1
    else:
        chosen = [it for it in items if not (it["dir"] / "translation.json").exists()][: args.next]

    import main as pipeline  # reuse site build + deploy + push

    pending, failures = [], 0
    for it in chosen:
        print(f"{it['key']}")
        try:
            meta = fetch_story(it)
            tr = translate_story(it["dir"], meta)
            if not (it["dir"] / ".pushed").exists():
                pending.append((it["dir"], meta, tr))
        except Exception as e:
            failures += 1
            print(f"    FAILED: {type(e).__name__}: {e}", file=sys.stderr)
    failures += pipeline.publish_and_push(pending, CATEGORY, not args.no_push and tg.configured())
    if args.once_per_day and not failures:
        daily_f.write_text(today + "\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
