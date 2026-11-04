# Invoice Guard Pilot

## A scoped test of invoice evidence in your approval workflow

Invoice Guard helps an accounts-payable team or integration partner gather structured
evidence before its existing approval step. It does not replace the person authorized to
approve a supplier invoice, and it never executes that invoice payment.

**Pilot: EUR 149 net, one time, for 30 days, on request.**

The accepted workflow, documents, volume and start date must be agreed in writing before
an order. The pilot covers **one bounded workflow** within the 30-day window:

- A format and integration check for the agreed invoice source and review process.
- A synthetic example run showing the intended evidence and review route.
- A documented handover identifying the result, integration requirements and open points.

API-call costs are separate and agreed in advance. The offer is not an automatically
activated service or a subscription. There is no automatic renewal.

Optional integration setup starts **from EUR 499 net**, only under a separate scoped offer. It is
not added to the pilot automatically. The public self-service API remains **1.00 USDC per
completed invoice dossier**; submitting a pilot enquiry does not authorize a paid API call.

## Who the pilot fits

- An accounts-payable team already receives UBL/CII invoices or hybrid e-invoice PDFs.
- An ERP or automation integrator wants a machine-readable evidence step in an existing flow.
- The buyer can provide its own expected amount, currency, country and verified supplier
  master data, including the trusted payee IBAN.

If the workflow starts with ordinary PDF invoices or scans, this version is not the right
fit. OCR and a full invoice-processing platform are not included.

## What the existing service supplies

1. **Free preparation:** document-format, size and required-context checks before purchase.
   This is not a full compliance check or a promise of a positive result.
2. **One structured dossier:** EN 16931 findings, invoice arithmetic, VIES evidence,
   EU/UN/UK sanctions name-list candidates, VAT treatment and comparison with the trusted
   payee details supplied by the buyer.
3. **A review route:** `READY_FOR_BUYER_APPROVAL`, `HOLD_FOR_REVIEW` or `DO_NOT_PAY`, with
   reasons, evidence paths, source timestamps and a freshness limit.
4. **Portable verification:** signed result evidence that a configured verifier can check
   for integrity and issuer authenticity.

The service accepts **UBL/CII XML** or **ZUGFeRD/Factur-X PDFs with exactly one embedded
invoice XML**. It validates the embedded XML, not a scan of the visible PDF pages. The
optional [Python adapter](../integrations/invoice_guard/README.md) supports XML only and
requires the buyer's authorized transport and pinned receipt issuer.

## What it does not prove or do

- No ordinary/scanned-PDF parsing or OCR.
- No accounting-history duplicate detection or matching of every previous invoice.
- No independent verification that the supplier owns a bank account.
- No proof that goods were delivered, a purchase order was approved or a human authorized payment.
- No legal or tax clearance, fraud guarantee, certification or promised financial saving.
- No bank login, wallet seed, private key, transfer, order placement or invoice payment.

A sanctions name match can require identity review. A `READY_FOR_BUYER_APPROVAL` result
does not mean “safe to pay”; it only permits the next step in the buyer's own approval
process. Missing or stale evidence must not be treated as approval.

## Agree the evaluation before starting

We use the enquiry to agree a narrow, testable scope, for example one invoice source,
one buyer entity and one existing review workflow. Agree the sample selection and
success criteria first: which findings must be visible, how review cases are handled,
whether signed evidence verifies correctly, and the acceptable cost per completed check.
Do not presume that an invoice count, connector or operational support level is included.

For the first discussion, use synthetic or redacted examples and share only the information
needed to assess fit. **Do not attach real invoices or credentials to public GitHub issues.**
Handling of production documents, access and retention must be agreed before they are sent.

## Request a pilot

[Open the pilot scope and enquiry page](https://api.eucompliance.tools/invoice-guard-pilot).

Describe your invoice format, current approval system, intended monthly volume and the
problem you want to measure. An enquiry is not an order and does not trigger a charge.
There is no need to connect a bank account or provide wallet credentials.
