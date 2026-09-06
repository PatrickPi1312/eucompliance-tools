# Put invoice evidence before your agent's approval step

An accounts-payable or procurement operator can install this gate before its
existing invoice-approval workflow. It buys the existing **EU Invoice Payment
Guard for exactly 1.00 USDC per completed dossier**, using the operator's
authorized MCP/x402 transport. No subscription or price change is introduced.

The dossier combines official EN 16931 validation, invoice arithmetic, live VIES,
EU/UN/UK name-list screening, VAT treatment and comparison to the buyer's trusted
payee IBAN. This integration makes the operator's chosen evidence check a required
step in that workflow. It is not a legal requirement to buy this service, and it
does not make other agents or platforms use it.

## What is included

- A dependency-free Python adapter and a durable SQLite claim/cache.
- A separate optional EIP-191 verification helper using `eth-account`.
- An integration-wiring example and offline regression tests.

There is **no wallet, private key, signer, payment sender or HTTP client** here.
The transport is supplied by the buyer; no purchase occurs on import or by running
the example module. A working buyer transport and explicit spending authority
are prerequisites, not capabilities manufactured by this adapter.

## Wire it into your workflow

Copy this `invoice_guard` package into your integration. Python 3.10+ is required.
Install `eth-account` in your buyer environment if using `verify_eip191.py`, or
inject your already audited verifier. Pin the approved receipt issuer during
operator setup; never take the trusted issuer from the current receipt.
The official discovery URL is `https://api.eucompliance.tools/verify`; validate
and pin its issuer via your trusted setup process rather than refetching it for
each purchase.

Implement the two methods in `adapter.Transport` with your existing transport:

| Method | HTTP | MCP |
| --- | --- | --- |
| `precheck` | `POST /free/invoice-to-pay/check` | `prepare_invoice_to_pay_eu` |
| `purchase` | `POST /x402/invoice-to-pay` | `invoice_to_pay_dossier_eu` |

HTTP requests use `https://api.eucompliance.tools`; the MCP endpoint is
`https://mcp.eucompliance.tools/mcp`.
Return `Reply(payload, status_code, is_error)`. For MCP, `payload` must be the
actual `structuredContent`, with `is_error` copied from `isError`; a 402 challenge
or a text error must never be turned into a dossier. HTTP transports must retain
the real status code. Apply the supplied timeout to the entire operation.

The paid transport must enforce `max_usdc_atomic=1_000_000` **before** signing or
authorizing payment, for known six-decimal USDC only. It must also bind the exact
resource, HTTP method, body, expected recipient and authorized network. The
adapter checks the free quoted price before calling the transport, but it cannot
control a dishonest or incorrectly implemented transport. One normal x402
challenge/authorized retry is one purchase; do not create additional purchases
on ambiguous delivery. Do not automatically buy this guard again to authorize
payment for the guard itself.

```python
from pathlib import Path
from invoice_guard.example import build_invoice_guard, route_before_buyer_approval

# All three values come from your existing operator setup.
guard = build_invoice_guard(
    authorized_transport=your_authorized_transport,
    trusted_issuer=your_pinned_receipt_issuer,
    journal_path=Path(your_private_persistent_directory) / "invoice-guard.sqlite3",
)
decision = route_before_buyer_approval(guard, {
    "invoice_xml": your_ubl_or_cii_xml,
    "buyer_country": "AT",
    "expected_amount": "100.00",
    "expected_currency": "EUR",
    "trusted_payee_iban": iban_from_your_verified_supplier_master_data,
})
# ready_for_buyer_approval only enters your EXISTING approval stage.
# Review/blocked/unavailable stays on hold. No transfer is submitted here.
```

Use `InvoiceGuard.evaluate()` for direct integration. Only the
`ready_for_buyer_approval` action advances to approval. `ready_to_pay` is always
false. Receipt authenticity, input binding and freshness are all checked; a
signature does not establish legal correctness or account ownership. An injected
verifier must return the literal boolean `True` only after cryptographically
verifying the complete v2 receipt envelope against a trusted issuer. A hash-only
check or `lambda _: True` is not a production verifier.

## Reuse and failure handling

The adapter recomputes the current `eu-invoice-to-pay/1` fingerprint from the exact
XML bytes and normalized buyer context. The free precheck and paid signed dossier
must both match. Changed document, payee, amount or supplier context cannot reuse
the previous dossier. Expected amounts are canonicalized to two decimal places
in the request body before precheck, purchase and hashing: `100`, `100.0` and
`100.00` share one durable claim for the same exact XML/context. XML whitespace,
attribute order and other XML-byte changes are not semantically deduplicated.
This version accepts `invoice_xml` only, up to 1 MB, and decimal-string expected
amounts with at most two decimal places. It does not
extract XML from PDFs. The underlying service also supports embedded XML in
ZUGFeRD/Factur-X, but this adapter intentionally does not claim that capability.

The journal atomically records an attempt **before** the purchase starts. Fresh,
valid identical dossiers are reused across process restarts and concurrent
workers; review/deny responses are also reused without another charge. A timeout,
invalid delivery, tool error or crash after the claim remains blocked for
reconciliation. Even an explicit unpaid error is not automatically retried.
An expired cached dossier stops; this adapter never repurchases it automatically.
There is deliberately no reset endpoint that could hide uncertain payment state.

Use one persistent local journal for all workers of the same buyer workflow,
owned only by that service account. Its parent directory should be private; on
POSIX the database must be mode `0600` (new files are created that way). Do not use
`:memory:`, a temporary path, a separate journal per request or network filesystems
in production. Multi-host orchestration needs a shared transactional claim store
instead of this SQLite implementation. The journal contains signed supplier and
invoice metadata, but not the raw XML or original full request; apply your own
access and retention controls. Lost/deleted journals remove duplicate protection.

The operator must still enforce this step in its actual orchestration and keep
the same invoice/context through the approval stage. This library cannot prevent
a separate tool from bypassing the workflow. Verify again at approval handoff if
time or invoice data changed. Proof of delivery, internal authority,
duplicate-ledger checks and independent bank-account ownership remain the buyer's
responsibility. A name-list match may require identity review. No agent should
reinterpret HOLD or DO_NOT_PAY as approval.

## Offline verification

From the repository root:

```sh
python3 -m unittest integrations.invoice_guard.test_adapter \
  integrations.invoice_guard.test_crypto_contract -v
```

The core tests use synthetic fixtures and an explicitly test-only HMAC verifier to
exercise tampering, wrong issuer, freshness, price caps, request binding,
denied/unpaid responses, concurrent claims and restart reuse. Optional tests use
real EIP-191 signing/recovery with `eth-account==0.13.7` (the seller's pinned
version) and ephemeral, memory-only test accounts. They check envelope/content
tampering and issuer pinning, then run the complete adapter with the real verifier.
These four crypto tests skip explicitly if the optional package is unavailable.

Two repository-only compatibility tests compare the adapter with the seller's
pure context/fingerprint and receipt-canonicalization functions and its local
XML fact parser, using the bundled official UBL example. They do not load the
server application, real receipt key or paid handler. All 29 tests passed locally
with the pinned crypto dependency. The tests perform no network/backend calls or
payments; this is not a live x402 purchase or compliance certification.
