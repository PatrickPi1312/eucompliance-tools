"""Bounded, buyer-owned batch review around :class:`InvoiceGuard`.

``evaluate_batch(guard, [{"case_id": "invoice-001", "request": {...}}],
                 max_total_usdc_atomic=1_000_000)``

This module has no HTTP client, wallet, sender, file export or command-line entry
point. The injected guard still uses its operator-authorized transport and
private journal: calling it may buy a dossier and retain its signed evidence.
The batch ceiling is an ADDITIONAL limit, never spending authority. The transport
must enforce its own authorization, per-call cap and cross-batch total budget.
The 200-case limit is technical, not a purchased quota or subscription.

All cases are checked locally before the first guard call. Exact repeated inputs
share at most one call, but every repeated case is held for manual review. A
conservative UBL/CII extractor can also flag conflicting inputs with the same
declared supplier VAT ID and invoice number. It does not verify that identity,
detect every duplicate, consult a ledger or prove that an invoice is unpaid.
No result grants payment authority. Reports are returned in memory only, contain
selected evidence metadata rather than raw documents, and are not independently
signed receipts. Verify full receipts through the original guard at handoff.
"""
from __future__ import annotations

import copy
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict

from .adapter import (MAX_USDC_ATOMIC, MAX_XML_BYTES, GuardRejected, InvoiceGuard,
                      Outcome, bind_request)

CONTRACT = "eu-invoice-batch-review/1"
MAX_BATCH_ITEMS = 200
MAX_BATCH_XML_BYTES = 20_000_000
MAX_CONTEXT_CHARS = 2_048
_CASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}\Z")
_AMOUNT = re.compile(r"(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,2})?\Z")
_REASON = re.compile(r"[A-Z][A-Z0-9_]{0,95}\Z")
_UBL = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
_CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
_CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
_CII = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
_RAM = "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
_NS = {"cac": _CAC, "cbc": _CBC, "rsm": _CII, "ram": _RAM}


def _code(value: object, fallback: str = "GUARD_REJECTED") -> str:
    return value if type(value) is str and _REASON.fullmatch(value) else fallback


def _one_text(parent: ET.Element, path: str) -> str | None:
    nodes = parent.findall(path, _NS)
    if len(nodes) != 1 or list(nodes[0]):
        return None
    value = (nodes[0].text or "").strip()
    if not value or len(value) > 128 or any(ord(c) < 32 for c in value):
        return None
    return value


def _invoice_key(xml: str) -> tuple[str, str] | None:
    """Return a conservative declared VAT/number key, not verified identity.

    Do not resolve entities, recover malformed XML, infer supplier names, use
    arbitrary descendant IDs or equate unknown fields with an empty identity.
    Missing/ambiguous identifiers return None, not a 'no duplicates' verdict.
    """
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", xml, re.IGNORECASE):
        raise GuardRejected("XML_DECLARATIONS_REJECTED")
    try:
        root = ET.fromstring(xml.encode("utf-8"))
    except (ET.ParseError, ValueError, UnicodeError) as exc:
        raise GuardRejected("MALFORMED_XML") from exc
    stack, count = [(root, 1)], 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > 50_000 or depth > 96 or len(node.attrib) > 100:
            raise GuardRejected("XML_STRUCTURE_LIMIT_EXCEEDED")
        stack.extend((child, depth + 1) for child in node)

    vats: list[str] = []
    if root.tag == f"{{{_UBL}}}Invoice":
        invoice_id = _one_text(root, "cbc:ID")
        parties = root.findall("cac:AccountingSupplierParty/cac:Party", _NS)
        if len(parties) != 1:
            return None
        for tax in parties[0].findall("cac:PartyTaxScheme", _NS):
            if _one_text(tax, "cac:TaxScheme/cbc:ID") == "VAT":
                value = _one_text(tax, "cbc:CompanyID")
                if value is None:
                    return None
                vats.append(value)
    elif root.tag == f"{{{_CII}}}CrossIndustryInvoice":
        # Other CII document type codes are not inferred to be payable invoices.
        if _one_text(root, "rsm:ExchangedDocument/ram:TypeCode") != "380":
            return None
        invoice_id = _one_text(root, "rsm:ExchangedDocument/ram:ID")
        parties = root.findall(
            "rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeAgreement/"
            "ram:SellerTradeParty", _NS)
        if len(parties) != 1:
            return None
        for registration in parties[0].findall("ram:SpecifiedTaxRegistration", _NS):
            for identifier in registration.findall("ram:ID", _NS):
                if identifier.get("schemeID") == "VA":
                    value = _one_text(registration, "ram:ID")
                    if value is None:
                        return None
                    vats.append(value)
    else:
        raise GuardRejected("UNSUPPORTED_XML_ROOT")

    if not invoice_id or len(vats) != 1:
        return None
    vat = re.sub(r"[\s.\-]", "", vats[0]).upper()
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{2,16}", vat):
        return None
    return vat, invoice_id


def _prepare(request: object) -> tuple[dict, str, str, tuple[str, str] | None, int]:
    if type(request) is not dict or len(request) > 13:
        raise GuardRejected("INVALID_REQUEST")
    for key, value in request.items():
        if type(key) is not str:
            raise GuardRejected("INVALID_REQUEST")
        if key == "b2b":
            if type(value) is not bool:
                raise GuardRejected("INVALID_REQUEST")
        elif type(value) is not str:
            raise GuardRejected("INVALID_REQUEST")
        elif key != "invoice_xml" and len(value) > MAX_CONTEXT_CHARS:
            raise GuardRejected("CONTEXT_LIMIT_EXCEEDED")
    xml = request.get("invoice_xml")
    if type(xml) is not str or len(xml) > MAX_XML_BYTES:
        raise GuardRejected("XML_REQUIRED")
    raw = xml.encode("utf-8")
    if not raw or len(raw) > MAX_XML_BYTES:
        raise GuardRejected("XML_REQUIRED")
    # Bound decimal parsing/formatting, including giant scientific exponents,
    # before the existing request canonicalizer can allocate a formatted amount.
    amount = request.get("expected_amount")
    if type(amount) is not str or not _AMOUNT.fullmatch(amount):
        raise GuardRejected("INVALID_EXPECTED_AMOUNT")
    body, fingerprint, document_hash = bind_request(request)
    identity = _invoice_key(xml)
    return body, fingerprint, document_hash, identity, len(raw)


def _metadata(row: object, fields: tuple[str, ...]) -> dict:
    if not isinstance(row, dict):
        return {}
    return {key: value for key in fields
            if type(value := row.get(key)) is str and len(value) <= 2_048}


def _evidence(document: dict) -> dict:
    """A bounded projection; no request, raw XML, full IBAN or signed-message dump."""
    checks = document.get("checks", [])
    sources = document.get("source_snapshots", [])
    if (not isinstance(checks, list) or not isinstance(sources, list)
            or len(checks) > 256 or len(sources) > 64):
        raise GuardRejected("INVALID_EVIDENCE_PROJECTION")
    return {
        "kind": "guard_validated_dossier_projection",
        "checks": [_metadata(row, ("id", "status", "code", "source", "evidence_path"))
                   for row in checks],
        "sources": [_metadata(row, ("id", "status", "checked_at", "url", "standard",
                                    "list_generated")) for row in sources],
        "validity": _metadata(document.get("validity"), ("checked_at", "fresh_until")),
        "receipt_reference": _metadata(document.get("receipt"),
                                       ("standard", "issuer_key", "result_hash",
                                        "issued_at", "valid_until")),
        "standalone_verifiable_receipt": False,
    }


def _case(case_id: str | None, index: int) -> dict:
    return {
        "case_id": case_id, "input_index": index,
        "action": "stopped", "reason_codes": [],
        "ready_for_buyer_approval": False, "ready_to_pay": False,
        "input_fingerprint": None, "document_sha256": None,
        "guard_called": False, "guard_reused": False,
        "evidence_reused_from": None, "source_evidence": {},
        "duplicate_check": {"status": "not_assessed", "related_case_ids": []},
    }


def _report(cases: list[dict], *, errors: list[str], ceiling: int | None,
            potential: int = 0, called: int = 0) -> dict:
    counts = {action: sum(row["action"] == action for row in cases)
              for action in ("ready_for_buyer_approval", "manual_review", "stopped")}
    result = {
        "contract": CONTRACT,
        "status": ("rejected" if errors else "requires_review"
                   if counts["manual_review"] or counts["stopped"] else "completed"),
        "ready_to_pay": False, "errors": errors,
        "summary": {"cases": len(cases), **counts, "guard_evaluations": called},
        "budget": {
            "authorized_ceiling_usdc_atomic": ceiling,
            "per_evaluation_ceiling_usdc_atomic": MAX_USDC_ATOMIC,
            "worst_case_usdc_atomic": potential * MAX_USDC_ATOMIC,
            "attempted_evaluations_ceiling_usdc_atomic": called * MAX_USDC_ATOMIC,
            "actual_spend_usdc_atomic": None,
            "payment_authority_granted": False,
        },
        "limits": {"max_cases": MAX_BATCH_ITEMS, "max_total_xml_bytes": MAX_BATCH_XML_BYTES,
                   "sold_quota": False},
        "limitations": [
            "The batch ceiling is not payment authority; the injected buyer transport must enforce its own spending policy and cross-batch budget.",
            "A guard evaluation may reuse its private cache; attempted evaluations are not a claim of settled purchases or actual spend.",
            "Duplicate hints concern only this batch's supplied XML/context and declared supplier VAT/invoice number; no ledger duplicate or unpaid-status guarantee.",
            "No payment is approved or submitted by this batch report. Keep proof-of-delivery, identity review and internal approval controls.",
            "This module does not export files or make its own network calls. The injected guard retains its existing authorized transport and private-journal behavior.",
        ],
        "cases": cases,
    }
    # Guarantee that callers receive ordinary JSON values, without shared
    # references to input requests, mutable guard evidence or custom objects.
    return json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False))


def evaluate_batch(guard: InvoiceGuard, items: list[dict] | tuple[dict, ...], *,
                   max_total_usdc_atomic: int = 0) -> dict:
    """Review at most 200 named XML cases with explicit aggregate spend ceiling.

    ``items`` is a materialized list/tuple of exactly ``case_id`` and ``request``.
    All item shapes, XML safety bounds and request bindings must pass before ANY
    evaluation. A zero/default ceiling permits no potential paid calls. Each
    unique non-conflicting input reserves the full one-dossier ceiling even if
    the guard may reuse cached evidence. Insufficient ceiling rejects the whole
    batch before execution rather than partially buying an unreviewed batch.

    Exact repeated inputs are evaluated once and all repetitions require manual
    review; conflicting supplier-VAT/invoice-number groups are held before any
    purchase. Exceptions and invalid outcomes always stop, without exposing
    exception messages, bypassing guard validation or retrying an uncertain buy.
    """
    ceiling = max_total_usdc_atomic
    if type(ceiling) is not int or ceiling < 0 or ceiling > 2**63 - 1:
        return _report([], errors=["INVALID_BATCH_BUDGET"], ceiling=None)
    if not isinstance(guard, InvoiceGuard):
        return _report([], errors=["INVOICE_GUARD_REQUIRED"], ceiling=ceiling)
    if type(items) not in (list, tuple) or not 1 <= len(items) <= MAX_BATCH_ITEMS:
        return _report([], errors=["BATCH_LIMIT_OR_SHAPE_INVALID"], ceiling=ceiling)

    cases, prepared, names, errors, byte_count = [], [], set(), [], 0
    # Snapshot every request now; later caller/transport mutation cannot change
    # the batch plan, duplicate grouping or the budget calculation.
    for index, item in enumerate(items):
        case_id = item.get("case_id") if type(item) is dict else None
        valid_name = type(case_id) is str and bool(_CASE_ID.fullmatch(case_id))
        row = _case(case_id if valid_name else None, index)
        cases.append(row)
        try:
            if type(item) is not dict or set(item) != {"case_id", "request"} or not valid_name:
                raise GuardRejected("INVALID_CASE")
            if case_id in names:
                raise GuardRejected("DUPLICATE_CASE_ID")
            names.add(case_id)
            body, fingerprint, document_hash, identity, size = _prepare(item["request"])
            byte_count += size
            if byte_count > MAX_BATCH_XML_BYTES:
                raise GuardRejected("BATCH_XML_BYTES_EXCEEDED")
            row.update(input_fingerprint=fingerprint, document_sha256=document_hash)
            row["duplicate_check"]["status"] = (
                "declared_key_available" if identity else "identity_not_assessable")
            prepared.append((body, fingerprint, identity))
        except GuardRejected as exc:
            row["reason_codes"] = [_code(str(exc), "INVALID_CASE")]
            prepared.append(None)
            errors.append("BATCH_INPUT_REJECTED")
        except Exception:
            row["reason_codes"] = ["INVALID_CASE"]
            prepared.append(None)
            errors.append("BATCH_INPUT_REJECTED")
    if errors:
        for row in cases:
            if not row["reason_codes"]:
                row["reason_codes"] = ["BATCH_INPUT_REJECTED"]
        return _report(cases, errors=["BATCH_INPUT_REJECTED"], ceiling=ceiling)

    by_fingerprint, by_identity = defaultdict(list), defaultdict(list)
    for index, (_, fingerprint, identity) in enumerate(prepared):
        by_fingerprint[fingerprint].append(index)
        if identity is not None:
            by_identity[identity].append(index)
            cases[index]["duplicate_check"]["status"] = "no_matching_key_in_this_batch"
    conflicts = set()
    for indices in by_identity.values():
        if len({prepared[index][1] for index in indices}) > 1:
            conflicts.update(indices)
            for index in indices:
                cases[index].update(action="manual_review",
                                    reason_codes=["DECLARED_INVOICE_KEY_CONFLICT"])
                cases[index]["duplicate_check"] = {
                    "status": "same_declared_key_with_conflicting_input",
                    "related_case_ids": [cases[i]["case_id"] for i in indices if i != index],
                }
    candidates = [(fingerprint, indices) for fingerprint, indices in by_fingerprint.items()
                  if not any(index in conflicts for index in indices)]
    potential = len(candidates)
    if potential * MAX_USDC_ATOMIC > ceiling:
        reason = "BATCH_BUDGET_NOT_AUTHORIZED" if ceiling == 0 else "BATCH_BUDGET_EXCEEDED"
        for _, indices in candidates:
            for index in indices:
                cases[index]["reason_codes"] = [reason]
        return _report(cases, errors=[reason], ceiling=ceiling, potential=potential)

    called = 0
    for fingerprint, indices in candidates:
        first = indices[0]
        row, body = cases[first], prepared[first][0]
        try:
            # Re-bind immediately before each guard call. The private snapshot
            # must still match the preflighted request and budget/duplicate plan.
            current_body, current_fingerprint, current_hash, _, _ = _prepare(body)
            if current_fingerprint != fingerprint or current_hash != row["document_sha256"]:
                raise GuardRejected("BATCH_REQUEST_CHANGED")
            row["guard_called"] = True
            called += 1
            outcome = guard.evaluate(copy.deepcopy(current_body))
            if (not isinstance(outcome, Outcome) or outcome.ready_to_pay is not False
                    or outcome.fingerprint not in (None, fingerprint)
                    or type(outcome.reused) is not bool
                    or outcome.action not in {"ready_for_buyer_approval", "stopped"}):
                raise GuardRejected("INVALID_GUARD_OUTCOME")
            if outcome.action == "ready_for_buyer_approval" and (
                    not isinstance(outcome.dossier, dict) or outcome.fingerprint != fingerprint
                    or outcome.reason != "EVIDENCE_READY_NOT_PAYMENT_AUTHORITY"):
                raise GuardRejected("INVALID_GUARD_OUTCOME")
            row["guard_reused"] = outcome.reused
            row["reason_codes"] = [_code(outcome.reason)]
            if outcome.dossier is not None:
                row["source_evidence"] = _evidence(outcome.dossier)
            if outcome.action == "ready_for_buyer_approval":
                row.update(action="ready_for_buyer_approval", ready_for_buyer_approval=True)
            elif outcome.reason == "HOLD_FOR_REVIEW":
                row["action"] = "manual_review"
        except GuardRejected as exc:
            row.update(action="stopped", ready_for_buyer_approval=False,
                       source_evidence={}, reason_codes=[_code(str(exc))])
        except Exception:
            row.update(action="stopped", ready_for_buyer_approval=False,
                       source_evidence={}, reason_codes=["GUARD_EVALUATION_FAILED"])

        if len(indices) > 1:
            row["reason_codes"].append("EXACT_INPUT_REPEATED_IN_BATCH")
            if row["action"] != "stopped":
                row["action"] = "manual_review"
            row["ready_for_buyer_approval"] = False
            row["duplicate_check"] = {
                "status": "exact_input_repeated",
                "related_case_ids": [cases[i]["case_id"] for i in indices[1:]],
            }
            for index in indices[1:]:
                duplicate = cases[index]
                duplicate.update(
                    action=row["action"], reason_codes=list(row["reason_codes"]),
                    evidence_reused_from=row["case_id"],
                    source_evidence=copy.deepcopy(row["source_evidence"]),
                    duplicate_check={
                        "status": "exact_input_repeated",
                        "related_case_ids": [cases[i]["case_id"] for i in indices if i != index],
                    })
    return _report(cases, errors=[], ceiling=ceiling, potential=potential, called=called)
