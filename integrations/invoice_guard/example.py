"""Integration wiring, not an autonomous purchase executable.

Run nothing on import. The operator provides an existing authorized HTTP/MCP
transport, an independently trusted issuer and a private persistent journal path.
"""
from pathlib import Path

from .adapter import InvoiceGuard, SQLiteJournal, Transport
from .verify_eip191 import make_verifier


def build_invoice_guard(*, authorized_transport: Transport, trusted_issuer: str,
                        journal_path: Path) -> InvoiceGuard:
    verifier = make_verifier(trusted_issuer=trusted_issuer)
    return InvoiceGuard(transport=authorized_transport, verifier=verifier,
                        journal=SQLiteJournal(journal_path))


def route_before_buyer_approval(guard: InvoiceGuard, invoice_request: dict) -> dict:
    outcome = guard.evaluate(invoice_request)
    return {
        "next_stage": ("existing_buyer_approval_workflow"
                       if outcome.action == "ready_for_buyer_approval" else "hold"),
        "reason": outcome.reason,
        "input_fingerprint": outcome.fingerprint,
        "reused": outcome.reused,
        "ready_to_pay": False,
        "signed_dossier": outcome.dossier,
    }
