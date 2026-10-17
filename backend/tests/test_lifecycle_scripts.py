"""Lifecycle regressions for the one failure class users actually hit: "the
package installed, App Center says it is running, and nothing answers".

Each rule here encodes a defect measured on real TOS 7 hardware (10.18.15.136,
TOS 7.1.x, systemd 249) rather than a preference:

* ``systemctl start ... || true`` makes a service that died with 203/EXEC,
  216/GROUP or 217/USER indistinguishable from a healthy install.
* A unit that already burned its ``StartLimitBurst`` refuses every later start,
  so an upgrade over a broken version stays broken until ``reset-failed``.
* TOS ships a custom ``/usr/bin/install`` that rejects ``install -d``.
* ``/var/api`` is a 1777 sticky symlink to ``/tmp/api``: a foreign-owned socket
  cannot be unlinked, and an unguarded ``rm -f`` aborts the script under
  ``set -e`` before the rest of removal or purge ever runs.

Run with:  py -3 -m unittest discover -s backend/tests
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ("postinst", "prerm", "postrm")

# (regex, why it is forbidden) — all measured or documented platform limits.
FORBIDDEN = [
    (r'(?<![\w./-])install\s+-',
     "TOS 7 ships a custom /usr/bin/install that rejects 'install -d'; "
     "use 'mkdir -p' plus 'chmod'"),
    (r'(?<![\w./-])realpath\b',
     "not guaranteed on the TOS image; resolve paths explicitly instead"),
    (r'(?<![\w./-])cp\s+-a\b',
     "GNU-only archive flags; TOS images do not all carry coreutils cp"),
    (r'(?<![\w./-])(apt|apt-get|dpkg)\s',
     "spec 10.6: an application must not touch the platform package manager"),
    (r'\bchown\s[^\n]*/var/api\b',
     "spec 10.6: /var/api is platform-owned and shared"),
]

BASH_ONLY = [
    (r'\[\[', "[[ ]] is not POSIX sh"),
    (r'<<<', "herestrings are not POSIX sh"),
    (r'\bfunction\s+\w+\s*\(', "'function name(' is not POSIX sh"),
    (r'\bdeclare\b', "'declare' is not POSIX sh"),
    (r'\$\{[A-Za-z_]\w*//', "pattern substitution is not POSIX sh"),
    (r'\becho\s+-n\b', "'echo -n' is not portable; use printf"),
    (r'\blocal\s+-', "'local -flags' is not POSIX sh"),
]


def executable_lines(text):
    """Script text without full-line comments and without the shebang.

    Comments legitimately talk about dpkg, App Center and systemd; only the code
    a TOS /bin/sh will actually parse is under test here.
    """
    kept = []
    for index, line in enumerate(text.splitlines()):
        if index == 0:
            continue
        if line.lstrip().startswith("#"):
            continue
        kept.append(line)
    return "\n".join(kept)


class LifecycleScriptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = json.loads((ROOT / "config.ini").read_text(encoding="utf-8"))
        cls.app_id = cls.cfg["id"]
        cls.system_id = cls.cfg["system_id"]
        cls.scripts = {
            name: (ROOT / "DEBIAN" / name).read_text(encoding="utf-8")
            for name in SCRIPTS
        }

    # ---- portability -------------------------------------------------------

    def test_scripts_are_posix_sh_with_no_bash_only_syntax(self):
        for name, text in self.scripts.items():
            self.assertTrue(text.startswith("#!/bin/sh\n"),
                            f"{name} must start with '#!/bin/sh' — dpkg runs it "
                            "with /bin/sh, which on TOS is dash")
            code = executable_lines(text)
            for pattern, why in BASH_ONLY:
                hit = re.search(pattern, code)
                self.assertIsNone(
                    hit,
                    None if hit is None else f"{name}: {why} ({hit.group(0)!r})")

    def test_scripts_avoid_commands_tos_cannot_run(self):
        for name, text in self.scripts.items():
            code = executable_lines(text)
            for pattern, why in FORBIDDEN:
                hit = re.search(pattern, code)
                self.assertIsNone(
                    hit,
                    None if hit is None else
                    f"{name}: '{hit.group(0).strip()}' — {why}")

    def test_scripts_use_lf_line_endings(self):
        for name in SCRIPTS:
            data = (ROOT / "DEBIAN" / name).read_bytes()
            self.assertNotIn(b"\r\n", data, f"spec 4.6: DEBIAN/{name} must be LF-only")

    # ---- install: a success dpkg reports must mean a service that answers --

    def test_postinst_checks_start_prerequisites(self):
        postinst = self.scripts["postinst"]
        for probe in (r'\-f "\$\{UNIT_SRC\}"', r'\-x "\$\{LAUNCHER\}"',
                      r'\-x /usr/bin/python3'):
            self.assertRegex(
                postinst, probe,
                "linking a unit whose file, launcher or interpreter is missing "
                "gives a package dpkg calls installed and systemd kills with "
                "203/EXEC on every start")

    def test_postinst_resets_a_burned_restart_quota(self):
        postinst = self.scripts["postinst"]
        self.assertIn("systemctl reset-failed", postinst,
                      "a previous broken version can exhaust StartLimitBurst, "
                      "after which systemd refuses every start until reset-failed")
        start = postinst.index("systemctl start")
        self.assertLess(postinst.index("reset-failed"), start,
                        "reset-failed must happen before the start it unblocks")

    def test_postinst_verifies_the_service_is_actually_serving(self):
        postinst = self.scripts["postinst"]
        start = postinst.index("systemctl start")
        tail = postinst[start:]
        self.assertIn("is-active --quiet", tail,
                      "dpkg exit code is not proof the service runs")
        self.assertRegex(tail, r'\-S "\$\{SOCKET_PATH\}"',
                         "readiness must wait for the iframe socket the proxy needs")
        self.assertRegex(tail, r"while \[.*WAITED",
                         "readiness must be polled, not asserted once")

    def test_postinst_reports_failure_instead_of_swallowing_it(self):
        postinst = self.scripts["postinst"]
        self.assertIn("postinst-diagnostic.txt", postinst,
                      "a failed start has to leave evidence an operator can read")
        self.assertIn("collect_diagnostics() {", postinst)
        self.assertEqual(postinst.count("collect_diagnostics "), 2,
                         "expected exactly two raises: missing prerequisites and "
                         "installed-but-not-serving")
        self.assertRegex(postinst, r"log ERROR", "App Center filters install logs by level")
        self.assertRegex(postinst.rstrip(), r"exit 0$",
                         "postinst must still exit 0: a non-zero configure leaves the "
                         "package half-configured and blocks uninstall/upgrade")

    def test_postinst_webui_extraction_cannot_abort_configure(self):
        postinst = self.scripts["postinst"]
        self.assertRegex(postinst, r"if tar -xjf .* then",
                         "a corrupt webui.bz2 must not abort configure under set -e")
        self.assertIn("could not be extracted", postinst,
                      "and it must be reported: a stale webui directory otherwise "
                      "silently serves the previous version's UI")

    def test_prerm_confirms_the_stop_and_escalates(self):
        prerm = self.scripts["prerm"]
        start = prerm.index("systemctl stop")
        tail = prerm[start:]
        self.assertRegex(tail, r"while \[.*WAITED",
                         "a stop that is ignored (restart policy, hung process) must "
                         "be observed, not assumed")
        self.assertIn("is-active --quiet", tail)
        self.assertIn("systemctl kill", tail,
                      "escalate to SIGKILL instead of leaving the daemon running "
                      "behind an App Center 'stopped' message")
        self.assertRegex(prerm, r"systemctl disable[^\n]*\|\| true")

    def test_socket_cleanup_can_never_abort_the_script(self):
        """set -e plus an EPERM unlink in sticky /var/api is how removal hangs."""
        for name in ("prerm", "postrm"):
            text = self.scripts[name]
            for line in text.splitlines():
                if "rm -f" in line and (".sock" in line or "SOCKET_PATH" in line):
                    self.assertRegex(
                        line, r"(\|\| true|!\s*rm\s)",
                        f"{name}: an unguarded socket rm aborts everything after it "
                        "when the socket is owned by another uid")

    def test_shared_var_api_is_only_created_never_restricted(self):
        postinst = self.scripts["postinst"]
        self.assertNotRegex(postinst, r"chown[^\n]*/var/api", "must not reown /var/api")
        self.assertNotRegex(postinst, r"chmod\s+0?770\s+/var/api", "must not narrow it")
        self.assertIn("mkdir -p /var/api", postinst)
        self.assertRegex(postinst, r"chmod 1777 /var/api",
                         "if the package has to create it, it must use the platform mode")

    # ---- the UI has to point at the same evidence the scripts write --------

    def test_ui_advertises_the_real_diagnostics_path_and_unit(self):
        html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        diag = f"/usr/local/{self.app_id}/logs/postinst-diagnostic.txt"
        postinst = self.scripts["postinst"]
        self.assertIn('DIAG_FILE="${LOG_DIR}/postinst-diagnostic.txt"', postinst)
        self.assertIn('LOG_DIR="${APP_DIR}/logs"', postinst)
        self.assertIn('APP_DIR="/usr/local/${APP_ID}"', postinst)
        self.assertIn(f'APP_ID="{self.app_id}"', postinst)
        self.assertIn(diag, html, "the banner must name the file postinst actually writes")
        self.assertIn(f"systemctl status {self.system_id}.service", html,
                      "the banner must name this app's real unit")

    def test_service_notice_is_wired_in_both_html_and_js(self):
        html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "frontend" / "styles.css").read_text(encoding="utf-8")
        for element_id in ("serviceNotice", "serviceNoticeReason", "serviceNoticeRetry"):
            self.assertIn(f'id="{element_id}"', html, f"missing #{element_id}")
            self.assertIn(f'byId("{element_id}")', js, f"{element_id} is never used")
        self.assertIn(".service-notice", css, "the notice has no styling")
        self.assertRegex(js, r"setServiceNotice\(true",
                         "the health probe must be what raises the notice")
        self.assertRegex(css, r"\.service-notice\[hidden\]",
                         "hidden must actually hide it, or the banner shows on boot")


if __name__ == "__main__":
    unittest.main(verbosity=2)
