"""Regression tests for the frontend request layer.

Two layers of proof, because a plain string grep would not have caught the bug
this iteration fixes. On real hardware (10.18.15.136, TOS 7.0.1298) the user
only ever sees "Failed to fetch", and that single string covers several
different causes:

* the browser rejected the request at the network layer (untrusted certificate,
  reset connection, CORS/CSP, an opaque-origin sandbox) — the backend process
  may be perfectly healthy;
* the platform refused to forward at all and answered HTTP 200 with
  ``{"is_login":false,"code":false,"msg":"please login","code_num":117}`` —
  measured with a logged-out browser against ``/v2/proxy/qishi-note-vault/health``;
* nginx did not match the proxy location and returned an HTML 404/502 — the
  body is not JSON, so "did the request reach the app?" is unknowable;
* the backend really did answer 5xx.

The old code collapsed the first three into "后端服务未运行", so the banner sent
the user off to restart a service that was fine. ``RequestBehaviourTests`` runs
the actual browser script under Node with a fake ``fetch`` and asserts the
classification; the static tests then pin the header shape and the banner wiring
the behaviour tests cannot see.

Run with:  py -3 -m unittest discover -s backend/tests
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_ID = "qishi-note-vault"
# 启思笔记的 apiRequest 在状态正常但不是 JSON 时必须把 Response 原样交回去
# （附件预览与备份导出走的就是这条路）。
BINARY_RESPONSE = True

APP_JS = ROOT / "frontend" / "app.js"
INDEX_HTML = ROOT / "frontend" / "index.html"
STYLES_CSS = ROOT / "frontend" / "styles.css"
HARNESS = Path(__file__).with_name("frontend_request_harness.cjs")
NODE = shutil.which("node")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class RequestBehaviourTests(unittest.TestCase):
    """Runs frontend/app.js for real and inspects how each failure is sorted."""

    @classmethod
    def setUpClass(cls) -> None:
        if NODE is None:
            raise unittest.SkipTest("node is required to drive the browser script")
        args = [NODE, str(HARNESS), str(APP_JS), APP_ID]
        if BINARY_RESPONSE:
            args.append("--binary")
        completed = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", timeout=120
        )
        if completed.returncode != 0:
            raise AssertionError(
                f"harness exited {completed.returncode}\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
            )
        payload = json.loads(completed.stdout)
        cls.scenarios = {item["scenario"]: item for item in payload["results"]}
        cls.raw = completed.stdout

    def outcome(self, name: str) -> dict:
        item = self.scenarios.get(name)
        self.assertIsNotNone(item, f"场景 {name} 没有跑起来：{self.raw[:400]}")
        assert item is not None
        if "harnessError" in item:
            raise AssertionError(f"场景 {name} 内部报错：{item['harnessError']}")
        return item["result"]

    def failure(self, name: str) -> dict:
        result = self.outcome(name)
        self.assertTrue(result.get("threw"), f"{name} 预期抛错，实际返回 {result}")
        return result

    def test_platform_login_envelope_is_not_reported_as_service_down(self) -> None:
        """实机测到的是 HTTP 200 + is_login:false，必须归到会话，不是"后端挂了"."""
        self.assertEqual(self.failure("platform-login-envelope-is-session")["kind"], "session")
        self.assertIn("117", read(APP_JS))

    def test_network_rejection_is_classified_as_network(self) -> None:
        failure = self.failure("fetch-rejection-is-network")
        self.assertEqual(failure["kind"], "network")
        self.assertIn("Failed to fetch", failure["detail"])

    def test_html_error_page_is_classified_as_gateway(self) -> None:
        self.assertEqual(self.failure("nginx-html-is-gateway")["kind"], "gateway")

    def test_another_apps_json_is_not_accepted_as_ours(self) -> None:
        """判据是回包里的 app 字段：平台或别的应用答一句 JSON 不算本应用活着."""
        self.assertEqual(self.failure("wrong-app-field-is-gateway")["kind"], "gateway")

    def test_http_401_is_a_session_problem(self) -> None:
        self.assertEqual(self.failure("http-401-is-session")["kind"], "session")

    def test_proxy_root_is_probed_and_then_reused(self) -> None:
        result = self.outcome("second-candidate-wins-and-is-reused")
        self.assertFalse(result["probe"]["threw"], result["probe"])
        self.assertEqual(result["rootAfter"], result["expectedRoot"])
        self.assertTrue(result["readyAfter"])
        self.assertEqual(result["probeCalls"], 2)
        self.assertEqual(result["extraCalls"], 1)
        self.assertIn("/v2/proxy/" + APP_ID, result["candidates"])

    def test_backend_5xx_is_the_only_service_down_banner(self) -> None:
        failure = self.failure("backend-500-is-offline")
        self.assertEqual(failure["kind"], "offline")
        self.assertTrue(failure["serviceDown"])

    def test_bad_input_is_not_a_service_failure(self) -> None:
        failure = self.failure("backend-400-is-rejected-and-not-service-down")
        self.assertEqual(failure["kind"], "rejected")
        self.assertFalse(failure["serviceDown"], "400 不该弹故障横幅")

    def test_json_body_is_the_only_request_that_sets_content_type(self) -> None:
        requests = self.outcome("header-shape")["value"]["requests"]
        self.assertEqual(requests[0]["method"], "GET")
        self.assertEqual(requests[0]["headerNames"], [], "无体的 GET 带任何请求头都会白付一次跨源预检")
        for request in requests:
            self.assertNotIn("cookie", request["headerNames"])
            self.assertEqual(request["credentials"], "include")

    def test_csrf_token_is_read_from_cookie_and_session_is_not_read_at_all(self) -> None:
        headers = self.outcome("csrf-cookie-forwarded-and-session-not-fabricated")["value"]["headers"]
        for header in headers:
            self.assertEqual(header.get("X-Csrf-Token"), "csrf-value")
            self.assertNotIn("Cookie", header)

    def test_gateway_failure_reprobes_once_only(self) -> None:
        result = self.outcome("gateway-error-triggers-one-reprobe")
        self.assertEqual(result["result"]["kind"], "gateway")
        self.assertLessEqual(result["retried"], 4)
        self.assertGreaterEqual(result["retried"], 1)

    def test_binary_body_is_handed_back_untouched(self) -> None:
        """附件与备份导出靠这条：200 + 非 JSON 不能被判成故障."""
        result = self.outcome("non-json-ok-returns-response")
        self.assertFalse(result.get("threw"), result)
        self.assertEqual(result["value"]["status"], 200)


class RequestLayerSourceTests(unittest.TestCase):
    """Things the behaviour test cannot observe: the banner copy and file hygiene."""

    def test_no_manual_cookie_header_anywhere(self) -> None:
        source = read(APP_JS)
        self.assertNotIn('"Cookie"', source)
        self.assertNotIn("'Cookie'", source)
        self.assertNotIn(
            'getCookie("TMSESSNAME")',
            source,
            "TMSESSNAME 是 HttpOnly（实机 Set-Cookie 已验证），JS 读出来永远是空串",
        )

    def test_upload_paths_still_omit_content_type(self) -> None:
        """FormData 必须让浏览器自己填 boundary，手写 Content-Type 会直接把上传打挂."""
        source = read(APP_JS)
        self.assertIn("platformHeaders(false)", source)

    def test_four_failure_kinds_have_their_own_banner_copy(self) -> None:
        source = read(APP_JS)
        block = re.search(r"var NOTICE_COPY = \{(.*?)\n\};", source, re.S)
        self.assertIsNotNone(block, "缺少 NOTICE_COPY 表")
        assert block is not None
        for kind in ("network", "session", "gateway", "offline"):
            self.assertIn(f"{kind}: {{", block.group(1), f"横幅没有为 {kind} 单独写文案")
        self.assertIn('notice.setAttribute("data-kind", kind)', source)
        self.assertIn("title.textContent = copy.title", source)

    def test_the_failing_url_is_shown_to_the_user(self) -> None:
        source = read(APP_JS)
        self.assertIn("实际失败的请求地址", source)
        self.assertIn("error.url", source)

    def test_network_kind_tells_the_user_how_to_spot_a_blocked_request(self) -> None:
        """Failed to fetch 在实机上最常见的原因是扩展拦截或证书不受信，
        光说"连不上"等于没说。网络分型必须给出可自查的那一步。"""
        source = read(APP_JS)
        block = re.search(r'if \(kind === "network"\) \{(.*?)\n  \}', source, re.S)
        self.assertIsNotNone(block, "network 分型没有单独的处置步骤")
        assert block is not None
        steps = block.group(1)
        self.assertIn("F12", steps)
        self.assertIn("扩展", steps)
        self.assertIn("证书", steps)
        self.assertIn("NOTICE_STEPS.unit", steps, "network 步骤没有带上本应用的 unit 名")

    def test_banner_elements_are_wired(self) -> None:
        html = read(INDEX_HTML)
        for element_id in (
            "serviceNotice",
            "serviceNoticeTitle",
            "serviceNoticeReason",
            "serviceNoticeSteps",
            "serviceNoticeRetry",
        ):
            self.assertIn(f'id="{element_id}"', html, element_id)
        self.assertIn('data-kind="offline"', html)

    def test_non_offline_kinds_render_in_warning_colour(self) -> None:
        css = read(STYLES_CSS)
        for kind in ("network", "session", "gateway"):
            self.assertIn(f'.service-notice[data-kind="{kind}"]', css, kind)
        self.assertIn("--notice-accent", css)
        self.assertIn(".service-badge.is-warn", css, "状态点也需要第三种颜色")

    def test_frontend_files_are_lf_without_bom(self) -> None:
        for path in (APP_JS, INDEX_HTML, STYLES_CSS):
            raw = path.read_bytes()
            self.assertNotIn(b"\r\n", raw, f"{path.name} 必须是 LF")
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"{path.name} 不能带 BOM")


if __name__ == "__main__":
    unittest.main()
