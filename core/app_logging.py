"""
app_logging.py — file-based logging setup.

The packaged .exe is built with console=False (photo_manager.spec), so any
plain print() call — which is how errors were being reported all over the
codebase — goes nowhere; there's no console attached to read it from. That
made real failures (a bad file, a missing DLL, a model that failed to load)
indistinguishable from "nothing to detect" in the installed app: indexing
would appear to finish cleanly while quietly finding 0 faces.

Call setup_logging() once, early, in every entry point (run_gui.py,
run_tray.py, run_index.py) so both `print`-style code and proper
logger.exception() calls end up in a log file the user can actually find
and share when something silently goes wrong.
"""

import logging
import logging.handlers

from .paths import DATA_DIR

LOG_DIR = DATA_DIR / "logs"
LOG_PATH = LOG_DIR / "app.log"

_configured = False


def setup_logging(level=logging.INFO):
    global _configured
    if _configured:
        return
    _configured = True

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)

    # console handler too — harmless when there's no console (frozen app),
    # helpful when running from source
    console = logging.StreamHandler()
    console.setFormatter(handler.formatter)
    root.addHandler(console)
