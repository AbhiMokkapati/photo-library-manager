import tempfile
import unittest
from pathlib import Path

from core import auto_sort, updates_manager


class _Conn:
    def execute(self, *a):
        pass

    def commit(self):
        pass


class _DB:
    conn = _Conn()


class AutoSortSafety(unittest.TestCase):
    def test_names_cannot_escape(self):
        self.assertNotIn("..", auto_sort._safe_part("../../x").split("/"))
        self.assertEqual(auto_sort._safe_part("a/b"), "a_b")

    def test_apply_stays_in_root(self):
        root = Path(tempfile.mkdtemp())
        (root / "a.jpg").write_text("x")
        auto_sort.apply_reorganization(_DB(), root, [{
            "photo_id": 1, "current_relative_path": "a.jpg",
            "proposed_relative_path": "../../esc/a.jpg"}], dry_run=False)
        self.assertFalse((root.parent / "esc").exists())


class UpdaterSafety(unittest.TestCase):
    def test_trusted_urls(self):
        t = updates_manager._is_trusted_url
        self.assertTrue(t("https://github.com/a/b/releases/download/v1/x.exe"))
        self.assertFalse(t("http://github.com/x"))
        self.assertFalse(t("https://evilgithub.com/x"))

    def test_refuses_without_checksum(self):
        info = updates_manager.UpdateInfo("1", "https://github.com/x", "", "S.exe")
        with self.assertRaises(ValueError):
            updates_manager.download_installer(info)


if __name__ == "__main__":
    unittest.main()
