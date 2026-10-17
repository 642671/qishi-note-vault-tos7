"""Package contract: the TOS 7 spec points this project deliberately depends on.

Every assertion here is anchored either in the official specification
(github.com/terramaster-tos/tos-app-pkg-tools, doc v2.7) or in behaviour measured
on a real TOS 7 box (systemd 249 / kernel 6.12). Run with:

    py -3 -m unittest discover -s backend/tests
"""
import configparser
import io
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LANG_TAGS = ["zh-cn", "zh-hk", "en-us", "fr-fr", "de-de", "it-it", "es-es", "hu-hu",
             "ja-jp", "ko-kr", "pl-pl", "ru-ru", "tr-tr", "pt-pt"]
REQUIRED_CONFIG = ["id", "icon", "publisher", "exec", "version", "recommend", "beta",
                   "low_version", "category", "platform", "application_type",
                   "system_id", "package", "user"]
# Spec 8.12 "Systemd Directive Reference": directives marked Required.
SPEC_REQUIRED = ["User", "WorkingDirectory", "NoNewPrivileges", "ProtectSystem",
                 "ProtectHome", "TimeoutStartSec", "TimeoutStopSec", "ReadWritePaths",
                 "ExecStart"]
PACKAGED_TEXT_GLOBS = ("DEBIAN/*", "config.ini", "*.lang", "*.env", "init.d/*",
                       "backend/*/*.py", "backend/tests/*.py", "frontend/*",
                       "images/icons/*.svg")


def load_config():
    return json.loads((ROOT / "config.ini").read_text(encoding="utf-8"))


def load_unit(cfg):
    path = ROOT / "init.d" / f"{cfg['system_id']}.service"
    text = path.read_text(encoding="utf-8")
    # strip comment lines so a documented rationale never looks like a directive
    body = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    values = {}
    for key, val in re.findall(r"^(\w+)=(.*)$", body, re.M):
        values.setdefault(key, val.strip())
    return text, values


def parse_lang(cfg):
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    text = (ROOT / f"{cfg['id']}.lang").read_text(encoding="utf-8")
    parser.read_string(text)
    return {s.lower(): dict(parser.items(s)) for s in parser.sections()}


class PackageContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config()
        cls.unit_text, cls.unit = load_unit(cls.cfg)

    def test_config_is_strict_json_with_required_fields(self):
        raw = (ROOT / "config.ini").read_text(encoding="utf-8")
        json.loads(raw)  # spec 8.4: extension is .ini, content must be strict JSON
        for field in REQUIRED_CONFIG:
            self.assertIn(field, self.cfg, f"config.ini missing required field {field}")
        self.assertIs(self.cfg["recommend"], False, "spec 8.4.2: recommend must stay false")
        self.assertRegex(self.cfg["id"], r"^[a-z][a-z0-9-]{0,49}$", "spec 8.4.2 id charset")
        self.assertIn(self.cfg["platform"], ("x86_64", "aarch64"))
        self.assertTrue(1 <= len(self.cfg["category"]) <= 3, "spec 8.4.2: 1-3 categories")

    def test_iframe_subtype_field_exclusivity(self):
        # spec 8.4.1 mutual-exclusion table: iframe apps set type, never open_path
        self.assertEqual(self.cfg["type"], "iframe")
        self.assertNotIn("open_path", self.cfg)
        self.assertEqual(self.cfg["path"], f"/{self.cfg['id']}/")

    def test_versions_are_synchronized(self):
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(self.cfg["version"], version, "config.ini vs VERSION")
        control = (ROOT / "DEBIAN" / "control").read_text(encoding="utf-8")
        self.assertIn(f"Version: {version}", control, "DEBIAN/control vs VERSION")
        self.assertIn(f"Package: {self.cfg['package']}", control, "spec 1.3 package link")
        pkg_init = sorted((ROOT / "backend").glob("*/__init__.py"))[0]
        m = re.search(r"__version__\s*=\s*[\"']([^\"']+)", pkg_init.read_text(encoding="utf-8"))
        self.assertIsNotNone(m, "backend package exposes __version__")
        self.assertEqual(m.group(1), version, "backend __version__ vs VERSION")
        html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        cached = re.findall(r"\?(?:v|version)=([\w.]+)", html)
        self.assertTrue(cached, "assets must carry a cache-busting version")
        for ref in cached:
            self.assertEqual(ref, version, "spec: stale ?v= serves the old UI after upgrade")

    def test_unit_keeps_spec_required_directives(self):
        for d in SPEC_REQUIRED:
            self.assertIn(d, self.unit, f"spec 8.12 requires {d}=")
        self.assertEqual(self.unit["User"], self.cfg["user"])
        self.assertNotEqual(self.unit["User"], "root", "spec 16.2: non-root execution")
        self.assertEqual(self.unit["WorkingDirectory"], f"/usr/local/{self.cfg['id']}")
        self.assertEqual(self.unit["ProtectSystem"], "strict")
        self.assertEqual(self.unit["TimeoutStartSec"], "30", "spec 8.12 required value")
        self.assertEqual(self.unit["TimeoutStopSec"], "10", "spec 8.12 required value")

    def test_unit_deviates_from_spec_only_where_tos_actually_fails(self):
        # Measured on TOS 7: Group=<appid> -> status=216/GROUP because the platform
        # never creates the same-named group; PrivateTmp=true -> status=226/NAMESPACE
        # because /var/api is a symlink into /tmp.
        self.assertNotIn("Group", self.unit, "Group= triggers 216/GROUP on real TOS 7")
        self.assertNotIn("PrivateTmp", self.unit, "PrivateTmp= triggers 226/NAMESPACE")
        rwpaths = self.unit["ReadWritePaths"].split()
        self.assertIn("/var/api", rwpaths, "iframe socket dir must stay writable")
        self.assertIn(f"/usr/local/{self.cfg['id']}/data", rwpaths)
        self.assertIn(f"/usr/local/{self.cfg['id']}/logs", rwpaths)
        self.assertIn("ExecStopPost", self.unit, "spec 12.9: stale socket blocks restart")
        self.assertTrue(
            self.unit["ExecStopPost"].startswith("-"),
            "cleanup must be optional: a foreign-owned socket in sticky /var/api makes "
            "/bin/rm fail with EPERM, and a non-zero ExecStopPost turns a clean stop "
            "into 'Control process exited, status=1/FAILURE' (App Center 停用失败)",
        )

    def test_unit_restart_policy_is_crash_recovery(self):
        # spec 8.7.1: the App Center honours the developer restart policy.
        self.assertEqual(self.unit.get("Restart"), "on-failure")
        self.assertIn("RestartSec", self.unit)
        self.assertIn("StartLimitBurst", self.unit_text, "crash-loop guard")

    def test_lang_covers_all_required_tags(self):
        lang = parse_lang(self.cfg)
        self.assertEqual(sorted(k.lower() for k in LANG_TAGS), sorted(lang))
        for tag in LANG_TAGS:
            for field in ("name", "auth", "descript"):
                self.assertTrue(lang[tag].get(field, "").strip(),
                                f"spec 8.5.3: [{tag}] {field} must not be empty")

    def test_lifecycle_scripts_behave_on_upgrade(self):
        postinst = (ROOT / "DEBIAN" / "postinst").read_text(encoding="utf-8")
        self.assertRegex(postinst, r"tar\s+-xjf", "webui.bz2 must be re-extracted on upgrade")
        for call in ("daemon-reload", "systemctl enable", "systemctl start"):
            self.assertIn(call, postinst, f"postinst must {call}")
        # /var/api is platform-owned: the package may create it, never restrict it.
        self.assertNotRegex(postinst, r"chown[^\n]*/var/api", "must not reown /var/api")
        self.assertNotRegex(postinst, r"chmod\s+0?770\s+/var/api", "must not narrow /var/api")
        prerm = (ROOT / "DEBIAN" / "prerm").read_text(encoding="utf-8")
        self.assertIn("systemctl stop", prerm)
        postrm = (ROOT / "DEBIAN" / "postrm").read_text(encoding="utf-8")
        self.assertRegex(postrm, r"rm\s+-f[^\n]*/var/api/.*\.sock", "spec 16.5 #8 residue")

    def test_packaged_text_is_utf8_without_crlf_or_bom(self):
        candidates = {p for pat in PACKAGED_TEXT_GLOBS for p in ROOT.glob(pat) if p.is_file()}
        for path in sorted(candidates):
            data = path.read_bytes()
            rel = path.relative_to(ROOT)
            self.assertNotIn(b"\r\n", data, f"spec 4.6: {rel} must use LF endings")
            self.assertFalse(data.startswith(b"\xef\xbb\xbf"), f"spec 8.5.5: {rel} has a BOM")

    def test_icon_matches_config_and_limits(self):
        icon = ROOT / self.cfg["icon"].lstrip("/")
        self.assertTrue(icon.exists(), "spec 8.6: icon path must match config.ini")
        svg = icon.read_text(encoding="utf-8")
        self.assertIn("viewBox", svg, "spec 8.6: SVG needs a valid viewBox")
        self.assertLessEqual(icon.stat().st_size, 50 * 1024, "spec 8.6: icon <= 50 KB")


if __name__ == "__main__":
    unittest.main(verbosity=2)
