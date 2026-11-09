#!/usr/bin/env python3
"""One FREE input check using a fixed synthetic UBL example (Python 3.10+).

No account, credentials, wallet, signer, paid call, or automatic retry. This is
not an EN 16931-conformant sample or a real invoice: its VAT ID is deliberately
fictional. Eligibility means preparation only, never invoice/payment approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

FREE_URL = "https://api.eucompliance.tools/free/invoice-to-pay/check"
CONTRACT = "eu-invoice-to-pay-input-check/1"
TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 256 * 1024
SYNTHETIC_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
 xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
 xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">
 <cbc:ID>SYNTHETIC-NOT-A-REAL-INVOICE-001</cbc:ID>
 <cbc:IssueDate>2026-11-01</cbc:IssueDate>
 <cbc:InvoiceTypeCode>380</cbc:InvoiceTypeCode>
 <cbc:Note>Fictional preparation example only; do not pay.</cbc:Note>
 <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
 <cac:AccountingSupplierParty><cac:Party>
  <cac:PostalAddress><cac:Country><cbc:IdentificationCode>DE</cbc:IdentificationCode></cac:Country></cac:PostalAddress>
  <cac:PartyTaxScheme><cbc:CompanyID>DE000000000</cbc:CompanyID></cac:PartyTaxScheme>
  <cac:PartyLegalEntity><cbc:RegistrationName>Synthetic Supplier - Not Real</cbc:RegistrationName></cac:PartyLegalEntity>
 </cac:Party></cac:AccountingSupplierParty>
 <cac:AccountingCustomerParty><cac:Party>
  <cac:PostalAddress><cac:Country><cbc:IdentificationCode>AT</cbc:IdentificationCode></cac:Country></cac:PostalAddress>
  <cac:PartyLegalEntity><cbc:RegistrationName>Synthetic Buyer - Not Real</cbc:RegistrationName></cac:PartyLegalEntity>
 </cac:Party></cac:AccountingCustomerParty>
 <cac:PaymentMeans><cbc:PaymentMeansCode>30</cbc:PaymentMeansCode>
  <cac:PayeeFinancialAccount><cbc:ID>DE89370400440532013000</cbc:ID></cac:PayeeFinancialAccount>
 </cac:PaymentMeans>
 <cac:LegalMonetaryTotal><cbc:PayableAmount currencyID="EUR">120.00</cbc:PayableAmount></cac:LegalMonetaryTotal>
</Invoice>"""


class PrecheckError(Exception):
    """Stop without retrying, following returned instructions, or paying."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PrecheckError("Redirect refused; no redirected request was sent.")


def synthetic_request(*, missing_trusted_payee=False):
    # The IBAN is a public checksum example, not proof of an account or owner.
    return {"invoice_xml": SYNTHETIC_XML, "filename": "synthetic-only.xml",
            "buyer_country": "AT", "expected_amount": "120.00", "expected_currency": "EUR",
            "trusted_payee_iban": "" if missing_trusted_payee else "DE89370400440532013000"}


def _fingerprint(body):
    # Exact eu-invoice-to-pay/1 context defaults, bound to this synthetic body.
    material = {
        "document_sha256": hashlib.sha256(body["invoice_xml"].encode()).hexdigest(),
        "context": {"buyer_country": body["buyer_country"], "expected_amount": body["expected_amount"],
                    "expected_currency": body["expected_currency"], "trusted_payee_iban": body["trusted_payee_iban"],
                    "expected_supplier_vat": "", "expected_supplier_name": "", "expected_buyer_vat": "",
                    "purchase_order_reference": "", "supply_type": "service", "b2b": True},
        "contract": "eu-invoice-to-pay/1",
    }
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def _constant_rejected(value):
    raise ValueError("non-finite JSON")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def check_synthetic_invoice(*, missing_trusted_payee=False):
    """Send only our fixed synthetic input to the fixed FREE endpoint, once."""
    if FREE_URL != "https://api.eucompliance.tools/free/invoice-to-pay/check":
        raise PrecheckError("Only the fixed free HTTPS endpoint is allowed.")
    body = synthetic_request(missing_trusted_payee=missing_trusted_payee)
    request = Request(FREE_URL, method="POST", data=json.dumps(body).encode(),
                      headers={"Accept": "application/json", "Content-Type": "application/json"})
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            if response.status != 200 or response.geturl() != FREE_URL:
                raise PrecheckError("Unexpected status or response URL; check stopped.")
            media_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if media_type != "application/json":
                raise PrecheckError("Expected application/json; check stopped.")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise PrecheckError("Response too large; check stopped.")
    except HTTPError as exc:
        exc.close()
        raise PrecheckError(f"HTTP {exc.code}; no retry or payment was attempted.") from None
    except (URLError, OSError):
        raise PrecheckError("HTTPS request failed or timed out; no retry was attempted.") from None
    try:
        data = json.loads(raw.decode("utf-8"), parse_constant=_constant_rejected,
                          object_pairs_hook=_unique_object)
        json.dumps(data, allow_nan=False)
    except (ValueError, UnicodeError, RecursionError):
        raise PrecheckError("Invalid UTF-8 JSON; check stopped.") from None
    if (not isinstance(data, dict) or data.get("contract") != CONTRACT
            or data.get("chargeable") is not False
            or type(data.get("eligible_for_paid_dossier")) is not bool
            or not isinstance(data.get("issues"), list)):
        raise PrecheckError("Unexpected free-preparation contract; check stopped.")
    codes = []
    for issue in data["issues"]:
        code = issue.get("code") if isinstance(issue, dict) else None
        if not isinstance(code, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", code):
            raise PrecheckError("Invalid issue code; check stopped.")
        codes.append(code)
    if data.get("input_fingerprint") != _fingerprint(body):
        raise PrecheckError("Preparation does not match the synthetic input; check stopped.")
    eligible = data["eligible_for_paid_dossier"]
    if eligible == bool(codes):
        raise PrecheckError("Conflicting preparation outcome; check stopped.")
    # Do not execute or echo purchase URLs, remote prose, raw XML or bank fields.
    return {"example": "synthetic_only", "contract": CONTRACT, "chargeable": False,
            "eligible_for_paid_dossier": eligible, "issue_codes": codes,
            "invoice_approved": False, "payment_authorized": False, "paid_calls": 0,
            "note": "Preparation only, not full EN16931, VIES or sanctions validation. No payment."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--missing-trusted-payee", action="store_true",
                        help="Demonstrate a held input, still using only fictional data.")
    args = parser.parse_args(argv)
    try:
        summary = check_synthetic_invoice(missing_trusted_payee=args.missing_trusted_payee)
    except PrecheckError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
