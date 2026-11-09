# Free, synthetic invoice preparation

Try the actual input contract before configuring a wallet, payment transport or pilot.
Python 3.10+ and its standard library are sufficient; no package installation is needed.
Run from a reviewed checkout of this repository:

```sh
python3 examples/invoice_precheck.py
python3 examples/invoice_precheck.py --missing-trusted-payee
```

Each invocation performs **exactly one unauthenticated POST** to the fixed free URL:
`https://api.eucompliance.tools/free/invoice-to-pay/check`.
It sends only the fictional XML and buyer context embedded in the script, never local
documents, environment credentials, wallet keys or arbitrary user-supplied input.
The public checksum-example IBAN is not a verified supplier account. The VAT ID is
deliberately fictional. **Do not pay this example or treat it as a conformant invoice.**

## What you should see

Default preparation:

```json
{
  "example": "synthetic_only",
  "chargeable": false,
  "eligible_for_paid_dossier": true,
  "issue_codes": [],
  "invoice_approved": false,
  "payment_authorized": false,
  "paid_calls": 0
}
```

With `--missing-trusted-payee`, `eligible_for_paid_dossier` becomes `false`, and
`issue_codes` contains `TRUSTED_PAYEE_IBAN_REQUIRED`. This demonstrates why an agent
must obtain trusted payee context from the buyer, not blindly copy the invoice's account.
Every other approval/payment flag remains false. The script also prints the contract
identifier and a preparation-only reminder.

These two outcomes were checked against the live **free** endpoint on 9 November 2026.
They are not paid end-to-end tests, a customer case, or revenue. Availability and existing
free quotas still apply: HTTP 402, 429, other errors and timeouts stop without a retry.

## Deliberate limits

- Preparation parses supported invoice XML and checks required context. It does not run
  full EN 16931 rules, VIES, sanctions screening or the paid dossier's decision policy.
  A parseable fictional invoice can therefore be eligible while failing those later checks.
- Eligibility is not legal clearance, invoice authenticity, bank-account ownership,
  delivery evidence, internal approval or authority to pay.
- The example rejects redirects, changed response URLs, oversized/non-JSON responses,
  duplicate JSON keys, unexpected contract values, conflicting outcomes and mismatched
  input fingerprints. It ignores returned prose and purchase instructions.
- No raw XML, full bank account, remote prose or payment link is printed. The script
  has no file/credential input, signer, wallet, paid request or auto-retry path.
- The HTTP timeout is a socket-operation timeout, not a hard total wall-clock deadline.
  This is a bounded demonstration, not a production HTTP transport or security certification.

## The same free step through MCP

Use `prepare_invoice_to_pay_eu` with the fields from `synthetic_request()` in the script.
Send **one** document field, `invoice_xml`; omit `document_base64` entirely. The free
result uses `eligible_for_paid_dossier`, not a payment-approval flag. An ordinary MCP
client can use the free tool but does not automatically pay for other tools.

For production, the [XML/batch buyer adapter](../integrations/invoice_guard/README.md)
requires the buyer's authorized transport, exact per-call and total budgets, trusted
receipt issuer, private journal and result verification. A completed dossier costs
1.00 USDC; nothing in this example authorizes that purchase. The optional
[149-EUR pilot](../docs/invoice-guard-pilot.md) is separate and is not required.

## Offline regression tests

```sh
python3 -m unittest examples.test_invoice_precheck examples.test_pay_and_call -v
```

All network openers are mocked. Cases cover both outcomes, changed/paid URL refusal,
redirect refusal, timeout/error handling, size/JSON limits, contract and fingerprint
binding, conflicting states, and suppressing arbitrary remote prose. They perform
no live calls or payments.

`pay_and_call.py` is a historical filename for a separate **free discovery-only**
example. It does not pay either.
