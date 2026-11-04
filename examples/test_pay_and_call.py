"""Offline regression tests: all HTTP openers mocked, no payments or requests."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

SPEC = importlib.util.spec_from_file_location("free_quickstart_example", Path(__file__).with_name("pay_and_call.py"))
example = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(example)


class Response(io.BytesIO):
    def __init__(self, body=b'{"version":"test"}', *, status=200,
                 url=example.QUICKSTART_URL, content_type="application/json"):
        super().__init__(body)
        self.status, self.url = status, url
        self.headers = {"Content-Type": content_type}

    def geturl(self):
        return self.url


class FreeQuickstartTests(unittest.TestCase):
    def setUp(self):
        self.opener = MagicMock()
        self.build = patch.object(example, "build_opener", return_value=self.opener).start()
        self.addCleanup(patch.stopall)

    def test_fixed_free_get_with_timeout_no_credentials_or_environment_override(self):
        self.opener.open.return_value = Response()
        with patch.dict("os.environ", {"EUCOMPLIANCE_BASE": "http://invalid.example",
                                       "X402_PRIVATE_KEY": "SYNTHETIC-NOT-A-KEY"}):
            self.assertEqual({"version": "test"}, example.fetch_quickstart())
        self.opener.open.assert_called_once()
        request = self.opener.open.call_args.args[0]
        self.assertEqual("https://api.eucompliance.tools/agent-quickstart.json", request.full_url)
        self.assertEqual("GET", request.method)
        self.assertIsNone(request.data)
        self.assertEqual({"Accept": "application/json"}, dict(request.header_items()))
        self.assertEqual({"timeout": 10}, self.opener.open.call_args.kwargs)
        proxy, redirect = self.build.call_args.args
        self.assertEqual({}, proxy.proxies)
        self.assertIsInstance(redirect, example.NoRedirect)

    def test_redirect_handler_rejects_before_second_request(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status), self.assertRaises(example.QuickstartError):
                example.NoRedirect().redirect_request(None, None, status, "", {}, "https://other.example/")
        self.opener.open.assert_not_called()

    def test_changed_or_non_https_initial_url_stops_before_network(self):
        for url in ("http://api.eucompliance.tools/agent-quickstart.json", "https://other.example/free"):
            with patch.object(example, "QUICKSTART_URL", url), self.assertRaises(example.QuickstartError):
                example.fetch_quickstart()
        self.opener.open.assert_not_called()

    def test_non_200_including_402_stops_without_retry(self):
        for status in (201, 301, 302, 402, 500):
            with self.subTest(status=status):
                self.opener.open.reset_mock()
                self.opener.open.return_value = Response(b'not json', status=status)
                with self.assertRaises(example.QuickstartError):
                    example.fetch_quickstart()
                self.opener.open.assert_called_once()

    def test_http_errors_and_timeout_stop(self):
        for error in (HTTPError(example.QUICKSTART_URL, 402, "", {}, None), URLError("offline"), TimeoutError()):
            with self.subTest(error=type(error).__name__):
                self.opener.open.reset_mock()
                self.opener.open.side_effect = error
                with self.assertRaises(example.QuickstartError):
                    example.fetch_quickstart()
                self.opener.open.assert_called_once()

    def test_changed_response_url_or_content_type_stops(self):
        for response in (Response(url="http://api.eucompliance.tools/agent-quickstart.json"),
                         Response(url="https://other.example/free"), Response(content_type="text/html")):
            self.opener.open.return_value = response
            with self.assertRaises(example.QuickstartError):
                example.fetch_quickstart()

    def test_invalid_json_utf8_nonobject_and_oversize_stop(self):
        for body in (b'not json', b'\xff', b'[]', b'{}', b'{"amount":NaN}',
                     b'{"amount":1e309}', b'[' * 2000, b'x' * (example.MAX_RESPONSE_BYTES + 1)):
            with self.subTest(length=len(body)):
                self.opener.open.return_value = Response(body)
                with self.assertRaises(example.QuickstartError):
                    example.fetch_quickstart()

    def test_main_displays_metadata_and_no_payment_notice(self):
        self.opener.open.return_value = Response()
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(0, example.main())
        self.assertEqual("test", json.loads(stdout.getvalue())["version"])
        self.assertIn("No wallet access", stderr.getvalue())
        self.assertIn("buyer-authorized", stderr.getvalue())

    def test_main_error_returns_failure_without_partial_output(self):
        self.opener.open.return_value = Response(b'not json')
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(1, example.main())
        self.assertEqual("", stdout.getvalue())
        self.assertIn("discovery stopped", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
