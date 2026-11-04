#!/usr/bin/env python3
"""Read FREE quickstart metadata only; the historical filename is retained.

Run: python3 examples/pay_and_call.py (Python 3.10+, no dependencies).
No wallet, key, signer, or paid endpoint is used. Automated purchases require an
existing buyer-authorized x402 transport with explicit spend limits and trust
policy. This example does not configure one or execute returned instructions.
"""
from __future__ import annotations

import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

QUICKSTART_URL = "https://api.eucompliance.tools/agent-quickstart.json"
TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 256 * 1024


class QuickstartError(Exception):
    """Discovery stopped without executing a tool or making a payment."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise QuickstartError("Redirect refused; no redirected request was sent.")


def _reject_json_constant(value):
    raise ValueError("Non-finite JSON value")


def fetch_quickstart() -> dict:
    """One unauthenticated GET to the fixed free HTTPS URL; no retries."""
    url = QUICKSTART_URL
    if (urlsplit(url).scheme != "https"
            or url != "https://api.eucompliance.tools/agent-quickstart.json"):
        raise QuickstartError("Only the fixed HTTPS quickstart URL is allowed.")
    request = Request(url, headers={"Accept": "application/json"}, method="GET")
    # Do not load proxy credentials or URLs from the environment.
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise QuickstartError(f"HTTP {response.status}; discovery stopped.")
            if response.geturl() != url:
                raise QuickstartError("Response URL changed; discovery stopped.")
            media_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if media_type != "application/json":
                raise QuickstartError("Expected application/json; discovery stopped.")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise QuickstartError("Quickstart response is too large.")
    except HTTPError as exc:
        exc.close()
        raise QuickstartError(f"HTTP {exc.code}; no retry or payment was attempted.") from None
    except (URLError, OSError):
        raise QuickstartError("HTTPS request failed or timed out; no retry was attempted.") from None
    try:
        data = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
        json.dumps(data, allow_nan=False)
    except (ValueError, UnicodeError, RecursionError):
        raise QuickstartError("Invalid UTF-8 JSON; discovery stopped.") from None
    if not isinstance(data, dict) or not data:
        raise QuickstartError("Expected a non-empty JSON object; discovery stopped.")
    return data


def main() -> int:
    try:
        metadata = fetch_quickstart()
    except QuickstartError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(metadata, indent=2, ensure_ascii=True, allow_nan=False))
    print("Free discovery only. No wallet access, signing, or paid tool calls. "
          "Automatic buying requires your existing buyer-authorized, capped x402 transport; "
          "this example does not configure it.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
