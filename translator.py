"""Translate article Markdown with Claude.

Backends (env TRANSLATE_BACKEND):
  claude-cli (default) – runs the local Claude Code CLI (`claude -p`), billed to your
                         Claude subscription (Max). Requires being logged in to Claude Code.
  api                  – Anthropic API via the Python SDK; needs ANTHROPIC_API_KEY.
  codex                – OpenAI Codex CLI (`codex exec`), billed to your ChatGPT subscription.
                         Optional: CODEX_MODEL, CODEX_EFFORT (default medium).
"""
from __future__ import annotations

import json
import re
import os
import shutil
import subprocess
from pathlib import Path

MODEL = "claude-opus-5-5"

SYSTEM = """You are a professional translator of philosophy writing for a general audience \
(the journal *Think*, Royal Institute of Philosophy). Translate the user's Markdown into \
{lang}.

- Keep the Markdown structure exactly: headings, lists, blockquotes, *italics*, **bold**.
- Translate faithfully and fluently; match the author's informal, essayistic tone.
- For established philosophical terms use the standard {lang} rendering, followed by the \
original in parentheses at first occurrence, e.g. 美德伦理学（virtue ethics）.
- Keep names of philosophers in their common {lang} form (亚里士多德), book titles in 《》 \
with the original in parentheses at first occurrence.
- Output only the translation, with no preamble or notes."""


def _claude_bin() -> str:
    # launchd/cron have a minimal PATH, so also check the default install location.
    found = os.environ.get("CLAUDE_BIN") or shutil.which("claude")
    fallback = Path.home() / ".local/bin/claude"
    if found:
        return found
    if fallback.exists():
        return str(fallback)
    raise RuntimeError("claude CLI not found; set CLAUDE_BIN in .env")


FICTION_SYSTEM = """You are an accomplished literary translator. Translate the user's Markdown, a \
short story (itself an English translation of a classic), into elegant, natural {lang} prose.

- Preserve the author's voice, irony and rhythm; this is literature, not a gloss. Do not add \
explanations, notes or English in parentheses.
- Use the established {lang} forms of names (果戈理, 芥川龙之介, 彼得堡). Where the story was \
originally Japanese or Chinese, render names and terms in their original characters where \
known (e.g. 犍陀多, 杜子春), not in phonetic transliteration of the English spelling.
- Keep the Markdown structure exactly: paragraphs, section headings, *italics*, blockquotes.
- Output only the translation."""

PROMPTS = {"essay": SYSTEM, "fiction": FICTION_SYSTEM}


def _translate_cli(markdown: str, lang: str, system: str = SYSTEM) -> str:
    env = {k: v for k, v in os.environ.items() if v != ""}
    # Use the logged-in subscription, not an API key that may be set for other purposes.
    env.pop("ANTHROPIC_API_KEY", None)
    cmd = [
        _claude_bin(), "-p", "Translate the Markdown provided on stdin.",
        "--model", MODEL,
        "--effort", "medium",
        "--system-prompt", system.format(lang=lang),
        "--tools", "",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--output-format", "json",
    ]
    p = subprocess.run(cmd, input=markdown, capture_output=True, text=True, env=env,
                       timeout=1800, cwd=Path(__file__).parent)
    try:
        out = json.loads(p.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude CLI failed (exit {p.returncode}): {(p.stderr or p.stdout)[-500:]}")
    if out.get("is_error") or out.get("subtype") != "success":
        raise RuntimeError(f"claude CLI error: {str(out.get('result') or out)[:500]}")
    return out["result"].strip()


def _translate_api(markdown: str, lang: str, system: str = SYSTEM) -> str:
    import anthropic

    client = anthropic.Anthropic(max_retries=4)
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=64000,
        system=system.format(lang=lang),
        messages=[{"role": "user", "content": markdown}],
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        # If a request is declined by a safety classifier, retry server-side on a fallback model.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError(f"translation refused: {msg.stop_details}")
    if msg.stop_reason == "max_tokens":
        raise RuntimeError("translation truncated (max_tokens)")
    return "".join(b.text for b in msg.content if b.type == "text").strip()


def _translate_codex(markdown: str, lang: str, system: str = SYSTEM) -> str:
    """OpenAI Codex CLI (`codex exec`), billed to the logged-in ChatGPT subscription."""
    import tempfile

    codex = os.environ.get("CODEX_BIN") or shutil.which("codex") or str(Path.home() / ".local/bin/codex")
    prompt = (system.format(lang=lang) + "\n\nDo not use any tools or files. The text to translate is "
              "between <text> tags; output only the translation.\n\n<text>\n" + markdown + "\n</text>\n")
    cmd = [codex, "exec", "-", "--skip-git-repo-check", "--ephemeral", "--ignore-rules",
           "-s", "read-only", "-c", f"model_reasoning_effort={os.environ.get('CODEX_EFFORT', 'medium')}"]
    if os.environ.get("CODEX_MODEL"):
        cmd += ["-m", os.environ["CODEX_MODEL"]]
    with tempfile.TemporaryDirectory() as tmp:  # empty working dir: nothing for the agent to touch
        out = Path(tmp) / "out.txt"
        p = subprocess.run(cmd + ["-o", str(out)], input=prompt, capture_output=True, text=True,
                           timeout=1800, cwd=tmp)
        text = out.read_text().strip() if out.exists() else ""
    if p.returncode != 0 or not text:
        raise RuntimeError(f"codex failed (exit {p.returncode}): {(p.stderr or p.stdout)[-500:]}")
    return re.sub(r"^\s*</?text>\s*|\s*</?text>\s*$", "", text).strip()


def translate(markdown: str, lang: str = "简体中文", style: str = "essay") -> str:
    system = PROMPTS[style]
    backend = os.environ.get("TRANSLATE_BACKEND", "claude-cli")
    if backend == "codex":
        return _translate_codex(markdown, lang, system)
    if backend == "api":
        return _translate_api(markdown, lang, system)
    return _translate_cli(markdown, lang, system)
