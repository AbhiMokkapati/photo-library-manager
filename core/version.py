"""
version.py — single source of truth for the app's version number.

Bump this on every release. build_installer.ps1 reads it and passes it into
Inno Setup (installer.iss) so the installer's version always matches, and
core/updates_manager.py compares it against the latest GitHub release to
decide whether an update is available.
"""

__version__ = "1.0.0"
