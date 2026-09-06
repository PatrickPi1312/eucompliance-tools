# Agent Decision & Evidence Tools over MCP and x402

**40 machine-callable tools, including 10 free preparation, routing and verification tools.**
Pay only for a successfully delivered result in USDC through x402. No account, API key,
subscription or minimum purchase is required.

- Live catalog: [eucompliance.tools](https://eucompliance.tools/)
- MCP endpoint: `https://mcp.eucompliance.tools/mcp`
- Exact machine-readable catalog: [agents.json](https://api.eucompliance.tools/agents.json)
- Agent guide: [llms.txt](https://api.eucompliance.tools/llms.txt)
- x402 discovery: [.well-known/x402](https://api.eucompliance.tools/.well-known/x402)

The service is built for agents that need a bounded decision or a cited piece of evidence,
not an unstructured report. Paid successes return machine-readable structured content and a
signed receipt. Invalid input, unavailable required evidence and tool errors are not settled.

## Add the MCP server

Claude Code:

```bash
claude mcp add --transport http eucompliance https://mcp.eucompliance.tools/mcp
```

Claude Desktop, Cursor or another streamable-HTTP MCP client:

```json
{
  "mcpServers": {
    "eucompliance": {
      "type": "http",
      "url": "https://mcp.eucompliance.tools/mcp"
    }
  }
}
```

If the right product is unclear, call the free `recommend_agent_tool` tool with the job,
available inputs and optional price cap. It returns one to three exact catalog matches and a
safe workflow without calling a model, making a payment or executing the selected tool.

## Start from the job, not the catalog

| Agent job | Tool | Result | Price |
|---|---|---|---:|
| Read live state on one or many EVM chains | `rpc_read_batch` | 1–20 allowlisted read-only JSON-RPC calls across 28 mainnets | $0.01 |
| Decide whether an on-chain transaction is safe to submit | `tx_preflight` | `proceed`, `review` or `block`, with five core checks and optional token evidence | $0.02 |
| Turn live market evidence into a time-bounded action | `market_decision_pack` | `ENTER_RISK`, `WAIT` or `REDUCE_RISK`, reasons, risks and 15-minute expiry | $0.05 |
| Check an EU counterparty before onboarding or payment | `check_counterparty_eu` | VAT, EU/UN/UK sanctions and IBAN consistency → `clear`, `review` or `blocked` | $0.02 |
| Decide whether an EU supplier invoice is ready for approval | `invoice_to_pay_dossier_eu` | EN 16931, VIES, sanctions, VAT and trusted-payee evidence → READY, HOLD or DO NOT PAY | $1.00 |
| Check ordinary DE/AT goods before requesting a US carrier quote | `us_import_readiness_guard` | USITC/CPSC-backed readiness decision, unknowns and safe next actions | $0.25 |
| Validate an EU e-invoice | `validate_einvoice_eu` | Official EN 16931 findings with rule codes and severity | $0.10 |
| Run fixed-price short inference | `agentllm_micro`, `agentllm_basic`, `agentllm_plus` | OpenAI-compatible completion plus disclosed upstream usage and signed delivery receipt | $0.003 / $0.005 / $0.02 |

The complete 40-tool list, exact input and output schemas, current prices and transport-specific
HTTP offers are always published in [agents.json](https://api.eucompliance.tools/agents.json).

## Read-only chain evidence

`rpc_read_batch` is the broad entry point: one default chain plus optional per-request chain
overrides, up to 20 allowlisted read calls, and 28 named EVM mainnets. Focused tools are also
available for wallet balance, gas estimate, contract information, transaction status and ERC-20
balance/allowance. None of them signs, approves, transfers or broadcasts a transaction.

For a decision rather than raw evidence, use `tx_preflight`. Missing evidence is never treated
as approval. Wallet-address sanctions screening is not claimed because name lists cannot clear an
address.

## Market evidence and decisions

Agents can buy the smallest useful observation or a complete decision:

- `spot_price` — multi-exchange median and spread: $0.001
- `open_social_search` — bounded Bluesky and Hacker News evidence: $0.002
- `perp_market_data` — Hyperliquid funding, open interest, premium and volume: $0.005
- `market_history` — archived raw or daily series: $0.008
- `market_data` or `coin_positioning_brief` — computed context with disclosed rules: $0.01
- `market_decision_pack` — action, confidence, reasons, risk flags and invalidation: $0.05

Every paid market result identifies its sources and freshness. The decision pack expires after
15 minutes instead of encouraging an agent to reuse stale market advice.

## High-value guarded workflows

The invoice and US-import products have free preparation tools. Run them first; they validate
shape and required context and return the exact paid next step. The paid tools never release a
payment, place an order, create a forwarding address, book a carrier, sign a wallet transaction
or broadcast anything.

`invoice_to_pay_dossier_eu` accepts UBL/CII XML or ZUGFeRD/Factur-X PDFs with one embedded invoice
XML. An ordinary or scanned PDF is intentionally unsupported. `READY_FOR_BUYER_APPROVAL` is an
evidence decision, not proof of delivery, internal approval, duplicate-ledger status or bank-account
ownership.

`us_import_readiness_guard` is limited to declared ordinary goods from Germany or Austria to the
United States. It supplies non-binding HTS evidence and readiness guidance; it is not customs
clearance, a binding classification or a shipping service.

## x402 payment behavior

HTTP products return an x402 v2 payment requirement with the exact amount. Payment options are
published for Base, Arbitrum, Polygon and Solana. The caller signs the selected option and retries
the identical request. The server never receives a wallet private key and does not perform a
buyer-side transfer.

Use a payment policy that pins the exact HTTPS resource, method, network, recipient and maximum
amount. The free `prepare_x402_purchase_guard` plus paid `x402_purchase_guard` workflow can check
an already received x402 challenge before a buyer confirms it; it never pays the target.

## Verify a delivered result

Signed receipts can be checked independently:

```bash
pip install eucompliance-verify
```

```python
from eucompliance_verify import verify_receipt, PaymentPolicy

verified = verify_receipt(response_json)
decision = PaymentPolicy(max_age_hours=24).check(response_json)
if not decision.allowed:
    raise RuntimeError("receipt outside policy")
```

Verifier source: [eucompliance-verify](https://github.com/PatrickPi1312/eucompliance-verify).
VAT conformance fixtures: [eu-vat-conformance](https://github.com/PatrickPi1312/eu-vat-conformance).

## Operator

eucompliance.tools is operated by Patrick Kaufmann, sole proprietor in Vienna, Austria. The
service exposes evidence and decision APIs; it does not act as a bank, wallet signer, customs
broker, freight forwarder or legal adviser.

On-chain identity: ERC-8004 agent **#60255** on Base.
