#!/usr/bin/env python3
"""Validate an e-invoice against the complete official EN 16931 rule set — in one call.

Checks UBL or CII XML (XRechnung, ZUGFeRD / Factur-X, Peppol BIS) against the
full official EN 16931 Schematron (CEN validation artefacts, used unmodified),
plus totals arithmetic and VAT category rules. Every finding carries its
official business-rule code (BR-..) and severity, and the result is signed so
it can be attached to the document as validation evidence.

No wallet and no account needed for this demo: it uses the free tier
(20 calls a day per IP). For volume, the same check is one x402 call at
/x402/einvoice — see examples/pay_and_call.py.

Run it (catches a deliberately broken sample out of the box):
    pip install requests
    python validate_einvoice.py

Validate your own invoice instead:
    python validate_einvoice.py path/to/invoice.xml        # UBL or CII
    python validate_einvoice.py path/to/zugferd.pdf        # ZUGFeRD / Factur-X PDF
"""
from __future__ import annotations

import json
import sys

import requests

BASE = "https://api.eucompliance.tools"

# A deliberately broken UBL invoice: it has no invoice number (BT-1), so a
# conformant validator must report BR-02. Handy for a zero-setup demo.
BROKEN_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:CustomizationID>urn:cen.eu:en16931:2017</cbc:CustomizationID>
  <cbc:IssueDate>2026-08-04</cbc:IssueDate>
  <cbc:InvoiceTypeCode>380</cbc:InvoiceTypeCode>
  <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty><cac:Party>
    <cac:PartyName><cbc:Name>Muster GmbH</cbc:Name></cac:PartyName>
  </cac:Party></cac:AccountingSupplierParty>
  <cac:AccountingCustomerParty><cac:Party>
    <cac:PartyName><cbc:Name>Beispiel AG</cbc:Name></cac:PartyName>
  </cac:Party></cac:AccountingCustomerParty>
</Invoice>"""


def validate(data: bytes, filename: str) -> dict:
    """Send the document to the free EN 16931 validation endpoint."""
    r = requests.post(f"{BASE}/free/einvoice",
                      files={"file": (filename, data, "application/octet-stream")},
                      timeout=120)
    if r.status_code == 429:
        sys.exit("Free daily quota reached. Use the paid endpoint /x402/einvoice "
                 "(see examples/pay_and_call.py) — no limit.")
    r.raise_for_status()
    return r.json()


def main() -> None:
    if len(sys.argv) > 1:
        with open(sys.argv[1], "rb") as fh:
            data, name = fh.read(), sys.argv[1]
    else:
        data, name = BROKEN_SAMPLE, "broken-sample.xml"
        print("No file given — validating a deliberately broken sample "
              "(missing invoice number).\n")

    result = validate(data, name)

    print(f"Document : {result.get('syntax')} / {result.get('profile')}")
    print(f"Valid    : {result.get('valid')}")
    print(f"Full EN 16931 Schematron applied: {result.get('full_schematron')}")

    findings = (result.get("schematron") or {}).get("findings", [])
    errors = result.get("errors", [])
    if not findings and not errors:
        print("\nNo findings — this invoice passes.")
    else:
        print(f"\nFindings ({len(findings) + len(errors)}):")
        for f in errors:
            print(f"  [core]  {f.get('rule'):12} {f.get('message')}")
        for f in findings:
            print(f"  [{f.get('flag','?'):5}] {f.get('rule'):12} {f.get('message')}")

    receipt = result.get("receipt", {})
    if receipt:
        print(f"\nSigned by {receipt.get('issuer_key')} — verify for free at "
              f"{BASE}/verify (no account, checked offline).")


if __name__ == "__main__":
    main()
