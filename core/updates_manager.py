"""
updates_manager.py — checks GitHub Releases for a newer build than the one
currently running, and downloads+launches the new installer on request.

Deliberately dependency-free (urllib instead of requests) since this is the
only thing in the app that would need it. Network calls here are blocking,
so callers must run check_for_update()/download_installer() off the GUI
thread (see gui/update_check_worker.py) — never call them directly from a
Qt slot on the main thread.
"""

import json
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.version import __version__

GITHUB_REPO = "abhimokkapati/photo-manager"
_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_REQUEST_TIMEOUT = 10
_USER_AGENT = "PhotoLibraryManager-UpdateChecker"


@dataclass
class UpdateInfo:
    version: str
    download_url: str
    release_notes: str
    asset_name: str


def _parse_version(v: str) -> tuple:
    v = v.strip().lstrip("vV")
    parts = []
    for p in v.split("."):
        digits = "".join(c for c in p if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def check_for_update(current_version: str = __version__) -> Optional[UpdateInfo]:
    """Blocking network call — run this off the GUI thread.

    Returns None if there's no newer release, or if the check fails for any
    reason (offline, rate-limited, repo has no releases yet, ...). An update
    check is a nice-to-have, never something that should surface an error
    dialog to the user or block them from using the app.
    """
    req = urllib.request.Request(_API_URL, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None

    tag = data.get("tag_name")
    if not tag:
        return None

    if _parse_version(tag) <= _parse_version(current_version):
        return None

    asset = next(
        (a for a in data.get("assets", []) if a.get("name", "").lower().endswith("setup.exe")),
        None,
    )
    if asset is None:
        return None

    return UpdateInfo(
        version=tag.lstrip("vV"),
        download_url=asset["browser_download_url"],
        release_notes=(data.get("body") or "").strip(),
        asset_name=asset["name"],
    )


def download_installer(info: UpdateInfo, on_progress=None) -> Path:
    """Blocking download — run this off the GUI thread.

    on_progress, if given, is called with (bytes_read, total_bytes) as the
    download progresses (total_bytes is 0 if the server didn't send a
    Content-Length). Returns the path to the downloaded installer in a temp
    directory; the caller is responsible for launching it.
    """
    dest = Path(tempfile.gettempdir()) / info.asset_name
    req = urllib.request.Request(info.download_url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        read = 0
        with open(dest, "wb") as f:
            while chunk := resp.read(65536):
                f.write(chunk)
                read += len(chunk)
                if on_progress:
                    on_progress(read, total)
    return dest
