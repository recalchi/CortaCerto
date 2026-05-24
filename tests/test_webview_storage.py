"""Tests for the WebView2 user-data folder helpers (Phase 3.7).

Verifies that:
  - The folder path is stable across calls (so pywebview doesn't try to mkdtemp
    a new one every launch).
  - Cleanup tolerates missing dirs without raising.
  - Cleanup retries on PermissionError without crashing.
"""
import os
import tempfile
import unittest
from unittest.mock import patch

from src.ui.webview_app import _webview_user_data_dir, _cleanup_stale_webview_folder


class WebViewStorageTests(unittest.TestCase):
    def test_user_data_dir_is_stable(self) -> None:
        a = _webview_user_data_dir()
        b = _webview_user_data_dir()
        self.assertEqual(a, b)
        self.assertTrue(os.path.isdir(a))
        # Should live under the system temp tree, not in cwd
        self.assertTrue(a.startswith(tempfile.gettempdir()))

    def test_cleanup_when_folder_missing_is_noop(self) -> None:
        # Should not raise even if base doesn't exist (it's created by _webview_user_data_dir,
        # but cleanup operates on subfolders that may or may not exist).
        try:
            _cleanup_stale_webview_folder()
        except Exception as e:
            self.fail(f"cleanup raised on missing subfolders: {e}")

    def test_cleanup_retries_on_permission_error(self) -> None:
        """When rmtree fails with PermissionError, cleanup should retry and
        eventually give up silently — never crash the startup path.

        We isolate by pointing the helper at a temp dir so we don't depend on
        (or pollute) the real shared cortacerto_webview2 folder.
        """
        # Capture the real rmtree BEFORE patching (else the flaky fn would
        # recurse back into the mock).
        import shutil as _shutil_module
        real_rmtree = _shutil_module.rmtree

        with tempfile.TemporaryDirectory() as tmp_root:
            lock_dir = os.path.join(tmp_root, "EBWebView", "Default", "BrowsingTopicsSiteData")
            os.makedirs(lock_dir)

            call_count = {"n": 0}

            def flaky_rmtree(path, **kwargs):
                call_count["n"] += 1
                if call_count["n"] < 3:
                    raise PermissionError("locked")
                return real_rmtree(path, **kwargs)

            with patch("src.ui.webview_app._webview_user_data_dir", return_value=tmp_root), \
                 patch("src.ui.webview_app.shutil.rmtree", side_effect=flaky_rmtree):
                # Should NOT raise — retries 3 times, last one succeeds
                _cleanup_stale_webview_folder()

            # 3 attempts (PermissionError on 1+2, success on 3)
            self.assertEqual(call_count["n"], 3)


if __name__ == "__main__":
    unittest.main()
