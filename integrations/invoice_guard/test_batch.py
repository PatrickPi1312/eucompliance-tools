"""Offline batch tests: synthetic invoices, in-memory journal, fake transport.

The HMAC fixture verifier is test-only, not a production receipt verifier. No
network calls, filesystem journal, wallet keys or real payments are used here.
"""
from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

from .adapter import MAX_USDC_ATOMIC, InvoiceGuard, Outcome, Reply
from .batch import MAX_BATCH_ITEMS, evaluate_batch
from .test_adapter import FakeTransport, NOW, request as base_request, sign, verify


def request(number="INV-001", vat="DE123456789", *, amount="100.00", extra=""):
    xml = (
        '<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2" '
        'xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" '
        'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">'
        f"<cbc:ID>{number}</cbc:ID><cac:AccountingSupplierParty><cac:Party>"
        f"<cac:PartyTaxScheme><cbc:CompanyID>{vat}</cbc:CompanyID>"
        "<cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:PartyTaxScheme>"
        "</cac:Party></cac:AccountingSupplierParty>"
        f"{extra}</Invoice>"
    )
    return {**base_request(), "invoice_xml": xml, "expected_amount": amount}


def cii_request(number="INV-001", vat="DE123456789", *, amount="100.00"):
    xml = (
        '<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100" '
        'xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100">'
        f"<rsm:ExchangedDocument><ram:ID>{number}</ram:ID><ram:TypeCode>380</ram:TypeCode>"
        "</rsm:ExchangedDocument><rsm:SupplyChainTradeTransaction>"
        "<ram:ApplicableHeaderTradeAgreement><ram:SellerTradeParty>"
        f'<ram:SpecifiedTaxRegistration><ram:ID schemeID="VA">{vat}</ram:ID>'
        "</ram:SpecifiedTaxRegistration></ram:SellerTradeParty>"
        "</ram:ApplicableHeaderTradeAgreement></rsm:SupplyChainTradeTransaction>"
        "</rsm:CrossIndustryInvoice>"
    )
    return {**base_request(), "invoice_xml": xml, "expected_amount": amount}


def item(name="invoice-1", body=None):
    return {"case_id": name, "request": request() if body is None else body}


class MemoryJournal:
    """Test-only journal; production keeps the existing durable SQLiteJournal."""

    def __init__(self):
        self.rows = {}

    def get(self, fingerprint):
        return copy.deepcopy(self.rows.get(fingerprint))

    def claim(self, fingerprint):
        if fingerprint in self.rows:
            return False
        self.rows[fingerprint] = ("attempted", None)
        return True

    def store_delivery(self, fingerprint, document):
        self.rows[fingerprint] = ("delivered", copy.deepcopy(document))


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.transport = FakeTransport()
        self.guard = InvoiceGuard(transport=self.transport, verifier=verify,
                                  journal=MemoryJournal(), clock=lambda: NOW)

    def evaluate(self, cases, budget=MAX_USDC_ATOMIC):
        result = evaluate_batch(self.guard, cases, max_total_usdc_atomic=budget)
        json.dumps(result, allow_nan=False)
        self.assertIs(result["ready_to_pay"], False)
        for row in result["cases"]:
            self.assertIs(row["ready_to_pay"], False)
        return result

    def assertNoCalls(self):
        self.assertEqual(self.transport.prechecks, [])
        self.assertEqual(self.transport.purchases, [])

    def test_single_case_uses_original_guard_and_returns_evidence_projection(self):
        result = self.evaluate([item()])
        row = result["cases"][0]
        self.assertEqual(result["status"], "completed")
        self.assertEqual(row["action"], "ready_for_buyer_approval")
        self.assertTrue(row["ready_for_buyer_approval"])
        self.assertEqual(row["source_evidence"]["kind"], "guard_validated_dossier_projection")
        self.assertFalse(row["source_evidence"]["standalone_verifiable_receipt"])
        self.assertTrue(row["source_evidence"]["checks"])
        self.assertEqual(self.transport.caps, [MAX_USDC_ATOMIC])
        self.assertIsNone(result["budget"]["actual_spend_usdc_atomic"])
        self.assertFalse(result["budget"]["payment_authority_granted"])

    def test_default_zero_budget_stops_before_precheck(self):
        result = evaluate_batch(self.guard, [item()])
        self.assertEqual(result["errors"], ["BATCH_BUDGET_NOT_AUTHORIZED"])
        self.assertNoCalls()

    def test_insufficient_aggregate_budget_rejects_whole_batch(self):
        result = self.evaluate([item(), item("invoice-2", request(number="INV-002"))])
        self.assertEqual(result["errors"], ["BATCH_BUDGET_EXCEEDED"])
        self.assertEqual(result["budget"]["worst_case_usdc_atomic"], 2 * MAX_USDC_ATOMIC)
        self.assertNoCalls()

    def test_invalid_budgets_do_not_invoke_guard(self):
        for budget in (None, True, -1, 1.0, "1000000", 2**64):
            with self.subTest(budget=budget):
                result = self.evaluate([item()], budget)
                self.assertEqual(result["errors"], ["INVALID_BATCH_BUDGET"])
        self.assertNoCalls()

    def test_technical_limit_is_200_not_a_sold_quota(self):
        cases = [item(f"invoice-{i}") for i in range(MAX_BATCH_ITEMS)]
        result = self.evaluate(cases)
        self.assertEqual(len(result["cases"]), 200)
        self.assertFalse(result["limits"]["sold_quota"])
        self.assertEqual(len(self.transport.purchases), 1)
        self.assertEqual(result["summary"]["manual_review"], 200)

    def test_over_200_materialized_items_rejects_without_processing(self):
        result = self.evaluate([item(f"invoice-{i}") for i in range(201)], budget=201 * MAX_USDC_ATOMIC)
        self.assertEqual(result["errors"], ["BATCH_LIMIT_OR_SHAPE_INVALID"])
        self.assertNoCalls()

    def test_empty_unbounded_generator_and_mapping_are_rejected(self):
        for cases in ([], (), {"x": item()}, (item(str(i)) for i in range(1_000_000))):
            self.assertEqual(self.evaluate(cases)["status"], "rejected")
        self.assertNoCalls()

    def test_wrong_guard_type_never_invokes_arbitrary_evaluator(self):
        class Untrusted:
            def evaluate(self, request):
                raise AssertionError("must not run")
        result = evaluate_batch(Untrusted(), [item()], max_total_usdc_atomic=MAX_USDC_ATOMIC)
        self.assertEqual(result["errors"], ["INVOICE_GUARD_REQUIRED"])

    def test_duplicate_case_ids_reject_whole_batch(self):
        result = self.evaluate([item(), item(body=request(number="INV-002"))], 2 * MAX_USDC_ATOMIC)
        self.assertEqual(result["errors"], ["BATCH_INPUT_REJECTED"])
        self.assertIn("DUPLICATE_CASE_ID", result["cases"][1]["reason_codes"])
        self.assertNoCalls()

    def test_invalid_last_item_prevents_spend_for_valid_first_item(self):
        result = self.evaluate([item(), {"case_id": "bad", "request": {}}], 2 * MAX_USDC_ATOMIC)
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(all(row["action"] == "stopped" for row in result["cases"]))
        self.assertNoCalls()

    def test_case_ids_and_shape_are_strict(self):
        for bad in (item("with spaces"), item("x" * 81), item("bad\nname"),
                    {**item(), "send_to": "https://elsewhere.invalid"}, None):
            with self.subTest(item=bad):
                self.assertEqual(self.evaluate([bad])["status"], "rejected")
        self.assertNoCalls()

    def test_unsupported_request_fields_and_non_string_values_stop_locally(self):
        for body in ({**request(), "callback": "https://elsewhere.invalid"},
                     {**request(), "b2b": "true"},
                     {**request(), "expected_amount": 100},
                     {**request(), "document_base64": "eA=="}):
            self.assertEqual(self.evaluate([item(body=body)])["status"], "rejected")
        self.assertNoCalls()

    def test_extreme_decimal_and_non_finite_amounts_are_rejected_before_formatting(self):
        for amount in ("1e10000000", "NaN", "Infinity", "1.001", "-1", "0.00", "1" * 100):
            self.assertEqual(self.evaluate([item(body=request(amount=amount))])["status"], "rejected")
        self.assertNoCalls()

    def test_xml_byte_context_and_aggregate_limits_are_enforced(self):
        too_large = {**request(), "invoice_xml": "<Invoice>" + "x" * 1_000_000 + "</Invoice>"}
        self.assertEqual(self.evaluate([item(body=too_large)])["status"], "rejected")
        multibyte = {**request(), "invoice_xml": "<Invoice>" + "é" * 600_000 + "</Invoice>"}
        self.assertEqual(self.evaluate([item(body=multibyte)])["status"], "rejected")
        context = {**request(), "expected_supplier_name": "x" * 2_049}
        self.assertEqual(self.evaluate([item(body=context)])["status"], "rejected")
        with patch("integrations.invoice_guard.batch.MAX_BATCH_XML_BYTES", 100):
            result = self.evaluate([item()])
            self.assertIn("BATCH_XML_BYTES_EXCEEDED", result["cases"][0]["reason_codes"])
        self.assertNoCalls()

    def test_malformed_xml_entities_unknown_root_and_deep_trees_stop_locally(self):
        documents = ["<broken>", '<!DOCTYPE x [<!ENTITY a "secret">]><x>&a;</x>',
                     "<NotAnInvoice/>", "<x>" * 97 + "</x>" * 97,
                     "<Invoice>\ud800</Invoice>"]
        for xml in documents:
            self.assertEqual(self.evaluate([item(body={**request(), "invoice_xml": xml})])["status"], "rejected")
        self.assertNoCalls()

    def test_identical_inputs_are_evaluated_once_and_all_copies_require_review(self):
        result = self.evaluate([item(), item("invoice-copy")])
        self.assertEqual(len(self.transport.prechecks), 1)
        self.assertEqual(len(self.transport.purchases), 1)
        for row in result["cases"]:
            self.assertEqual(row["action"], "manual_review")
            self.assertFalse(row["ready_for_buyer_approval"])
            self.assertIn("EXACT_INPUT_REPEATED_IN_BATCH", row["reason_codes"])
        self.assertFalse(result["cases"][1]["guard_called"])
        self.assertEqual(result["cases"][1]["evidence_reused_from"], "invoice-1")

    def test_canonical_amount_repetitions_do_not_purchase_twice(self):
        result = self.evaluate([item("a", request(amount="100")), item("b", request(amount="100.00"))])
        self.assertEqual(len(self.transport.purchases), 1)
        self.assertEqual(result["summary"]["manual_review"], 2)

    def test_changed_buyer_context_same_declared_invoice_stops_all_before_purchase(self):
        changed = {**request(), "trusted_payee_iban": "DE12500105170648489890"}
        result = self.evaluate([item(), item("changed-account", changed)], budget=0)
        self.assertEqual(result["summary"]["manual_review"], 2)
        self.assertEqual(result["budget"]["worst_case_usdc_atomic"], 0)
        for row in result["cases"]:
            self.assertEqual(row["reason_codes"], ["DECLARED_INVOICE_KEY_CONFLICT"])
        self.assertNoCalls()

    def test_same_declared_invoice_different_xml_is_review_not_duplicate_proof(self):
        result = self.evaluate([item(), item("changed-xml", request(extra="<cbc:Note>changed</cbc:Note>"))], budget=0)
        self.assertEqual(result["summary"]["manual_review"], 2)
        self.assertEqual(result["cases"][0]["duplicate_check"]["status"],
                         "same_declared_key_with_conflicting_input")
        self.assertNoCalls()

    def test_conflict_is_found_across_ubl_and_cii_and_normalized_vat(self):
        result = self.evaluate([item(), item("cii-copy", cii_request(vat="de 123-456.789"))], budget=0)
        self.assertEqual(result["summary"]["manual_review"], 2)
        self.assertNoCalls()

    def test_single_cii_case_is_supported_without_inventing_identity_verification(self):
        result = self.evaluate([item("cii", cii_request())])
        row = result["cases"][0]
        self.assertEqual(row["action"], "ready_for_buyer_approval")
        self.assertEqual(row["duplicate_check"]["status"], "no_matching_key_in_this_batch")
        self.assertEqual(len(self.transport.purchases), 1)

    def test_non_vat_registration_and_ambiguous_invoice_ids_are_not_business_keys(self):
        non_vat = request()
        non_vat["invoice_xml"] = non_vat["invoice_xml"].replace(
            "<cbc:ID>VAT</cbc:ID>", "<cbc:ID>OTHER</cbc:ID>")
        two_ids = request()
        two_ids["invoice_xml"] = two_ids["invoice_xml"].replace(
            "<cbc:ID>INV-001</cbc:ID>", "<cbc:ID>INV-001</cbc:ID><cbc:ID>INV-002</cbc:ID>")
        result = self.evaluate([item("registration", non_vat), item("two-ids", two_ids)],
                               2 * MAX_USDC_ATOMIC)
        for row in result["cases"]:
            self.assertEqual(row["duplicate_check"]["status"], "identity_not_assessable")

    def test_different_suppliers_same_number_are_not_conflated(self):
        result = self.evaluate([item(), item("other-supplier", request(vat="ATU12345678"))], 2 * MAX_USDC_ATOMIC)
        self.assertEqual(result["summary"]["ready_for_buyer_approval"], 2)
        self.assertEqual(len(self.transport.purchases), 2)

    def test_missing_or_ambiguous_vat_does_not_claim_business_duplicate_check(self):
        missing = request(vat="")
        ambiguous = request()
        ambiguous["invoice_xml"] = ambiguous["invoice_xml"].replace(
            "</cac:Party>", "<cac:PartyTaxScheme><cbc:CompanyID>ATU12345678</cbc:CompanyID>"
            "<cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:PartyTaxScheme></cac:Party>")
        result = self.evaluate([item("missing", missing), item("ambiguous", ambiguous)], 2 * MAX_USDC_ATOMIC)
        self.assertEqual(len(self.transport.purchases), 2)
        for row in result["cases"]:
            self.assertEqual(row["duplicate_check"]["status"], "identity_not_assessable")

    def test_existing_guard_cache_is_reused_across_batches(self):
        self.evaluate([item()])
        again = self.evaluate([item("second-batch")])
        self.assertEqual(len(self.transport.purchases), 1)
        self.assertTrue(again["cases"][0]["guard_reused"])
        self.assertIsNone(again["budget"]["actual_spend_usdc_atomic"])

    def test_uncertain_payment_is_never_retried_within_or_after_batch(self):
        self.transport.purchase_error = TimeoutError("secret XML and credentials must not leak")
        first = self.evaluate([item(), item("copy")])
        self.assertEqual(first["summary"]["stopped"], 2)
        self.evaluate([item("retry")])
        self.assertEqual(len(self.transport.purchases), 1)
        self.assertNotIn("credentials", json.dumps(first))

    def test_existing_guard_rejects_bad_signature_and_price(self):
        for edit in (lambda doc: {**doc, "receipt": {**doc["receipt"], "signature": "wrong"}},
                     lambda doc: sign({**doc, "pricing": {**doc["pricing"], "price_usdc": "2.00"}})):
            self.setUp()
            self.transport.delivery_edit = edit
            result = self.evaluate([item()])
            self.assertEqual(result["cases"][0]["action"], "stopped")
            self.assertEqual(result["cases"][0]["source_evidence"], {})

    def test_tool_or_transport_failure_does_not_become_approval(self):
        self.transport.precheck = lambda *args, **kwargs: Reply({"error": "offline"}, status_code=503)
        result = self.evaluate([item()])
        self.assertEqual(result["summary"]["stopped"], 1)
        self.assertEqual(self.transport.purchases, [])

    def test_valid_hold_and_do_not_pay_remain_non_approvals(self):
        for action, expected in (("HOLD_FOR_REVIEW", "manual_review"), ("DO_NOT_PAY", "stopped")):
            self.setUp()
            def edit(document):
                status = "review" if action == "HOLD_FOR_REVIEW" else "fail"
                document["checks"][0]["status"] = status
                document["evidence_coverage"]["passed"] -= 1
                document["evidence_coverage"]["review" if status == "review" else "failed"] += 1
                document["decision"].update(action=action, ready_for_buyer_approval=False)
                return sign(document)
            self.transport.delivery_edit = edit
            result = self.evaluate([item(), item("copy")])
            self.assertTrue(all(row["action"] == expected for row in result["cases"]))
            self.assertTrue(all(not row["ready_for_buyer_approval"] for row in result["cases"]))
            self.assertTrue(result["cases"][0]["source_evidence"])

    def test_guard_exception_and_malformed_outcome_are_sanitized(self):
        for returned in (None, {"action": "ready_for_buyer_approval"},
                         Outcome("ready_for_buyer_approval", "EVIDENCE_READY_NOT_PAYMENT_AUTHORITY"),
                         Outcome("stopped", "NO", ready_to_pay=True)):
            with patch.object(self.guard, "evaluate", return_value=returned):
                self.assertEqual(self.evaluate([item()])["cases"][0]["action"], "stopped")
        with patch.object(self.guard, "evaluate", side_effect=RuntimeError("private document secret")):
            result = self.evaluate([item()])
            self.assertEqual(result["cases"][0]["reason_codes"], ["GUARD_EVALUATION_FAILED"])
            self.assertNotIn("private document secret", json.dumps(result))

    def test_report_and_input_do_not_share_mutable_guard_data(self):
        original = item()
        before = copy.deepcopy(original)
        result = self.evaluate([original])
        self.assertEqual(original, before)
        text = json.dumps(result)
        self.assertNotIn(original["request"]["invoice_xml"], text)
        self.assertNotIn(original["request"]["trusted_payee_iban"], text)
        self.assertNotIn("signed_message", text)
        result["cases"][0]["source_evidence"]["checks"][0]["status"] = "fake"
        again = self.evaluate([original])
        self.assertEqual(again["cases"][0]["source_evidence"]["checks"][0]["status"], "pass")

    def test_guard_cannot_mutate_the_next_request_snapshot(self):
        second = item("second", request(number="INV-002"))
        original_precheck = self.transport.precheck
        def precheck(body, **kwargs):
            second["request"]["expected_amount"] = "999.00"
            return original_precheck(body, **kwargs)
        self.transport.precheck = precheck
        result = self.evaluate([item(), second], 2 * MAX_USDC_ATOMIC)
        self.assertEqual(result["summary"]["ready_for_buyer_approval"], 2)
        self.assertEqual(self.transport.purchases[1]["expected_amount"], "100.00")


if __name__ == "__main__":
    unittest.main()
