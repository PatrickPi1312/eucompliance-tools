"""Offline only: deterministic signed fixtures, no HTTP, wallet, or real payment.

HMAC is a TEST verifier to exercise the injected-verifier boundary. Production
must use an issuer-pinned EIP-191 verifier, e.g. verify_eip191.make_verifier.
"""
import copy
import hashlib
import hmac
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .adapter import (CONTRACT, MAX_USDC_ATOMIC, PAID_URL, PRECHECK_CONTRACT,
                      InvoiceGuard, Reply, SQLiteJournal, bind_request,
                      receipt_message, result_hash)

NOW = datetime(2026, 9, 6, 18, 0, tzinfo=timezone.utc)
TEST_ISSUER = "0x" + "1" * 40
TEST_HMAC_KEY = b"offline-test-fixtures-not-a-wallet-key"
CHECKS = ("en16931_conformance", "document_type", "amount_consistency",
          "currency_consistency", "buyer_country", "supplier_vies",
          "supplier_name_vies", "sanctions", "payment_account", "vat_treatment")


def request():
    # The seller's free validator, not this adapter, parses/validates real UBL/CII.
    return {"invoice_xml": "<Invoice>offline fixture</Invoice>", "buyer_country": "AT",
            "expected_amount": "100.00", "expected_currency": "EUR",
            "trusted_payee_iban": "DE89370400440532013000"}


def sign(document, issuer=TEST_ISSUER):
    document = copy.deepcopy(document)
    old = document.get("receipt", {})
    receipt = {
        "standard": "eucompliance-receipt/2", "service": "invoice_to_pay_dossier",
        "issuer_key": issuer, "issued_at": old.get("issued_at", NOW.isoformat()),
        "valid_until": document["validity"]["fresh_until"],
        "result_hash": result_hash(document),
    }
    receipt["signed_message"] = receipt_message(receipt)
    receipt["signature"] = hmac.new(TEST_HMAC_KEY, receipt["signed_message"].encode(),
                                     hashlib.sha256).hexdigest()
    document["receipt"] = receipt
    document["extensions"] = {"delivery-receipt": copy.deepcopy(receipt)}
    return document


def verify(document):
    receipt = document["receipt"]
    expected = hmac.new(TEST_HMAC_KEY, receipt_message(receipt).encode(), hashlib.sha256).hexdigest()
    return receipt["issuer_key"] == TEST_ISSUER and hmac.compare_digest(expected, receipt["signature"])


def dossier(body):
    _, fingerprint, doc_hash = bind_request(body)
    return sign({
        "contract": CONTRACT, "policy_version": "invoice-payment-policy/1",
        "input_fingerprint": fingerprint,
        "invoice": {"document_sha256": doc_hash, "amount_due": body["expected_amount"],
                    "currency": body["expected_currency"]},
        "decision": {"action": "READY_FOR_BUYER_APPROVAL", "ready_for_buyer_approval": True,
                     "ready_to_pay": False, "blocking_reasons": [], "review_reasons": []},
        "validity": {"checked_at": NOW.isoformat(), "fresh_until": (NOW + timedelta(hours=24)).isoformat()},
        "pricing": {"price_usdc": "1.00", "additional_charges": False,
                    "paid_follow_ups_required": False},
        "checks": [{"id": name, "status": "pass"} for name in CHECKS],
        "evidence_coverage": {"required": len(CHECKS), "passed": len(CHECKS),
                              "failed": 0, "review": 0, "unknown": 0},
        "payment": {"matches_buyer_trusted_iban": True, "structurally_valid": True,
                    "trusted_payee_account": {"sha256": hashlib.sha256(body["trusted_payee_iban"].encode()).hexdigest()}},
    })


class FakeTransport:
    def __init__(self):
        self.prechecks, self.purchases, self.caps = [], [], []
        self.precheck_edit = lambda body: body
        self.delivery_edit = lambda body: body
        self.purchase_error = None
        self.precheck_barrier = None

    def precheck(self, body, *, timeout_seconds):
        self.prechecks.append(copy.deepcopy(body))
        if self.precheck_barrier:
            self.precheck_barrier.wait(timeout=3)
        payload = {"contract": PRECHECK_CONTRACT, "chargeable": False,
                   "eligible_for_paid_dossier": True, "issues": [],
                   "input_fingerprint": bind_request(body)[1],
                   "purchase": {"method": "POST", "url": PAID_URL, "price": "1.00 USDC",
                                "requires_same_input_fingerprint": True,
                                "no_settlement_on_4xx_or_5xx": True}}
        return Reply(self.precheck_edit(payload))

    def purchase(self, body, *, max_usdc_atomic, timeout_seconds):
        self.purchases.append(copy.deepcopy(body))
        self.caps.append(max_usdc_atomic)
        if self.purchase_error:
            raise self.purchase_error
        return Reply(self.delivery_edit(dossier(body)))


class InvoiceGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "journal.sqlite3"
        self.transport = FakeTransport()
        self.guard = self.new_guard()

    def new_guard(self, **overrides):
        return InvoiceGuard(transport=self.transport, verifier=overrides.get("verifier", verify),
                            journal=SQLiteJournal(self.path), clock=overrides.get("clock", lambda: NOW))

    def assertStopped(self, result, reason=None):
        self.assertEqual(result.action, "stopped")
        self.assertFalse(result.ready_to_pay)
        if reason:
            self.assertEqual(result.reason, reason)

    def test_success_only_routes_to_buyer_approval_and_reuses_after_restart(self):
        first = self.guard.evaluate(request())
        self.assertEqual(first.action, "ready_for_buyer_approval")
        self.assertFalse(first.ready_to_pay)
        self.assertFalse(first.reused)
        first.dossier["decision"]["ready_to_pay"] = True
        second = self.new_guard().evaluate(request())
        self.assertEqual(second.action, "ready_for_buyer_approval")
        self.assertTrue(second.reused)
        self.assertFalse(second.dossier["decision"]["ready_to_pay"])
        self.assertEqual(len(self.transport.prechecks), 1)
        self.assertEqual(len(self.transport.purchases), 1)
        self.assertEqual(self.transport.prechecks, self.transport.purchases)
        self.assertEqual(self.transport.caps, [MAX_USDC_ATOMIC])
        with closing(sqlite3.connect(self.path)) as db:
            stored = db.execute("SELECT dossier FROM invoice_guard").fetchone()[0]
        self.assertNotIn(request()["invoice_xml"], stored)
        self.assertNotIn(request()["trusted_payee_iban"], stored)

    def test_local_fingerprint_matches_document_and_all_normalized_context(self):
        body = request()
        body.update(expected_supplier_vat="de 123-456.789", expected_supplier_name=" A GmbH ",
                    expected_buyer_vat="at-u 123", purchase_order_reference=" PO1 ",
                    supply_type="GOODS", b2b=False)
        _, actual, document_hash = bind_request(body)
        context = {"buyer_country": "AT", "expected_amount": "100.00", "expected_currency": "EUR",
                   "trusted_payee_iban": "DE89370400440532013000", "expected_supplier_vat": "DE123456789",
                   "expected_supplier_name": "A GmbH", "expected_buyer_vat": "ATU123",
                   "purchase_order_reference": "PO1", "supply_type": "goods", "b2b": False}
        material = {"document_sha256": hashlib.sha256(body["invoice_xml"].encode()).hexdigest(),
                    "context": context, "contract": CONTRACT}
        expected = "sha256:" + hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":"),
                                                         ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(actual, expected)
        self.assertEqual(document_hash, material["document_sha256"])

    def test_equivalent_amount_formats_share_one_durable_purchase(self):
        for amount in ("100", "100.0", "100.00"):
            result = self.new_guard().evaluate({**request(), "expected_amount": amount})
            self.assertEqual(result.action, "ready_for_buyer_approval")
        self.assertEqual(len(self.transport.purchases), 1)
        self.assertEqual(len(self.transport.prechecks), 1)
        self.assertEqual(self.transport.purchases[0]["expected_amount"], "100.00")
        self.assertEqual(self.transport.prechecks, self.transport.purchases)

    def test_buyer_held_supplier_name_replaces_absent_vies_name(self):
        def no_published_vies_name(document):
            document["checks"] = [item for item in document["checks"]
                                  if item["id"] != "supplier_name_vies"]
            document["checks"].append({"id": "supplier_name_expected", "status": "pass"})
            return sign(document)
        self.transport.delivery_edit = no_published_vies_name
        body = {**request(), "expected_supplier_name": "Buyer trusted supplier GmbH"}
        result = self.guard.evaluate(body)
        self.assertEqual(result.action, "ready_for_buyer_approval")
        self.assertTrue(self.new_guard().evaluate(body).reused)
        self.assertEqual(len(self.transport.purchases), 1)

    def test_missing_both_supplier_name_checks_stops(self):
        def absent_names(document):
            document["checks"] = [item for item in document["checks"]
                                  if item["id"] != "supplier_name_vies"]
            document["evidence_coverage"].update(required=len(CHECKS) - 1, passed=len(CHECKS) - 1)
            return sign(document)
        self.transport.delivery_edit = absent_names
        for amount, name in (("100.00", ""), ("101.00", "Buyer trusted supplier GmbH")):
            body = {**request(), "expected_amount": amount, "expected_supplier_name": name}
            self.assertStopped(self.guard.evaluate(body), "INCOMPLETE_EVIDENCE")

    def test_invalid_input_never_contacts_transport(self):
        for change in ({"document_base64": "AA=="}, {"invoice_xml": "%PDF-1"},
                       {"expected_amount": "NaN"}, {"expected_amount": "100.001"},
                       {"expected_amount": True}, {"b2b": "false"}, {"trusted_payee_iban": ""}):
            with self.subTest(change=change):
                self.assertStopped(self.guard.evaluate({**request(), **change}))
        self.assertEqual(self.transport.prechecks, [])
        self.assertEqual(self.transport.purchases, [])

    def test_free_precheck_rejection_prevents_purchase(self):
        self.transport.precheck_edit = lambda body: {**body, "eligible_for_paid_dossier": False, "purchase": None}
        self.assertStopped(self.guard.evaluate(request()), "FREE_PRECHECK_REJECTED")
        self.assertEqual(self.transport.purchases, [])

    def test_precheck_wrong_fingerprint_prevents_purchase(self):
        self.transport.precheck_edit = lambda body: {**body, "input_fingerprint": "sha256:" + "0" * 64}
        self.assertStopped(self.guard.evaluate(request()), "FREE_PRECHECK_REJECTED")
        self.assertEqual(self.transport.purchases, [])

    def test_price_increase_and_wrong_resource_never_purchase(self):
        for field, value in (("price", "1.01 USDC"), ("url", "https://evil.invalid/buy"),
                             ("method", "GET"), ("no_settlement_on_4xx_or_5xx", False)):
            with self.subTest(field=field):
                self.transport.precheck_edit = lambda body: {**body, "purchase": {**body["purchase"], field: value}}
                self.assertStopped(self.guard.evaluate(request()), "PRICE_OR_RESOURCE_MISMATCH")
        self.assertEqual(self.transport.purchases, [])

    def test_timeout_is_not_rebought_after_restart(self):
        self.transport.purchase_error = TimeoutError("secret must not appear")
        first = self.guard.evaluate(request())
        self.assertStopped(first, "DEPENDENCY_OR_MALFORMED_RESPONSE")
        self.assertNotIn("secret", repr(first))
        self.assertStopped(self.new_guard().evaluate(request()), "PRIOR_PURCHASE_REQUIRES_RECONCILIATION")
        self.assertEqual(len(self.transport.purchases), 1)

    def test_explicit_unpaid_error_stops_without_automatic_repurchase(self):
        self.transport.delivery_edit = lambda _: {"error": "VIES unavailable", "charged": False}
        self.assertStopped(self.guard.evaluate(request()), "TOOL_OR_TRANSPORT_ERROR")
        self.assertStopped(self.guard.evaluate(request()), "PRIOR_PURCHASE_REQUIRES_RECONCILIATION")
        self.assertEqual(len(self.transport.purchases), 1)

    def test_http_or_mcp_error_cannot_become_success(self):
        for reply in (Reply(dossier(request()), status_code=402),
                      Reply(dossier(request()), is_error=True)):
            with self.subTest(reply=reply.status_code):
                self.transport.precheck = lambda *args, **kwargs: reply
                self.assertStopped(self.guard.evaluate(request()), "TOOL_OR_TRANSPORT_ERROR")
        self.assertEqual(self.transport.purchases, [])

    def test_changed_input_cannot_reuse_old_dossier(self):
        self.guard.evaluate(request())
        old = dossier(request())
        self.transport.delivery_edit = lambda _: old
        for change in ({"expected_amount": "101.00"}, {"invoice_xml": "<Invoice>changed</Invoice>"},
                       {"trusted_payee_iban": "GB82WEST12345698765432"}):
            with self.subTest(change=change):
                self.assertStopped(self.guard.evaluate({**request(), **change}), "REQUEST_MISMATCH")
        self.assertEqual(len(self.transport.purchases), 4)

    def test_payload_tampering_fails_even_with_permissive_verifier(self):
        def mutate(document):
            document["invoice"]["amount_due"] = "0.01"
            return document
        self.transport.delivery_edit = mutate
        self.assertStopped(self.new_guard(verifier=lambda _: True).evaluate(request()), "INTEGRITY_FAILED")

    def test_forged_signature_or_wrong_issuer_stops(self):
        def forge(document):
            document["receipt"]["signature"] = "forged"
            document["extensions"]["delivery-receipt"] = copy.deepcopy(document["receipt"])
            return document
        self.transport.delivery_edit = forge
        self.assertStopped(self.guard.evaluate(request()), "SIGNATURE_OR_ISSUER_INVALID")
        changed = {**request(), "expected_amount": "102.00"}
        self.transport.delivery_edit = lambda document: sign(document, issuer="0x" + "2" * 40)
        self.assertStopped(self.guard.evaluate(changed), "SIGNATURE_OR_ISSUER_INVALID")

    def test_verifier_required_and_must_return_literal_true(self):
        with self.assertRaises(ValueError):
            self.new_guard(verifier=None)
        self.assertStopped(self.new_guard(verifier=lambda _: {"valid": True}).evaluate(request()),
                           "SIGNATURE_OR_ISSUER_INVALID")

    def test_mismatched_duplicate_receipt_stops(self):
        def mutate(document):
            document["extensions"]["delivery-receipt"]["service"] = "another_service"
            return document
        self.transport.delivery_edit = mutate
        self.assertStopped(self.guard.evaluate(request()), "RECEIPT_MISMATCH")

    def test_stale_cache_does_not_trigger_another_purchase(self):
        self.guard.evaluate(request())
        result = self.new_guard(clock=lambda: NOW + timedelta(hours=24)).evaluate(request())
        self.assertStopped(result, "STALE_OR_INVALID_FRESHNESS")
        self.assertTrue(result.reused)
        self.assertEqual(len(self.transport.purchases), 1)

    def test_future_or_overlong_freshness_stops(self):
        def overlong(document):
            document["validity"]["fresh_until"] = (NOW + timedelta(days=90)).isoformat()
            return sign(document)
        self.transport.delivery_edit = overlong
        self.assertStopped(self.guard.evaluate(request()), "STALE_OR_INVALID_FRESHNESS")
        self.transport.delivery_edit = lambda document: document
        self.assertStopped(self.new_guard(clock=lambda: NOW - timedelta(seconds=1)).evaluate(
            {**request(), "expected_amount": "102.00"}), "STALE_OR_INVALID_FRESHNESS")

    def test_signed_price_mismatch_stops(self):
        def mutate(document):
            document["pricing"]["price_usdc"] = "1.01"
            return sign(document)
        self.transport.delivery_edit = mutate
        self.assertStopped(self.guard.evaluate(request()), "PRICE_CONTRACT_MISMATCH")

    def test_review_and_denied_dossiers_are_cached_but_never_approve(self):
        for index, (action, status) in enumerate((("HOLD_FOR_REVIEW", "review"), ("DO_NOT_PAY", "fail"))):
            def change(document):
                document["decision"].update(action=action, ready_for_buyer_approval=False)
                document["checks"][0]["status"] = status
                document["evidence_coverage"]["passed"] -= 1
                document["evidence_coverage"]["review" if status == "review" else "failed"] = 1
                return sign(document)
            body = {**request(), "expected_amount": str(100 + index)}
            self.transport.delivery_edit = change
            self.assertStopped(self.guard.evaluate(body), action)
            reused = self.guard.evaluate(body)
            self.assertStopped(reused, action)
            self.assertTrue(reused.reused)
        self.assertEqual(len(self.transport.purchases), 2)

    def test_unknown_decision_and_missing_core_check_stop(self):
        def unknown(document):
            document["decision"]["action"] = "ALLOW_PAYMENT"
            return sign(document)
        self.transport.delivery_edit = unknown
        self.assertStopped(self.guard.evaluate(request()), "UNKNOWN_OR_INVALID_DECISION")
        def incomplete(document):
            document["checks"].pop()
            document["evidence_coverage"].update(required=9, passed=9)
            return sign(document)
        self.transport.delivery_edit = incomplete
        self.assertStopped(self.guard.evaluate({**request(), "expected_amount": "101.00"}), "INCOMPLETE_EVIDENCE")

    def test_two_concurrent_guards_share_one_purchase_claim(self):
        self.transport.precheck_barrier = threading.Barrier(2)
        guards = [self.new_guard(), self.new_guard()]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda guard: guard.evaluate(request()), guards))
        self.assertEqual(len(self.transport.purchases), 1)
        self.assertEqual(sum(result.action == "ready_for_buyer_approval" for result in results), 1)
        self.assertEqual(sum(result.action == "stopped" for result in results), 1)


if __name__ == "__main__":
    unittest.main()
