"""Scrape a Cambridge Core issue of *Think*, translate each article, push to Telegram.

Usage:
  .venv/bin/python main.py                  # latest issue: scrape → translate → push
  .venv/bin/python main.py --issue URL      # a specific issue
  options: --limit N  --no-translate  --no-push  --lang 简体中文  --index-only

After each run the fixed Telegraph index page (TELEGRAPH_INDEX_PATH in .env) is
rebuilt, so the same URL always lists every translated article.

Output per article under output/<journal>/<volX-issueY>/<slug>/:
  original.md, meta.json, translated.md, translation.json, .pushed
Every step is skipped if its output already exists, so the script is safe to run on a
schedule: it only translates/pushes what is new.
"""
from __future__ import annotations

import argparse
import warnings

warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")

import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from dataclasses import asdict, is_dataclass  # noqa: E402
from pathlib import Path  # noqa: E402

import telegram_push as tg  # noqa: E402
import telegraph  # noqa: E402
import deploy  # noqa: E402
import build_site as site  # noqa: E402
from scraper import LATEST, Article, get_article, list_issue  # noqa: E402

OUT = Path(__file__).parent / "output"


def slug(s: str, n: int = 60) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:n] or "untitled"


def render(art: Article, title: str, body: str, abstract: str, labels: tuple[str, str, str]) -> str:
    out = "\n".join([
        f"# {title}", "",
        f"**{labels[0]}**: {', '.join(art.authors)}  ",
        f"*{art.journal or 'Think'}* Vol. {art.volume}, Issue {art.issue} · {art.date} · [DOI {art.doi}](https://doi.org/{art.doi})  ",
        f"{labels[1]}: {art.url}",
    ])
    if abstract:
        out += f"\n\n## {labels[2]}\n\n{abstract}"
    if body:
        out += f"\n\n---\n\n{body}"
    return out + "\n"


def wait_until_push_time(lead_minutes: int = 0) -> None:
    """If PUSH_AT=HH:MM (Beijing time, UTC+8) is set and still ahead today, sleep until then,
    so a run started early (to absorb GitHub's cron delays and translation time) pushes on time."""
    import datetime
    at = os.environ.get("PUSH_AT")
    if not at:
        return
    tz = datetime.timezone(datetime.timedelta(hours=8))
    now = datetime.datetime.now(tz)
    h, mnt = map(int, at.split(":"))
    target = now.replace(hour=h, minute=mnt, second=0, microsecond=0) - datetime.timedelta(minutes=lead_minutes)
    wait = (target - now).total_seconds()
    if 0 < wait < 6 * 3600:
        print(f"    holding until {target:%H:%M} (UTC+8), {wait / 60:.0f} min")
        time.sleep(wait)


def publish_and_push(pending: list, label: str, do_push: bool) -> int:
    """Rebuild the site (or Telegraph pages), then send one Telegram card per new item.
    pending: (dir, Article-or-meta-dict, translation dict) tuples."""
    failures = 0
    use_site = deploy.configured()
    base = os.environ.get("SITE_BASE_URL", "")
    items = [(d, asdict(a) if is_dataclass(a) else dict(a), tr) for d, a, tr in pending]
    if do_push and items:
        wait_until_push_time(lead_minutes=2 if use_site else 0)
    if use_site and items:  # nothing new -> no rebuild/deploy
        try:
            site.build()
            if deploy.deploy(f"Add {len(items)} item(s)") and items:
                ok = all(deploy.wait_live(f"{base}/{site.page_for(m['id'])}", site.BUILD_ID) for _, m, _ in items)
                print("    waiting for GitHub Pages…", "live" if ok else "timed out")
            print(f"Site: {base}/")
        except Exception as e:
            print(f"    site deploy FAILED: {type(e).__name__}: {e}", file=sys.stderr)
            return 1  # don't push links that may not work
    for d, m, tr in items:
        try:
            read_url = None
            if m.get("body_md"):
                if use_site:
                    read_url = f"{base}/{site.page_for(m['id'])}"
                else:  # fallback: Telegraph page
                    page_f = d / "telegraph.json"
                    if not page_f.exists():
                        url = telegraph.publish(m, tr["title_zh"], tr["abstract_zh"], tr["body_zh"], label)
                        page_f.write_text(json.dumps({"url": url}))
                    read_url = json.loads(page_f.read_text())["url"]
            if do_push:
                card_meta = {**m, "teaser_zh": tr.get("teaser_zh", "")}
                mid = tg.push(tg.card(card_meta, tr["title_zh"], tr["abstract_zh"], m.get("source_label") or label,
                                      read_url), read_url)
                (d / ".pushed").write_text(f"{time.strftime('%Y-%m-%d %H:%M:%S')} message_id={mid}")
                print(f"    pushed: {m['title']}")
                time.sleep(1)
        except Exception as e:
            failures += 1
            print(f"    push FAILED ({m.get('title')}): {type(e).__name__}: {e}", file=sys.stderr)
    if not use_site and items:
        try:
            print(f"Index: {telegraph.update_index(OUT)}")
        except Exception as e:
            failures += 1
            print(f"    index update FAILED: {type(e).__name__}: {e}", file=sys.stderr)
    return failures


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--journal", default="think", help="Cambridge Core journal slug (from its URL)")
    ap.add_argument("--issue", help="issue URL (default: the journal's latest issue)")
    ap.add_argument("--limit", type=int, default=0, help="process at most N articles")
    ap.add_argument("--only", help="process only articles whose id starts with this (e.g. 0D52ADF2)")
    ap.add_argument("--no-translate", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--lang", default="简体中文")
    ap.add_argument("--index-only", action="store_true", help="only rebuild the fixed index page")
    args = ap.parse_args()

    tg.load_env()
    if args.index_only:
        print(f"Index: {telegraph.update_index(OUT)}")
        return 0
    do_push = not args.no_push and not args.no_translate
    if do_push and not tg.configured():
        print("Telegram not configured (.env) – skipping push.")
        do_push = False

    label, refs = list_issue(args.issue or LATEST.format(journal=args.journal))
    paid = [r for r in refs if not r.free]
    refs = [r for r in refs if r.free]  # only free (open-access) articles
    print(f"{label}: {len(refs)} free articles (skipping {len(paid)} paywalled)")
    if args.only:
        refs = [r for r in refs if r.id.upper().startswith(args.only.upper())]
    if args.limit:
        refs = refs[: args.limit]

    issue_dir = OUT / args.journal / label
    failures = 0
    pending: list = []
    for i, ref in enumerate(refs, 1):
        d = issue_dir / f"{slug(ref.title)}-{ref.id[:8]}"
        orig, meta_f = d / "original.md", d / "meta.json"
        trans, trans_f, pushed = d / "translated.md", d / "translation.json", d / ".pushed"
        print(f"[{i}/{len(refs)}] {ref.title}")
        try:
            # 1. scrape
            if not meta_f.exists():
                art = get_article(ref.url)
                d.mkdir(parents=True, exist_ok=True)
                meta_f.write_text(json.dumps(asdict(art), ensure_ascii=False, indent=2))
                orig.write_text(render(art, art.title, art.body_md, art.abstract, ("Author(s)", "Source", "Abstract")))
                if not art.body_md:
                    print("    no open full text; abstract only")
                time.sleep(1.5)  # be polite to the server
            art = Article(**json.loads(meta_f.read_text()))
            if not art.body_md:  # safety net: never translate/push abstract-only articles
                print("    no full text; skipped")
                continue
            if args.no_translate:
                continue

            # 2. translate
            if not trans_f.exists():
                from translator import translate  # lazy: needs API credentials

                t0 = time.time()
                tr = {
                    # The model tends to append the original title in parentheses; we show it separately.
                    "title_zh": re.sub(r"\s*[（(][^（）()]*[A-Za-z][^（）()]*[）)]\s*$", "",
                                       translate(art.title, args.lang)),
                    "abstract_zh": translate(art.abstract, args.lang) if art.abstract else "",
                    "body_zh": translate(art.body_md, args.lang) if art.body_md else "",
                }
                trans.write_text(render(art, f"{tr['title_zh']}（{art.title}）", tr["body_zh"],
                                        tr["abstract_zh"], ("作者", "原文", "摘要")))
                trans_f.write_text(json.dumps(tr, ensure_ascii=False, indent=2))
                print(f"    translated in {time.time() - t0:.0f}s")
            tr = json.loads(trans_f.read_text())

            # 3. push (queued, so the website can be rebuilt/deployed first)
            if do_push and not pushed.exists():
                pending.append((d, art, tr))
        except Exception as e:  # keep going; report at end
            failures += 1
            print(f"    FAILED: {type(e).__name__}: {e}", file=sys.stderr)
    if not args.no_translate:
        failures += publish_and_push(pending, label, do_push)
    print(f"Done. Output in {issue_dir}  ({failures} failures)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
