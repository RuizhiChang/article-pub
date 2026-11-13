"""Push articles to Telegram via the Bot API.

Setup:
  1. Talk to @BotFather in Telegram, /newbot, copy the token.
  2. Send any message to your bot (or add it to a group/channel as admin).
  3. Put TELEGRAM_BOT_TOKEN=... in .env, then run:
       .venv/bin/python telegram_push.py chat-id
     and put the printed TELEGRAM_CHAT_ID=... in .env too.
     (For a public channel you can use TELEGRAM_CHAT_ID=@channelname.)
"""
from __future__ import annotations

import html
import json
import os
import sys
import time
from pathlib import Path

import requests

ENV_FILE = Path(__file__).parent / ".env"
MSG_LIMIT = 4096


def load_env() -> None:
    """Minimal .env loader (KEY=VALUE lines); real env vars take precedence."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _api(method: str, **kw) -> dict:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set (see telegram_push.py header)")
    for attempt in range(4):
        r = requests.post(f"https://api.telegram.org/bot{token}/{method}", timeout=60, **kw)
        data = r.json()
        if data.get("ok"):
            return data["result"]
        retry = data.get("parameters", {}).get("retry_after")
        if r.status_code == 429 and retry and attempt < 3:
            time.sleep(retry + 1)
            continue
        raise RuntimeError(f"Telegram {method} failed: {data.get('description')}")
    raise RuntimeError("unreachable")


def _chat() -> str:
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not chat:
        raise RuntimeError("TELEGRAM_CHAT_ID not set (run: python telegram_push.py chat-id)")
    return chat


def configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def card(meta: dict, title_zh: str, abstract_zh: str, issue_label: str, read_url: str | None) -> str:
    e = html.escape
    lines = [f"<b>{e(title_zh)}</b>", f"<i>{e(meta['title'])}</i>", ""]
    if meta.get("authors"):
        lines.append(f"✍️ {e(', '.join(meta['authors']))}")
    when = f" · {e(meta['date'])}" if meta.get("date") and meta.get("kind") != "fiction" else ""
    lines.append(f"📖 {e(meta.get('journal') or 'Think')} · {e(issue_label)}{when}")
    if not read_url:
        lines.append("🔒 全文需订阅，以下仅为摘要")
    elif meta.get("kind") == "fiction" and meta.get("teaser_zh"):
        abstract_zh = abstract_zh or meta["teaser_zh"]
    head = "\n".join(lines)
    if read_url:
        orig = "英译底本" if meta.get("kind") == "fiction" else "英文原文"
        tail = f'\n\n👉 <a href="{e(read_url)}"><b>阅读全文译文</b></a>　·　<a href="{e(meta["url"])}">{orig}</a>'
        if os.environ.get("SITE_BASE_URL"):
            tail += f'　·　<a href="{e(os.environ["SITE_BASE_URL"])}/">📚 全部文章</a>'
    else:
        tail = f'\n\n<a href="{e(meta["url"])}">英文原文</a>'
    if abstract_zh:
        room = MSG_LIMIT - len(head) - len(tail) - 40
        ab = abstract_zh if len(abstract_zh) <= room else abstract_zh[: room - 1] + "…"
        head += f"\n\n<blockquote expandable>{e(ab)}</blockquote>"
    return head + tail


def push(text: str, preview_url: str | None = None) -> int:
    """Send an HTML message; if preview_url is given, show it as a large link preview
    (Telegraph links open as Instant View inside Telegram)."""
    if preview_url:
        preview = {"url": preview_url, "prefer_large_media": True, "show_above_text": False}
    else:
        preview = {"is_disabled": True}
    msg = _api("sendMessage", data={"chat_id": _chat(), "text": text, "parse_mode": "HTML",
                                    "link_preview_options": json.dumps(preview)})
    return msg["message_id"]


def clear_chat() -> int:
    """Delete every message in the bot's chat that Telegram still allows (sent < 48 h ago).
    Message ids aren't listable, so send a probe to learn the newest id and sweep down."""
    chat = _chat()
    top = _api("sendMessage", data={"chat_id": chat, "text": "🧹"})["message_id"]
    deleted = 0
    for hi in range(top, 0, -100):
        ids = list(range(max(1, hi - 99), hi + 1))
        try:
            if _api("deleteMessages", data={"chat_id": chat, "message_ids": json.dumps(ids)}):
                deleted += len(ids)
        except RuntimeError:
            pass  # some ids too old / already gone; keep sweeping
    return deleted


def print_chat_ids() -> None:
    seen = set()
    for u in _api("getUpdates"):
        msg = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
        c = msg.get("chat")
        if c and c["id"] not in seen:
            seen.add(c["id"])
            name = c.get("title") or c.get("username") or c.get("first_name")
            print(f"TELEGRAM_CHAT_ID={c['id']}   # {c['type']}: {name}")
    if not seen:
        print("No chats found. Send your bot a message first, then re-run.")


if __name__ == "__main__":
    load_env()
    if sys.argv[1:] == ["chat-id"]:
        print_chat_ids()
    elif sys.argv[1:] == ["clear"]:
        clear_chat()
        print("cleared")
    elif sys.argv[1:] == ["test"]:
        push("✅ Think 推送机器人已连接")
        print("sent")
    else:
        print(__doc__)
