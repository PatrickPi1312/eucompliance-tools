#!/usr/bin/env python3
"""Runnable example: an agent pays per call in USDC on Base and uses every endpoint.

Setup:
    pip install "x402[evm]" requests
    export X402_PRIVATE_KEY=0x...        # wallet holding USDC on Base (no ETH needed)

Run:
    python pay_and_call.py
"""
from __future__ import annotations

import json
import os
import sys

import requests
from eth_account import Account
from x402.client import x402ClientSync
from x402.http.clients.requests import wrapRequestsWithPayment
from x402.mechanisms.evm.exact.register import register_exact_evm_client
from x402.mechanisms.evm.signers import EthAccountSigner

BASE = os.environ.get("EUCOMPLIANCE_BASE", "https://api.eucompliance.tools")


def build_session():
    key = os.environ.get("X402_PRIVATE_KEY")
    if not key:
        sys.exit("X402_PRIVATE_KEY is not set (wallet with USDC on Base)")
    client = x402ClientSync()
    register_exact_evm_client(client, EthAccountSigner(Account.from_key(key)),
                              networks="eip155:8453")
    return wrapRequestsWithPayment(requests.Session(), client)


def show(title: str, response: requests.Response) -> None:
    print(f"\n=== {title}  [HTTP {response.status_code}]")
    if response.status_code != 200:
        print(response.text[:300])
        return
    print(json.dumps(response.json(), indent=2, ensure_ascii=False)[:900])


def main() -> None:
    s = build_session()

    # 1) One-call due diligence before onboarding a supplier ($0.02)
    show("Counterparty check", s.get(f"{BASE}/x402/counterparty",
                                     params={"name": "Acme GmbH", "vat": "ATU16170006"},
                                     timeout=180))

    # 2) Sanctions screening only ($0.01)
    show("Sanctions screening", s.get(f"{BASE}/x402/sanctions",
                                      params={"name": "Acme GmbH", "country": "AT"},
                                      timeout=180))

    # 3) How to invoice a cross-border service ($0.003)
    show("VAT rules", s.get(f"{BASE}/x402/vat-rules",
                            params={"supplier": "AT", "customer": "DE",
                                    "b2b": "true", "type": "service"},
                            timeout=180))

    # 4) VAT ID against the live VIES register ($0.002)
    show("VAT ID validation", s.get(f"{BASE}/x402/vat/ATU16170006", timeout=180))

    # 5) IBAN structure and checksum ($0.002)
    show("IBAN validation", s.get(f"{BASE}/x402/iban/AT151420020014409310", timeout=180))

    # 6) Receipt or invoice to structured JSON ($0.02)
    path = os.environ.get("RECEIPT_FILE")
    if path and os.path.exists(path):
        with open(path, "rb") as fh:
            show("Receipt extraction",
                 s.post(f"{BASE}/x402/extract",
                        files={"file": (os.path.basename(path), fh.read(), "application/pdf")},
                        timeout=300))
    else:
        print("\n=== Receipt extraction skipped (set RECEIPT_FILE=/path/to/receipt.pdf)")


if __name__ == "__main__":
    main()
