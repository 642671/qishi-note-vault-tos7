"""Regression tests for the /var/api socket start-up path.

Findings from the TOS 7 lab that these tests pin down:

* ``/var/api`` is a shared directory with mode ``1777`` and the sticky bit, so a
  service running as the app user can only unlink entries it owns.
* A plain ``os.path.exists()`` check misses a dangling symlink, and binding then
  fails with ``EADDRINUSE``. The code uses ``os.path.lexists()``.
* An unlink that the sticky bit forbids must surface as one actionable journal
  line, not a ``PermissionError`` traceback, and ``main()`` must exit 1 so
  ``Restart=on-failure`` stops after ``StartLimitBurst`` instead of looping with
  a stack trace.
"""

from __future__ import annotations

import errno
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from qishi_note_vault import server  # noqa: E402


def supports_symlink(directory: Path) -> bool:
    target = directory / "symlink-target"
    link = directory / "symlink-link"
    try:
        target.write_text("x", encoding="utf-8")
        os.symlink(target, link)
        return True
    except (OSError, NotImplementedError):
        return False
    finally:
        for path in (link, target):
            try:
                path.unlink()
            except OSError:
                pass


class RemoveStaleSocketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.sock_dir = root / "api"
        self.sock_dir.mkdir()
        self.sock_path = self.sock_dir / "qishi-note-vault.sock"
        self.patches = [
            mock.patch.object(server, "SOCKET_DIR", str(self.sock_dir)),
            mock.patch.object(server, "SOCKET_PATH", str(self.sock_path)),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_no_socket_present_is_a_no_op(self):
        server._remove_stale_socket()
        self.assertTrue(self.sock_dir.is_dir())
        self.assertFalse(os.path.lexists(self.sock_path))

    def test_leftover_socket_file_is_removed_before_bind(self):
        self.sock_path.write_text("stale", encoding="utf-8")
        server._remove_stale_socket()
        self.assertFalse(os.path.lexists(self.sock_path))

    def test_dangling_symlink_is_removed_before_bind(self):
        if not supports_symlink(self.sock_dir):
            self.skipTest("symlink creation is not permitted here")
        missing = self.sock_dir / "does-not-exist"
        os.symlink(missing, self.sock_path)
        # The old code used os.path.exists(), which reports False for a dangling
        # symlink and then lets bind() fail with EADDRINUSE.
        self.assertFalse(os.path.exists(self.sock_path))
        self.assertTrue(os.path.lexists(self.sock_path))
        server._remove_stale_socket()
        self.assertFalse(os.path.lexists(self.sock_path))

    def test_unremovable_socket_reports_an_actionable_error(self):
        self.sock_path.write_text("foreign", encoding="utf-8")
        with mock.patch.object(
            server.os, "unlink", side_effect=OSError(errno.EPERM, "Operation not permitted")
        ):
            with self.assertRaises(RuntimeError) as ctx:
                server._remove_stale_socket()
        message = str(ctx.exception)
        self.assertIn(str(self.sock_path), message)
        self.assertIn("sticky", message, "operator must learn why the unlink failed")
        self.assertIn("Operation not permitted", message)


class MainFailureTests(unittest.TestCase):
    def test_main_exits_with_status_and_log_instead_of_traceback(self):
        with mock.patch.object(server, "configure_service"), mock.patch.object(
            server, "create_server", side_effect=RuntimeError("boom: sticky /var/api")
        ), mock.patch.object(server, "log") as logger:
            self.assertEqual(server.main(), 1)
        level, message = logger.call_args.args
        self.assertEqual(level, "ERROR")
        self.assertIn("boom: sticky /var/api", message)


if __name__ == "__main__":
    unittest.main()
