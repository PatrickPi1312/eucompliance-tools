"""Opt-in invoice evidence gate. No network client, wallet, or payment sender.

The operator supplies an authorized transport and a trusted signature verifier.
READY only permits handing the invoice to the operator's approval workflow.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Protocol

CONTRACT = "eu-invoice-to-pay/1"
PRECHECK_CONTRACT = "eu-invoice-to-pay-input-check/1"
PAID_URL = "https://api.eucompliance.tools/x402/invoice-to-pay"
MAX_USDC_ATOMIC = 1_000_000  # Exactly 1 USDC, six decimal places.
MAX_XML_BYTES = 1_000_000  # This adapter deliberately accepts small XML only.
_REQUIRED = {"invoice_xml", "buyer_country", "expected_amount",
             "expected_currency", "trusted_payee_iban"}
_OPTIONAL = {"filename", "expected_supplier_vat", "expected_supplier_name",
             "expected_buyer_vat", "purchase_order_reference", "supply_type", "b2b"}
_CORE_CHECKS = {"en16931_conformance", "document_type", "amount_consistency",
                "currency_consistency", "buyer_country", "supplier_vies",
                "sanctions", "payment_account", "vat_treatment"}


class GuardRejected(ValueError):
    """Stable local reason code; never contains invoice or transport secrets."""


@dataclass(frozen=True)
class Reply:
    """Normalized HTTP/MCP reply; payload is the actual structured JSON object.

    HTTP: status_code is the actual HTTP status. MCP: set is_error from isError
    and extract structuredContent without manufacturing a success from text.
    Transport exceptions (including timeouts) may be propagated to the guard.
    """

    payload: dict
    status_code: int = 200
    is_error: bool = False


class Transport(Protocol):
    def precheck(self, request: dict, *, timeout_seconds: float) -> Reply:
        """Only /free/invoice-to-pay/check or prepare_invoice_to_pay_eu; no spend."""
        ...

    def purchase(self, request: dict, *, max_usdc_atomic: int,
                 timeout_seconds: float) -> Reply:
        """One authorized dossier purchase, same request, enforced USDC cap.

        The operator's existing transport must verify the exact resource, network,
        recipient and asset, enforce the cap BEFORE authorization, and never
        create another purchase after an ambiguous timeout. This protocol does
        not implement or grant that authority.
        """
        ...


@dataclass(frozen=True)
class Outcome:
    action: str
    reason: str
    fingerprint: str | None = None
    reused: bool = False
    dossier: dict | None = None
    ready_to_pay: bool = False


def _json_copy(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _timestamp(value) -> datetime:
    if not isinstance(value, str):
        raise GuardRejected("INVALID_TIMESTAMP")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GuardRejected("INVALID_TIMESTAMP") from exc
    if result.tzinfo is None:
        raise GuardRejected("INVALID_TIMESTAMP")
    return result


def _money(value) -> Decimal:
    if not isinstance(value, str):
        raise GuardRejected("INVALID_PRICE_OR_AMOUNT")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise GuardRejected("INVALID_PRICE_OR_AMOUNT") from exc
    if not number.is_finite():
        raise GuardRejected("INVALID_PRICE_OR_AMOUNT")
    return number


def bind_request(request: dict) -> tuple[dict, str, str]:
    """Match invoice-to-pay/1 normalization, without importing seller modules.

    Restrict to XML so the exact server document hash is reproducible locally.
    No PDF extraction, document parsing, or paid compliance decision occurs here.
    """
    if not isinstance(request, dict) or not _REQUIRED <= request.keys():
        raise GuardRejected("INVALID_REQUEST")
    if request.keys() - _REQUIRED - _OPTIONAL:
        raise GuardRejected("UNSUPPORTED_REQUEST_FIELD")
    for key, value in request.items():
        if key == "b2b":
            if type(value) is not bool:
                raise GuardRejected("INVALID_REQUEST")
        elif not isinstance(value, str):
            raise GuardRejected("INVALID_REQUEST")
    body = _json_copy(request)
    raw = body["invoice_xml"].encode("utf-8")
    if not raw or len(raw) > MAX_XML_BYTES or not raw.lstrip().startswith(b"<"):
        raise GuardRejected("XML_REQUIRED")
    amount = _money(body["expected_amount"])
    if amount <= 0 or amount.as_tuple().exponent < -2:
        raise GuardRejected("INVALID_EXPECTED_AMOUNT")
    # Normalize the transmitted body too: the seller hashes this exact string.
    # 100, 100.0 and 100.00 must not buy the same byte-identical invoice twice.
    body["expected_amount"] = format(amount, ".2f")
    if not re.fullmatch(r"[A-Z]{2}", body["buyer_country"].upper().strip()):
        raise GuardRejected("INVALID_BUYER_COUNTRY")
    if not re.fullmatch(r"[A-Z]{3}", body["expected_currency"].upper().strip()):
        raise GuardRejected("INVALID_CURRENCY")
    iban = re.sub(r"\s+", "", body["trusted_payee_iban"].upper())
    if not iban:
        raise GuardRejected("TRUSTED_PAYEE_REQUIRED")
    vat = lambda key: re.sub(r"[\s.\-]", "", body.get(key, "").upper())
    context = {
        "buyer_country": body["buyer_country"].upper().strip(),
        "expected_amount": body["expected_amount"],
        "expected_currency": body["expected_currency"].upper().strip(),
        "trusted_payee_iban": iban,
        "expected_supplier_vat": vat("expected_supplier_vat"),
        "expected_supplier_name": body.get("expected_supplier_name", "").strip(),
        "expected_buyer_vat": vat("expected_buyer_vat"),
        "purchase_order_reference": body.get("purchase_order_reference", "").strip(),
        "supply_type": (body.get("supply_type") or "service").lower().strip(),
        "b2b": body.get("b2b", True),
    }
    if context["supply_type"] not in {"goods", "service", "digital"}:
        raise GuardRejected("INVALID_SUPPLY_TYPE")
    document_hash = hashlib.sha256(raw).hexdigest()
    material = {"document_sha256": document_hash, "context": context,
                "contract": CONTRACT}
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    return body, "sha256:" + hashlib.sha256(encoded).hexdigest(), document_hash


def result_hash(document: dict) -> str:
    stripped = {key: value for key, value in document.items() if key != "receipt"}
    extensions = stripped.get("extensions")
    if isinstance(extensions, dict) and "delivery-receipt" in extensions:
        remaining = {key: value for key, value in extensions.items()
                     if key != "delivery-receipt"}
        if remaining:
            stripped["extensions"] = remaining
        else:
            stripped.pop("extensions", None)
    canonical = json.dumps(stripped, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, allow_nan=False)
    return "0x" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def receipt_message(receipt: dict) -> str:
    envelope = {key: value for key, value in receipt.items()
                if key not in {"signature", "signed_message"}}
    return "eucompliance.tools compliance receipt v2\n" + json.dumps(
        envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False)


class SQLiteJournal:
    """Durable, single-host claim/cache. An attempted purchase is never retried.

    Use one persistent database for all workers of the same buyer workflow.
    SQLite atomic claims also prevent duplicate purchases by separate processes.
    Supplier data in signed dossiers is personal/business data: protect the path.
    Raw invoice XML and full request bodies are never stored.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path).absolute()
        if self.path.is_symlink():
            raise ValueError("journal must not be a symlink")
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        if not self.path.is_file():
            raise ValueError("journal must be a regular file")
        if os.name == "posix" and self.path.stat().st_mode & 0o077:
            raise ValueError("journal must have owner-only file permissions")
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS invoice_guard "
                       "(fingerprint TEXT PRIMARY KEY, state TEXT NOT NULL, dossier TEXT)")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, fingerprint: str):
        with self._connect() as db:
            row = db.execute("SELECT state, dossier FROM invoice_guard WHERE fingerprint=?",
                             (fingerprint,)).fetchone()
        return (row[0], json.loads(row[1]) if row[1] else None) if row else None

    def claim(self, fingerprint: str) -> bool:
        with self._connect() as db:
            cursor = db.execute("INSERT OR IGNORE INTO invoice_guard VALUES (?, 'attempted', NULL)",
                                (fingerprint,))
            return cursor.rowcount == 1

    def store_delivery(self, fingerprint: str, document: dict):
        encoded = json.dumps(document, ensure_ascii=False, allow_nan=False)
        with self._connect() as db:
            db.execute("UPDATE invoice_guard SET state='delivered', dossier=? "
                       "WHERE fingerprint=? AND state='attempted'", (encoded, fingerprint))


class InvoiceGuard:
    def __init__(self, *, transport: Transport, verifier: Callable[[dict], bool],
                 journal: SQLiteJournal, timeout_seconds: float = 30,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        if not callable(verifier) or not 0 < timeout_seconds <= 120:
            raise ValueError("explicit verifier and timeout between 0 and 120 seconds required")
        self.transport, self.verifier, self.journal = transport, verifier, journal
        self.timeout_seconds, self.clock = timeout_seconds, clock

    @staticmethod
    def _payload(reply: Reply) -> dict:
        if (not isinstance(reply, Reply) or type(reply.is_error) is not bool
                or reply.is_error or reply.status_code != 200
                or not isinstance(reply.payload, dict) or "error" in reply.payload):
            raise GuardRejected("TOOL_OR_TRANSPORT_ERROR")
        return _json_copy(reply.payload)

    def _validate(self, document: dict, body: dict, fingerprint: str,
                  document_hash: str) -> str:
        if not isinstance(document, dict) or "error" in document:
            raise GuardRejected("INVALID_DOSSIER")
        receipt = document.get("receipt")
        if (not isinstance(receipt, dict) or receipt.get("standard") != "eucompliance-receipt/2"
                or receipt.get("service") != "invoice_to_pay_dossier"
                or not receipt.get("signature") or not receipt.get("issuer_key")):
            raise GuardRejected("INVALID_RECEIPT")
        extension = document.get("extensions", {})
        if not isinstance(extension, dict):
            raise GuardRejected("INVALID_RECEIPT")
        if "delivery-receipt" in extension and extension["delivery-receipt"] != receipt:
            raise GuardRejected("RECEIPT_MISMATCH")
        if (result_hash(document) != receipt.get("result_hash")
                or receipt.get("signed_message") != receipt_message(receipt)):
            raise GuardRejected("INTEGRITY_FAILED")
        # A hash alone proves no issuer. The trusted verifier must authenticate
        # the complete EIP-191 envelope and pin the operator-approved issuer.
        if self.verifier(copy.deepcopy(document)) is not True:
            raise GuardRejected("SIGNATURE_OR_ISSUER_INVALID")
        if (document.get("contract") != CONTRACT
                or document.get("policy_version") != "invoice-payment-policy/1"
                or document.get("input_fingerprint") != fingerprint
                or document.get("invoice", {}).get("document_sha256") != document_hash):
            raise GuardRejected("REQUEST_MISMATCH")
        validity = document.get("validity", {})
        checked, fresh = _timestamp(validity.get("checked_at")), _timestamp(validity.get("fresh_until"))
        issued, expires = _timestamp(receipt.get("issued_at")), _timestamp(receipt.get("valid_until"))
        now = self.clock()
        if now.tzinfo is None:
            raise GuardRejected("INVALID_CLOCK")
        if not checked <= issued <= now < fresh or expires != fresh or fresh - checked > timedelta(hours=24):
            raise GuardRejected("STALE_OR_INVALID_FRESHNESS")
        pricing = document.get("pricing", {})
        if (_money(pricing.get("price_usdc")) != Decimal("1.00")
                or pricing.get("additional_charges") is not False
                or pricing.get("paid_follow_ups_required") is not False):
            raise GuardRejected("PRICE_CONTRACT_MISMATCH")
        decision = document.get("decision", {})
        action = decision.get("action")
        if (action not in {"READY_FOR_BUYER_APPROVAL", "HOLD_FOR_REVIEW", "DO_NOT_PAY"}
                or decision.get("ready_to_pay") is not False
                or decision.get("ready_for_buyer_approval") is not (action == "READY_FOR_BUYER_APPROVAL")):
            raise GuardRejected("UNKNOWN_OR_INVALID_DECISION")
        coverage, checks = document.get("evidence_coverage", {}), document.get("checks")
        if not isinstance(checks, list) or not all(isinstance(item, dict) for item in checks):
            raise GuardRejected("INCOMPLETE_EVIDENCE")
        required = set(_CORE_CHECKS)
        # VIES does not publish names in every member state. The seller accepts
        # a matching buyer-held supplier name instead; require that check below.
        if not body.get("expected_supplier_name", "").strip():
            required.add("supplier_name_vies")
        for field, check in (("expected_buyer_vat", "buyer_vat_expected"),
                             ("expected_supplier_vat", "supplier_vat_expected"),
                             ("expected_supplier_name", "supplier_name_expected"),
                             ("purchase_order_reference", "purchase_order_reference")):
            if body.get(field, "").strip():
                required.add(check)
        check_ids = [item.get("id") for item in checks]
        counts = {status: sum(item.get("status") == status for item in checks)
                  for status in ("pass", "fail", "review")}
        if (not all(type(coverage.get(key)) is int for key in
                    ("required", "passed", "failed", "review", "unknown"))
                or len(set(check_ids)) != len(checks) or not required <= set(check_ids)
                or sum(counts.values()) != len(checks) or coverage.get("unknown") != 0
                or coverage.get("required") != len(checks)
                or coverage.get("passed") != counts["pass"]
                or coverage.get("failed") != counts["fail"]
                or coverage.get("review") != counts["review"]):
            raise GuardRejected("INCOMPLETE_EVIDENCE")
        if action == "READY_FOR_BUYER_APPROVAL":
            payment = document.get("payment", {})
            iban = re.sub(r"\s+", "", body["trusted_payee_iban"].upper())
            invoice = document["invoice"]
            if (counts["fail"] or counts["review"] or decision.get("blocking_reasons") != []
                    or decision.get("review_reasons") != []
                    or payment.get("matches_buyer_trusted_iban") is not True
                    or payment.get("structurally_valid") is not True
                    or payment.get("trusted_payee_account", {}).get("sha256") != hashlib.sha256(iban.encode()).hexdigest()
                    or _money(invoice.get("amount_due")) != _money(body["expected_amount"])
                    or invoice.get("currency") != body["expected_currency"].upper().strip()):
                raise GuardRejected("INCONSISTENT_READY_DECISION")
        return action

    def evaluate(self, request: dict) -> Outcome:
        """Return evidence readiness; never authorize or submit a payment.

        A fresh identical dossier is reused. A prior attempt, stale dossier, or
        invalid cached response stops without a new charge. Resolve uncertain
        payment state externally; this class deliberately has no reset/retry API.
        """
        fingerprint = None
        reused = False
        try:
            body, fingerprint, document_hash = bind_request(request)
            cached = self.journal.get(fingerprint)
            if cached:
                if cached[0] != "delivered":
                    raise GuardRejected("PRIOR_PURCHASE_REQUIRES_RECONCILIATION")
                reused = True
                document = cached[1]
            else:
                prepared = self._payload(self.transport.precheck(
                    copy.deepcopy(body), timeout_seconds=self.timeout_seconds))
                purchase = prepared.get("purchase", {})
                if (prepared.get("contract") != PRECHECK_CONTRACT
                        or prepared.get("chargeable") is not False
                        or prepared.get("eligible_for_paid_dossier") is not True
                        or prepared.get("input_fingerprint") != fingerprint
                        or prepared.get("issues") != []):
                    raise GuardRejected("FREE_PRECHECK_REJECTED")
                if (not isinstance(purchase, dict) or purchase.get("method") != "POST"
                        or purchase.get("url") != PAID_URL
                        or purchase.get("price") != "1.00 USDC"
                        or purchase.get("requires_same_input_fingerprint") is not True
                        or purchase.get("no_settlement_on_4xx_or_5xx") is not True):
                    raise GuardRejected("PRICE_OR_RESOURCE_MISMATCH")
                if not self.journal.claim(fingerprint):
                    raise GuardRejected("PRIOR_PURCHASE_REQUIRES_RECONCILIATION")
                document = self._payload(self.transport.purchase(
                    copy.deepcopy(body), max_usdc_atomic=MAX_USDC_ATOMIC,
                    timeout_seconds=self.timeout_seconds))
            action = self._validate(document, body, fingerprint, document_hash)
            if not reused:
                self.journal.store_delivery(fingerprint, document)
            if action != "READY_FOR_BUYER_APPROVAL":
                return Outcome("stopped", action, fingerprint, reused, copy.deepcopy(document))
            return Outcome("ready_for_buyer_approval", "EVIDENCE_READY_NOT_PAYMENT_AUTHORITY",
                           fingerprint, reused, copy.deepcopy(document))
        except GuardRejected as exc:
            return Outcome("stopped", str(exc), fingerprint, reused)
        except Exception:
            # Transport, verifier, malformed data and journal failures all stop.
            # Exception text can contain credentials/documents and is not exposed.
            return Outcome("stopped", "DEPENDENCY_OR_MALFORMED_RESPONSE", fingerprint, reused)
