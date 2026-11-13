"""Build a static reading site from output/ into site/.

  .venv/bin/python build_site.py            # writes site/index.html and site/a/<id>.html

Every translated article (output/<journal>/<issue>/<article>/translation.json) gets its own
page; the home page lists all of them, newest first, with a filter per journal.
Pages carry Open Graph tags so Telegram/WhatsApp show a rich link preview.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "output"
SITE = ROOT / "site"
SITE_NAME = os.environ.get("SITE_NAME", "外刊译读")
SITE_TAGLINE = os.environ.get("SITE_TAGLINE", "把好看的外文期刊文章，译成好读的中文")
BASE_URL = os.environ.get("SITE_BASE_URL", "").rstrip("/")  # e.g. https://you.github.io/reader

# Per-journal accent colours (light, dark); new journals get one by hash.
PALETTE = [("#9b3b2f", "#e08a7c"), ("#2f5f8a", "#8cb6e0"), ("#3d6b45", "#93c79c"),
           ("#7a4f8f", "#c7a0dc"), ("#8a6a2f", "#d9b878"), ("#2f7a78", "#86cfcc")]

e = html.escape


# ---- data -----------------------------------------------------------------------------

def load_articles() -> list[dict]:
    arts = []
    for tr_f in OUT.glob("*/*/*/translation.json"):
        d = tr_f.parent
        if (d / ".hidden").exists():  # removed from the site (translation kept on disk)
            continue
        meta = json.loads((d / "meta.json").read_text())
        tr = json.loads(tr_f.read_text())
        journal_slug = meta.get("journal_slug") or d.parent.parent.name
        arts.append({
            **meta, **tr,
            "journal": meta.get("journal") or journal_slug.title(),
            "journal_slug": journal_slug,
            "issue_label": d.parent.name,
            "page": page_for(meta["id"]),
        })
    arts.sort(key=lambda a: (a.get("date", ""), a.get("firstpage", "")), reverse=True)
    return arts


def page_for(art_id: str) -> str:
    """Site path of an item: journal articles use a short hex id, stories their slug."""
    if re.fullmatch(r"[0-9A-Fa-f]{32}", art_id):
        return f"a/{art_id[:12].lower()}.html"
    return f"a/{re.sub(r'[^a-z0-9-]+', '-', art_id.lower())}.html"


def is_fiction(a: dict) -> bool:
    return a.get("kind") == "fiction"


def byname(a: dict) -> str:
    """Author line: Chinese name first for stories."""
    names = ", ".join(a.get("authors", []))
    return f"{a['author_zh']}（{names}）" if a.get("author_zh") else names


def blurb(a: dict) -> str:
    return a.get("abstract_zh") or a.get("teaser_zh") or a.get("body_zh", "")


def accent(slug: str) -> tuple[str, str]:
    return PALETTE[int(hashlib.md5(slug.encode()).hexdigest(), 16) % len(PALETTE)]


def issue_text(a: dict) -> str:
    if a.get("source_label"):
        return a["source_label"]
    m = re.match(r"vol(\d+)-issue(\d+)", a["issue_label"])
    return f"第 {m.group(1)} 卷 · 第 {m.group(2)} 期" if m else a["issue_label"]


def reading_minutes(text: str) -> int:
    return max(1, round(len(re.sub(r"\s", "", text)) / 450))


def teaser(text: str, n: int = 110) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= n:
        return text
    cut = text[:n]
    i = max(cut.rfind(c) for c in "。；！？")
    return (cut[: i + 1] if i > n // 3 else cut.rstrip("，、：（《“ ")) + ("" if i > n // 3 else "……")


# ---- markdown subset -> HTML ----------------------------------------------------------

def inline(t: str) -> str:
    t = e(t, quote=False)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"\*(.+?)\*", r"<em>\1</em>", t)
    return t


def md_to_html(md: str) -> str:
    out, bullets = [], []

    def flush():
        if bullets:
            out.append("<ul>" + "".join(f"<li>{b}</li>" for b in bullets) + "</ul>")
            bullets.clear()

    for block in re.split(r"\n\s*\n", md.strip()):
        block = " ".join(l.strip() for l in block.strip().splitlines())
        if not block:
            continue
        if block.startswith("- "):
            bullets.append(inline(block[2:]))
            continue
        flush()
        if block == "---":
            out.append("<hr>")
        elif m := re.match(r"^(#{1,4})\s+(.*)", block):
            out.append(f"<h{2 if len(m.group(1)) <= 2 else 3}>{inline(m.group(2))}</h{2 if len(m.group(1)) <= 2 else 3}>")
        elif block.startswith(">"):
            out.append(f"<blockquote>{inline(block.lstrip('> '))}</blockquote>")
        elif m := re.match(r"^((?:[A-Z]\.){2,3})\s+(.*)", block):  # interview speaker
            out.append(f'<p class="qa"><span class="speaker">{m.group(1)}</span>{inline(m.group(2))}</p>')
        elif m := re.match(r"^(\d+)\.\s+(.*)", block):
            out.append(f'<p class="num"><span>{m.group(1)}</span>{inline(m.group(2))}</p>')
        else:
            out.append(f"<p>{inline(block)}</p>")
    flush()
    return "\n".join(out)


# ---- templates ------------------------------------------------------------------------

CSS = """
:root{--bg:#f7f4ee;--card:#fffdf9;--ink:#1f1c18;--soft:#5d574e;--mute:#8f877b;--line:#e6dfd3;
--accent:#9b3b2f;--quote:#f1ebe0;--shadow:0 1px 2px rgba(60,40,10,.05),0 8px 24px rgba(60,40,10,.06);
--serif:"Noto Serif SC","Source Han Serif SC","Songti SC","STSong",Georgia,serif;
--sans:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#15130f;--card:#1d1a16;--ink:#ece6da;
--soft:#bdb4a5;--mute:#8a8274;--line:#2f2a23;--accent:#e08a7c;--quote:#24201a;--shadow:none}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);line-height:1.6}
a{color:inherit;text-decoration:none}
.wrap{max-width:1080px;margin:0 auto;padding:0 20px}
.read{max-width:700px;margin:0 auto;padding:0 20px}
header.top{border-bottom:1px solid var(--line);background:var(--bg);position:sticky;top:0;z-index:5;
backdrop-filter:saturate(1.4) blur(8px)}
header.top .wrap,header.top .read{display:flex;align-items:center;justify-content:space-between;height:56px}
.brand{font-family:var(--serif);font-weight:700;font-size:19px;letter-spacing:.06em}
.brand i{font-style:normal;color:var(--accent)}
.nav-note{font-size:13px;color:var(--mute)}
.hero{padding:56px 0 28px}
.hero h1{font-family:var(--serif);font-size:clamp(32px,6vw,52px);line-height:1.15;margin:0 0 12px;letter-spacing:.02em}
.hero p{margin:0;color:var(--soft);font-size:16px}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin:26px 0 8px}
.chip{border:1px solid var(--line);background:var(--card);border-radius:999px;padding:6px 14px;font-size:14px;
color:var(--soft);cursor:pointer;font-family:inherit}
.chip[aria-pressed=true]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.tag{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;letter-spacing:.08em;
text-transform:uppercase;color:var(--j)}
.tag::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--j)}
.feature{display:grid;grid-template-columns:1.25fr 1fr;gap:0;background:var(--card);border:1px solid var(--line);
border-radius:18px;overflow:hidden;box-shadow:var(--shadow);margin:20px 0 34px;transition:transform .2s}
.feature:hover{transform:translateY(-2px)}
.feature .cover{background:var(--j);color:#fff;padding:36px;display:flex;flex-direction:column;justify-content:space-between;
min-height:300px;position:relative;overflow:hidden}
.feature .cover::after{content:"“";position:absolute;right:-10px;bottom:-90px;font-family:Georgia,serif;font-size:340px;
line-height:1;opacity:.14}
.feature .cover .kicker{font-size:13px;letter-spacing:.2em;opacity:.85}
.feature .cover h2{font-family:var(--serif);font-size:clamp(26px,3.6vw,38px);line-height:1.3;margin:18px 0 0;position:relative;z-index:1}
.feature .body{padding:32px 34px;display:flex;flex-direction:column;gap:14px}
.en{font-family:Georgia,serif;font-style:italic;color:var(--soft);font-size:15px;line-height:1.5}
.by{font-size:14px;color:var(--mute)}
.excerpt{font-family:var(--serif);font-size:16px;line-height:1.85;color:var(--soft);margin:0}
.more{margin-top:auto;font-weight:600;font-size:14px;color:var(--j)}
.section-h{display:flex;align-items:baseline;justify-content:space-between;border-bottom:2px solid var(--ink);
padding-bottom:8px;margin:8px 0 20px}
.section-h h3{font-family:var(--serif);font-size:20px;margin:0}
.section-h span{font-size:13px;color:var(--mute)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:20px;margin-bottom:60px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:24px;display:flex;
flex-direction:column;gap:10px;box-shadow:var(--shadow);border-top:4px solid var(--j);transition:transform .2s}
.card:hover{transform:translateY(-2px)}
.card h4{font-family:var(--serif);font-size:20px;line-height:1.45;margin:4px 0 0}
.card .meta{display:flex;justify-content:space-between;font-size:12.5px;color:var(--mute);margin-top:auto;
padding-top:12px;border-top:1px dashed var(--line)}
footer{border-top:1px solid var(--line);padding:28px 0 48px;color:var(--mute);font-size:13px;line-height:1.8}
/* article */
.progress{position:fixed;top:0;left:0;height:3px;background:var(--j);width:0;z-index:10}
.art-head{padding:44px 0 8px}
.art-head h1{font-family:var(--serif);font-size:clamp(28px,5vw,40px);line-height:1.3;margin:14px 0 10px}
.art-head .en{font-size:17px}
.byline{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:14px;color:var(--mute);margin:18px 0 0;padding:14px 0;
border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.byline b{color:var(--ink);font-weight:600}
.abstract{background:var(--quote);border-radius:12px;padding:20px 22px;margin:28px 0 8px;font-family:var(--serif);
font-size:16px;line-height:1.85;color:var(--soft)}
.abstract strong{display:block;font-family:var(--sans);font-size:12px;letter-spacing:.2em;color:var(--j);margin-bottom:6px}
article.prose{font-family:var(--serif);font-size:18px;line-height:2;letter-spacing:.01em;padding:12px 0 20px}
.prose p{margin:0 0 1.2em}
.prose h2{font-size:23px;line-height:1.4;margin:2em 0 .8em;padding-left:12px;border-left:4px solid var(--j)}
.prose h3{font-size:19px;margin:1.6em 0 .6em}
.prose blockquote{margin:1.4em 0;padding:4px 0 4px 18px;border-left:3px solid var(--line);color:var(--soft)}
.prose hr{border:0;text-align:center;margin:2em 0}.prose hr::after{content:"· · ·";color:var(--mute);letter-spacing:.6em}
.prose a{color:var(--j);border-bottom:1px solid currentColor}
.prose .qa .speaker{display:inline-block;font-family:var(--sans);font-size:12px;font-weight:700;color:#fff;
background:var(--j);border-radius:4px;padding:1px 6px;margin-right:10px;vertical-align:2px}
.prose .num{padding-left:2em;position:relative}.prose .num span{position:absolute;left:0;color:var(--j);font-weight:700}
.prose.fiction p{text-indent:2em;margin:0 0 .6em}
.nav-home{font-size:14px;font-weight:600;color:var(--ink);border:1px solid var(--line);background:var(--card);
border-radius:999px;padding:5px 14px}
.fab{position:fixed;right:18px;bottom:22px;z-index:9;display:flex;align-items:center;gap:6px;background:var(--ink);
color:var(--bg);border-radius:999px;padding:11px 18px;font-size:15px;font-weight:600;box-shadow:0 6px 20px rgba(0,0,0,.18);
transition:opacity .25s,transform .25s}
.fab.hide{opacity:0;transform:translateY(12px);pointer-events:none}
.source{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px 24px;margin:20px 0 40px;
font-size:14px;color:var(--soft);line-height:1.9}
.source a{color:var(--j)}
.back{display:inline-block;margin:0 0 50px;font-size:14px;font-weight:600;color:var(--j)}
@media (max-width:720px){.feature{grid-template-columns:1fr}.feature .cover{min-height:220px;padding:28px}
.feature .body{padding:24px}.hero{padding:36px 0 16px}article.prose{font-size:17px;line-height:1.95}}
"""


def page(title: str, desc: str, body: str, url: str = "", extra_head: str = "") -> str:
    og_url = f'<meta property="og:url" content="{e(BASE_URL + "/" + url)}">' if BASE_URL else ""
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="build" content="{BUILD_ID}">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(desc)}">
<meta property="og:type" content="article"><meta property="og:site_name" content="{e(SITE_NAME)}">
<meta property="og:title" content="{e(title)}"><meta property="og:description" content="{e(desc)}">{og_url}
<style>{CSS}</style>{extra_head}</head>
<body>{body}</body></html>"""


def jstyle(a: dict) -> str:
    light, dark = accent(a["journal_slug"])
    return f'--j:{light}" data-j-dark="{dark}'


THEME_JS = """<script>
// swap journal accents for dark mode
if(matchMedia('(prefers-color-scheme: dark)').matches)document.querySelectorAll('[data-j-dark]').forEach(el=>el.style.setProperty('--j',el.dataset.jDark));
</script>"""


def build_home(arts: list[dict]) -> str:
    journals = sorted({(a["journal_slug"], a["journal"]) for a in arts}, key=lambda x: x[1])
    chips = '<button class="chip" aria-pressed="true" data-f="*">全部</button>' + "".join(
        f'<button class="chip" aria-pressed="false" data-f="{e(s)}">{e(n)}</button>' for s, n in journals)
    feat = ""
    rest = arts
    if arts:
        a, rest = arts[0], arts[1:]
        feat = f"""<a class="feature" href="{a['page']}" data-j="{e(a['journal_slug'])}" style="{jstyle(a)}">
  <div class="cover"><div class="kicker">最新译文 · {e(a['journal'])}</div><h2>{e(a['title_zh'])}</h2></div>
  <div class="body"><div class="en">{e(a['title'])}</div>
  <div class="by">{e(byname(a))} · {e(issue_text(a))}</div>
  <p class="excerpt">{e(teaser(blurb(a), 150))}</p>
  <div class="more">阅读全文（约 {reading_minutes(a.get('body_zh') or a.get('abstract_zh', ''))} 分钟）→</div></div></a>"""
    cards = "".join(f"""<a class="card" href="{a['page']}" data-j="{e(a['journal_slug'])}" style="{jstyle(a)}">
  <span class="tag">{e(a['journal'])}</span><h4>{e(a['title_zh'])}</h4>
  <div class="en">{e(a['title'])}</div>
  <p class="excerpt">{e(teaser(blurb(a), 80))}</p>
  <div class="meta"><span>{e(a.get('author_zh') or ', '.join(a.get('authors', [])[:2]))}</span><span>{reading_minutes(a.get('body_zh') or a.get('abstract_zh', ''))} 分钟</span></div></a>"""
                    for a in rest)
    body = f"""<header class="top"><div class="wrap"><a class="brand" href="index.html">{e(SITE_NAME)}<i>.</i></a>
<span class="nav-note">已收录 {len(arts)} 篇 · {len(journals)} 个栏目</span></div></header>
<main class="wrap">
<section class="hero"><h1>{e(SITE_NAME)}</h1><p>{e(SITE_TAGLINE)}</p>
<div class="chips" role="group" aria-label="按栏目筛选">{chips}</div></section>
{feat}
<div class="section-h"><h3>更多文章</h3><span>按收录时间排序</span></div>
<div class="grid">{cards or '<p class="by">更多文章即将推出。</p>'}</div>
</main>
<footer><div class="wrap">译文由 AI 生成，仅供阅读参考，请以原文为准。仅收录以 CC BY 等开放许可发布、或已进入公有领域的作品；原文版权归作者及出版方所有。</div></footer>
{THEME_JS}
<script>
document.querySelectorAll('.chip').forEach(c=>c.onclick=()=>{{
  document.querySelectorAll('.chip').forEach(x=>x.setAttribute('aria-pressed',x===c));
  const f=c.dataset.f;
  document.querySelectorAll('[data-j]').forEach(el=>el.style.display=(f==='*'||el.dataset.j===f)?'':'none');
}});
</script>"""
    return page(f"{SITE_NAME} · {SITE_TAGLINE}", SITE_TAGLINE, body, "index.html")


def build_article(a: dict) -> str:
    body_html = md_to_html(a["body_zh"]) if a.get("body_zh") else (
        '<p>本文全文需订阅阅读，以上为中文摘要。</p>')
    abstract = (f'<div class="abstract"><strong>导 读</strong>{e(a["abstract_zh"])}</div>'
                if a.get("abstract_zh") else "")
    if is_fiction(a):
        source = (f'英译底本：<a href="{e(a["url"])}">{e(a["title"])}</a>（{e(", ".join(a.get("authors", [])))}，'
                  f'Standard Ebooks 版，CC0）<br>原作在美国已进入公有领域。中文据英译本由 AI 转译，'
                  f'仅供阅读欣赏，与通行中译本可能有出入。')
    doi = f' · DOI <a href="https://doi.org/{e(a["doi"])}">{e(a["doi"])}</a>' if a.get("doi") else ""
    if not is_fiction(a):
        source = (f'原文：<a href="{e(a["url"])}">{e(a["title"])}</a><br>'
                  f'{e(", ".join(a.get("authors", [])))}，<i>{e(a["journal"])}</i>，{issue_text(a)}{doi}<br>'
                  f'原文以 CC BY 4.0 许可发布。中文为 AI 翻译，仅供阅读参考，请以原文为准。')
    body = f"""<div class="progress" style="{jstyle(a)}"></div>
<header class="top"><div class="read"><a class="brand" href="../index.html">{e(SITE_NAME)}<i>.</i></a>
<a class="nav-home" href="../index.html">☰ 全部文章</a></div></header>
<a class="fab" href="../index.html" aria-label="返回目录">☰<span>目录</span></a>
<main class="read" style="{jstyle(a)}">
<div class="art-head"><span class="tag">{e(a['journal'])} · {e(issue_text(a))}</span>
<h1>{e(a['title_zh'])}</h1><div class="en">{e(a['title'])}</div>
<div class="byline"><span>作者 <b>{e(byname(a))}</b></span>
<span>{"收录" if is_fiction(a) else "刊出"} {e(a.get('date', ''))}</span><span>阅读约 {reading_minutes(a.get('body_zh') or a.get('abstract_zh', ''))} 分钟</span></div></div>
{abstract}
<article class="prose{' fiction' if is_fiction(a) else ''}">{body_html}</article>
<div class="source">{source}</div>
<a class="back" href="../index.html">← 返回首页</a>
</main>
{THEME_JS}
<script>
const bar=document.querySelector('.progress');
const fab=document.querySelector('.fab');let lastY=0;
addEventListener('scroll',()=>{{const h=document.documentElement,y=h.scrollTop;
bar.style.width=(y/(h.scrollHeight-h.clientHeight)*100)+'%';
fab.classList.toggle('hide',y>lastY&&y>200&&y<h.scrollHeight-h.clientHeight-80);lastY=y}},{{passive:true}});
</script>"""
    desc = teaser(blurb(a), 100)
    return page(f"{a['title_zh']} | {SITE_NAME}", desc, body, a["page"])


BUILD_ID = ""


def build() -> list[dict]:
    global BUILD_ID
    import time
    BUILD_ID = str(int(time.time()))
    arts = load_articles()
    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "a").mkdir(parents=True)
    (SITE / "index.html").write_text(build_home(arts))
    for a in arts:
        (SITE / a["page"]).write_text(build_article(a))
    (SITE / ".nojekyll").write_text("")
    return arts


if __name__ == "__main__":
    arts = build()
    print(f"Built {len(arts)} articles into {SITE}")
