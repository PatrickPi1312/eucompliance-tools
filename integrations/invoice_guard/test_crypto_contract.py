"""Real offline EIP-191 and pure seller-contract compatibility checks.

No configured account, production signer, RPC, server application or paid handler
is imported. Test accounts are generated only in RAM and never printed/stored.
"""
from __future__ import annotations

import ast
import base64
import copy
import hashlib
import importlib.util
import json
import re
import tempfile
import unittest
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from .adapter import (CONTRACT, PRECHECK_CONTRACT, InvoiceGuard, SQLiteJournal,
                      bind_request, receipt_message, result_hash)
from .test_adapter import FakeTransport, NOW, dossier, request
from .verify_eip191 import make_verifier

try:
    from eth_account import Account
    from eth_account.messages import encode_defunct
except ImportError:
    HAS_ETH_ACCOUNT = False
else:
    HAS_ETH_ACCOUNT = True

ROOT = Path(__file__).resolve().parents[2]


def pure_functions(path: Path, names: set[str], namespace: dict) -> dict:
    """Load selected function/constant definitions, never module startup code."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    definitions = []
    found = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names:
            definitions.append(node)
            found.add(node.name)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in names:
                definitions.append(node)
                found.add(target.id)
    if found != names:
        raise AssertionError(f"source contract changed: missing {sorted(names - found)}")
    module = ast.Module(body=definitions, type_ignores=[])
    exec(compile(module, str(path), "exec"), namespace)
    return namespace


@unittest.skipUnless(HAS_ETH_ACCOUNT, "optional eth-account==0.13.7 is not installed")
class EIP191Tests(unittest.TestCase):
    def setUp(self):
        # Pure cryptography accounts: neither is ever submitted to any network.
        self.signer = Account.create()
        self.other_signer = Account.create()
        self.verifier = make_verifier(trusted_issuer=self.signer.address)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.transport = FakeTransport()
        self.transport.delivery_edit = self.sign
        self.guard = InvoiceGuard(transport=self.transport, verifier=self.verifier,
                                  journal=SQLiteJournal(Path(self.tmp.name) / "proof.sqlite3"),
                                  clock=lambda: NOW)
        self.no_network = mock.patch("socket.create_connection", side_effect=AssertionError("network forbidden"))
        self.no_network.start()
        self.addCleanup(self.no_network.stop)

    def sign(self, document: dict, signer=None):
        signer = self.signer if signer is None else signer
        document = copy.deepcopy(document)
        envelope = {
            "standard": "eucompliance-receipt/2", "service": "invoice_to_pay_dossier",
            "subject": document["input_fingerprint"][-24:],
            "issuer": "offline ephemeral test issuer", "issuer_key": signer.address,
            "key_id": "eip191:" + signer.address.lower(),
            "result_hash": result_hash(document), "issued_at": NOW.isoformat(),
            "valid_until": document["validity"]["fresh_until"],
        }
        envelope["signed_message"] = receipt_message(envelope)
        envelope["signature"] = "0x" + signer.sign_message(
            encode_defunct(text=envelope["signed_message"])).signature.hex().removeprefix("0x")
        document["receipt"] = envelope
        document["extensions"] = {"delivery-receipt": copy.deepcopy(envelope)}
        return document

    def test_real_eip191_v2_sign_recover_and_guard_accept(self):
        signed = self.sign(dossier(request()))
        self.assertTrue(self.verifier(signed))
        recovered = Account.recover_message(encode_defunct(text=receipt_message(signed["receipt"])),
                                            signature=signed["receipt"]["signature"])
        self.assertEqual(recovered.lower(), self.signer.address.lower())
        result = self.guard.evaluate(request())
        self.assertEqual(result.action, "ready_for_buyer_approval")
        self.assertFalse(result.ready_to_pay)
        self.assertTrue(self.guard.evaluate(request()).reused)
        self.assertEqual(len(self.transport.purchases), 1)

    def test_real_eip191_detects_payload_and_signed_envelope_tampering(self):
        signed = self.sign(dossier(request()))
        changed_payload = copy.deepcopy(signed)
        changed_payload["invoice"]["amount_due"] = "999.00"
        self.assertFalse(self.verifier(changed_payload))
        for field, value in (("valid_until", (NOW + timedelta(days=90)).isoformat()),
                             ("service", "another_service"), ("subject", "another_invoice")):
            with self.subTest(field=field):
                changed = copy.deepcopy(signed)
                changed["receipt"][field] = value
                # Updating the displayed envelope still cannot forge its signature.
                changed["receipt"]["signed_message"] = receipt_message(changed["receipt"])
                changed["extensions"]["delivery-receipt"] = copy.deepcopy(changed["receipt"])
                self.assertFalse(self.verifier(changed))

    def test_real_eip191_wrong_issuer_and_relabelled_signature_fail(self):
        wrong = self.sign(dossier(request()), signer=self.other_signer)
        self.assertFalse(self.verifier(wrong))
        wrong["receipt"]["issuer_key"] = self.signer.address
        wrong["receipt"]["key_id"] = "eip191:" + self.signer.address.lower()
        wrong["receipt"]["signed_message"] = receipt_message(wrong["receipt"])
        self.assertFalse(self.verifier(wrong))
        self.transport.delivery_edit = lambda document: self.sign(document, self.other_signer)
        result = self.guard.evaluate(request())
        self.assertEqual(result.action, "stopped")
        self.assertEqual(result.reason, "SIGNATURE_OR_ISSUER_INVALID")

    def test_real_signature_does_not_override_request_binding_or_freshness(self):
        old = self.sign(dossier(request()))
        self.transport.delivery_edit = lambda _: old
        result = self.guard.evaluate({**request(), "expected_amount": "101.00"})
        self.assertEqual(result.reason, "REQUEST_MISMATCH")
        guard = InvoiceGuard(transport=self.transport, verifier=self.verifier,
                             journal=SQLiteJournal(Path(self.tmp.name) / "stale.sqlite3"),
                             clock=lambda: NOW + timedelta(hours=24))
        self.assertEqual(guard.evaluate(request()).reason, "STALE_OR_INVALID_FRESHNESS")


@unittest.skipUnless((ROOT / "app/invoice_to_pay.py").is_file(),
                     "seller source contract only exists in the repository checkout")
class SellerContractTests(unittest.TestCase):
    def test_real_xml_fixture_fingerprint_matches_pure_seller_precheck(self):
        # einvoice's reviewed module startup contains stdlib imports/constants and
        # function/class definitions only. XML facts are local; PDF and subprocess
        # paths are blocked and never used by this test.
        spec = importlib.util.spec_from_file_location("invoice_guard_xml_facts", ROOT / "app/einvoice.py")
        einvoice = importlib.util.module_from_spec(spec)
        with mock.patch("socket.create_connection", side_effect=AssertionError("network forbidden")), \
                mock.patch("subprocess.run", side_effect=AssertionError("subprocess forbidden")):
            spec.loader.exec_module(einvoice)
            pure_validation = pure_functions(ROOT / "app/validate.py", {"IBAN_LENGTHS", "validate_iban"},
                                             {"re": re, "RegistryIBAN": None})
            seller = pure_functions(ROOT / "app/invoice_to_pay.py", {
                "CONTRACT", "PRECHECK_CONTRACT", "PRICE_USDC", "ALLOWED_SUPPLY_TYPES",
                "InputRejected", "_normalise_iban", "_normalise_vat", "_decimal", "_decode_document",
                "_context", "_fingerprint", "precheck",
            }, {"re": re, "base64": base64, "hashlib": hashlib, "json": json,
                "Decimal": Decimal, "InvalidOperation": InvalidOperation, "einvoice": einvoice,
                "validate": SimpleNamespace(validate_iban=pure_validation["validate_iban"])})
            self.assertEqual(CONTRACT, seller["CONTRACT"])
            self.assertEqual(PRECHECK_CONTRACT, seller["PRECHECK_CONTRACT"])
            self.assertEqual(seller["PRICE_USDC"], "1.00")
            raw = (ROOT / "artefakte/en16931-1.3.16/ubl/examples/BIS3_Invoice_positive.XML").read_bytes()
            parsed = einvoice.extract_invoice_facts(raw, "invoice.xml")
            self.assertTrue(parsed["ok"])
            self.assertEqual(parsed["syntax"], "UBL")
            body = {**request(), "invoice_xml": raw.decode("utf-8"),
                    "buyer_country": parsed["facts"]["buyer_country"],
                    "expected_amount": str(parsed["facts"]["payable"]),
                    "expected_currency": parsed["facts"]["currency"],
                    "expected_supplier_vat": "dk 123-456.78", "expected_supplier_name": " Company A ",
                    "expected_buyer_vat": "dk 876.543-21", "purchase_order_reference": " n/a ",
                    "supply_type": "GOODS", "b2b": False}
            # The adapter canonicalizes the expected amount in the body before
            # BOTH transport calls and hashing. Compare the same transmitted body.
            body, fingerprint, document_hash = bind_request(body)
            prepared = seller["precheck"](body)
            self.assertTrue(prepared["eligible_for_paid_dossier"], prepared["issues"])
            self.assertEqual(document_hash, parsed["document_sha256"])
            self.assertEqual(fingerprint, prepared["input_fingerprint"])
            self.assertEqual(fingerprint, seller["_fingerprint"](document_hash, seller["_context"](body)))

    def test_receipt_canonicalization_matches_pure_seller_functions(self):
        seller = pure_functions(ROOT / "app/attest.py", {
            "_ohne_selbstbezug", "canonical_hash", "_message_v2",
        }, {"json": json, "hashlib": hashlib})
        document = dossier(request())
        self.assertEqual(result_hash(document), seller["canonical_hash"](document))
        self.assertEqual(receipt_message(document["receipt"]), seller["_message_v2"](document["receipt"]))
        document["extensions"]["other-evidence"] = {"text": "Österreich", "count": 2}
        self.assertEqual(result_hash(document), seller["canonical_hash"](document))


if __name__ == "__main__":
    unittest.main()
