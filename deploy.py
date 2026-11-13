"""Publish site/ to GitHub Pages (gh-pages branch of SITE_REPO, over SSH).

.env:
  SITE_REPO=git@github.com:RuizhiChang/reader.git
  SITE_BASE_URL=https://ruizhichang.github.io/reader
Only the built site/ folder is pushed; .env, output/ and code never leave this machine.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import requests

ROOT = Path(__file__).parent
SITE = ROOT / "site"
WORK = ROOT / ".deploy"  # local clone of the gh-pages branch
BRANCH = "gh-pages"


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(WORK), *args], capture_output=True, text=True, check=check)


def configured() -> bool:
    return bool(os.environ.get("SITE_REPO") and os.environ.get("SITE_BASE_URL"))


def deploy(message: str = "Update site") -> bool:
    """Push site/ to gh-pages. Returns True if anything changed."""
    repo = os.environ["SITE_REPO"]
    if not (WORK / ".git").exists():
        WORK.mkdir(exist_ok=True)
        _git("init", "-q")
        _git("remote", "add", "origin", repo)
        if _git("ls-remote", "--heads", "origin", BRANCH).stdout.strip():
            _git("fetch", "-q", "origin", BRANCH)
            _git("checkout", "-q", "-B", BRANCH, f"origin/{BRANCH}")
        else:
            _git("checkout", "-q", "--orphan", BRANCH)
    # Another machine (CI or the Mac) may have deployed since: start from the remote's state.
    if _git("ls-remote", "--heads", "origin", BRANCH).stdout.strip():
        _git("fetch", "-q", "origin", BRANCH)
        _git("reset", "-q", "--hard", f"origin/{BRANCH}")
    for p in WORK.iterdir():
        if p.name != ".git":
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    shutil.copytree(SITE, WORK, dirs_exist_ok=True)
    _git("add", "-A")
    if not _git("status", "--porcelain").stdout.strip():
        return False
    _git("-c", "user.name=article-pub bot", "-c", "user.email=bot@users.noreply.github.com",
         "commit", "-q", "-m", message)
    _git("push", "-q", "origin", BRANCH)
    return True


def wait_live(url: str, marker: str = "", timeout: int = 300) -> bool:
    """GitHub Pages takes ~30-90 s to rebuild; wait so link previews aren't 404."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            r = requests.get(url, params={"_": int(time.time())}, timeout=15)
            if r.status_code == 200 and marker in r.text:
                return True
        except requests.RequestException:
            pass
        time.sleep(10)
    return False
