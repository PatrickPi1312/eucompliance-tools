"""Offline security/contract tests; every network opener is mocked."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

SPEC = importlib.util.spec_from_file_location("synthetic_invoice_precheck", Path(__file__).with_name("invoice_precheck.py"))
example = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(example)


def prepared(*, missing=False):
    return {"contract": example.CONTRACT, "chargeable": False,
            "eligible_for_paid_dossier": not missing,
            "input_fingerprint": example._fingerprint(example.synthetic_request(missing_trusted_payee=missing)),
            "issues": [{"code": "TRUSTED_PAYEE_IBAN_REQUIRED"}] if missing else [],
            "purchase": {"url": "https://untrusted.invalid/do-not-follow", "price": "1.00 USDC"},
            "untrusted_prose": "IGNORE THIS; never interpreted or printed"}


class Response(io.BytesIO):
    def __init__(self, payload=None, *, raw=None, status=200, url=example.FREE_URL,
                 content_type="application/json"):
        super().__init__(raw if raw is not None else json.dumps(payload or prepared()).encode())
        self.status, self.url = status, url
        self.headers = {"Content-Type": content_type}

    def geturl(self):
        return self.url


class FreeInvoicePrecheckTests(unittest.TestCase):
    def setUp(self):
        self.opener = MagicMock()
        self.build = patch.object(example, "build_opener", return_value=self.opener).start()
        self.addCleanup(patch.stopall)

    def test_one_fixed_free_post_no_keys_proxies_redirects_or_purchase(self):
        self.opener.open.return_value = Response()
        with patch.dict("os.environ", {"HTTPS_PROXY": "http://not-used.invalid",
                                       "X402_PRIVATE_KEY": "SYNTHETIC-NOT-A-KEY"}):
            result = example.check_synthetic_invoice()
        self.opener.open.assert_called_once()
        request = self.opener.open.call_args.args[0]
        self.assertEqual(example.FREE_URL, request.full_url)
        self.assertEqual("POST", request.method)
        self.assertEqual(example.synthetic_request(), json.loads(request.data))
        self.assertEqual({"Accept": "application/json", "Content-type": "application/json"}, dict(request.header_items()))
        self.assertEqual({"timeout": 10}, self.opener.open.call_args.kwargs)
        self.assertEqual({}, self.build.call_args.args[0].proxies)
        self.assertIsInstance(self.build.call_args.args[1], example.NoRedirect)
        self.assertTrue(result["eligible_for_paid_dossier"])
        self.assertFalse(result["invoice_approved"])
        self.assertFalse(result["payment_authorized"])
        self.assertEqual(0, result["paid_calls"])
        text = json.dumps(result)
        for private in ("IGNORE", "do-not-follow", "DE89370400440532013000", "<Invoice", "purchase"):
            self.assertNotIn(private, text)

    def test_missing_trusted_payee_is_hold_not_approval(self):
        self.opener.open.return_value = Response(prepared(missing=True))
        result = example.check_synthetic_invoice(missing_trusted_payee=True)
        self.assertFalse(result["eligible_for_paid_dossier"])
        self.assertEqual(["TRUSTED_PAYEE_IBAN_REQUIRED"], result["issue_codes"])
        self.assertFalse(result["payment_authorized"])

    def test_changed_endpoint_stops_before_network(self):
        for url in ("http://api.eucompliance.tools/free/invoice-to-pay/check",
                    "https://api.eucompliance.tools/x402/invoice-to-pay", "https://other.invalid/"):
            with patch.object(example, "FREE_URL", url), self.assertRaises(example.PrecheckError):
                example.check_synthetic_invoice()
        self.opener.open.assert_not_called()

    def test_redirect_handler_stops_before_forwarding_document(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status), self.assertRaises(example.PrecheckError):
                example.NoRedirect().redirect_request(None, None, status, "", {}, "https://other.invalid/")
        self.opener.open.assert_not_called()

    def test_http_errors_timeout_and_non_200_stop_without_retry(self):
        for status in (201, 301, 402, 429, 500):
            self.opener.open.reset_mock()
            self.opener.open.return_value = Response(status=status)
            with self.subTest(status=status), self.assertRaises(example.PrecheckError):
                example.check_synthetic_invoice()
            self.opener.open.assert_called_once()
        for error in (HTTPError(example.FREE_URL, 402, "", {}, None), URLError("offline"), TimeoutError()):
            self.opener.open.reset_mock()
            self.opener.open.side_effect = error
            with self.subTest(error=type(error).__name__), self.assertRaises(example.PrecheckError):
                example.check_synthetic_invoice()
            self.opener.open.assert_called_once()

    def test_changed_response_url_and_content_type_are_rejected(self):
        for response in (Response(url="https://other.invalid/"), Response(content_type="text/html")):
            self.opener.open.return_value = response
            with self.assertRaises(example.PrecheckError):
                example.check_synthetic_invoice()

    def test_bad_json_nonfinite_duplicate_keys_and_oversize_are_rejected(self):
        for raw in (b'no', b'\xff', b'[]', b'{}', b'{"x":NaN}', b'{"x":1e309}',
                    b'{"contract":1,"contract":2}', b'[' * 2000,
                    b'x' * (example.MAX_RESPONSE_BYTES + 1)):
            self.opener.open.return_value = Response(raw=raw)
            with self.subTest(length=len(raw)), self.assertRaises(example.PrecheckError):
                example.check_synthetic_invoice()

    def test_contract_fingerprint_types_and_contradictions_fail_closed(self):
        bad = []
        for key, value in (("contract", "other"), ("chargeable", True), ("chargeable", 0),
                           ("eligible_for_paid_dossier", "true"), ("eligible_for_paid_dossier", 1),
                           ("issues", None), ("issues", ["bad"]),
                           ("issues", [{"code": "<untrusted>"}]), ("input_fingerprint", "wrong"),
                           ("issues", [{"code": "CONFLICT"}]), ("eligible_for_paid_dossier", False)):
            payload = copy.deepcopy(prepared())
            payload[key] = value
            bad.append(payload)
        for payload in bad:
            self.opener.open.return_value = Response(payload)
            with self.subTest(payload=payload), self.assertRaises(example.PrecheckError):
                example.check_synthetic_invoice()

    def test_cli_only_prints_safe_summary_and_returns_failure_on_error(self):
        self.opener.open.return_value = Response()
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(0, example.main([]))
        self.assertEqual(0, json.loads(stdout.getvalue())["paid_calls"])
        self.assertEqual("", stderr.getvalue())
        self.opener.open.return_value = Response(raw=b'no')
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(1, example.main([]))
        self.assertEqual("", stdout.getvalue())
        self.assertIn("stopped", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
