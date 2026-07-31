# EU Compliance Tools for AI Agents — pay per call in USDC (x402), no account

Machine-payable EU compliance APIs. An AI agent calls an endpoint, gets `HTTP 402 Payment Required`,
pays a fraction of a cent in **USDC on Base**, repeats the call and receives the result.
**No account, no API key, no subscription, no minimum.** Also available as an **MCP server**
for Claude, Claude Code and other agent runtimes.

Catalog: **https://eucompliance.tools** · Machine-readable: [`/llms.txt`](https://eucompliance.tools/llms.txt) · [`/agents.json`](https://eucompliance.tools/agents.json)

| Tool | What it answers | Price |
|---|---|---|
| **Counterparty check (KYB)** | VAT ID + sanctions + IBAN in one call → traffic light `clear / review / blocked` with reasons | $0.02 |
| **EU sanctions screening** | Is this person or company on the official EU consolidated sanctions list? (AML/KYC) | $0.01 |
| **EU VAT rules engine** | Place of supply, reverse charge, EN 16931 category + exemption codes for e-invoicing | $0.003 |
| **EU VAT ID validation** | Live VIES register check, returns registered name and address | $0.002 |
| **IBAN validation** | Checksum and country structure | $0.002 |
| **Receipt / invoice extraction** | Photo or PDF → structured JSON (vendor, date, total, VAT, line items), DACH-optimised | $0.02 |

Every answer carries its **official source and data date**, so results are auditable.
Sanctions data comes from the European Commission's Financial Sanctions Files and is re-indexed daily.

## Quick start (Python)

```bash
pip install "x402[evm]" requests
```

```python
import requests
from eth_account import Account
from x402.client import x402ClientSync
from x402.http.clients.requests import wrapRequestsWithPayment
from x402.mechanisms.evm.exact.register import register_exact_evm_client
from x402.mechanisms.evm.signers import EthAccountSigner

account = Account.from_key("0x...")          # wallet funded with USDC on Base
client = x402ClientSync()
register_exact_evm_client(client, EthAccountSigner(account), networks="eip155:8453")
session = wrapRequestsWithPayment(requests.Session(), client)

r = session.get("https://api.eucompliance.tools/x402/counterparty",
                params={"name": "Acme GmbH", "vat": "ATU12345678"})
print(r.json()["risk"])       # {'level': 'clear', 'reasons': [...], 'recommendation': '...'}
```

The payer needs **no ETH for gas** — settlement is sponsored via EIP-3009 through the Coinbase facilitator.

See [`examples/`](examples/) for a runnable script covering every endpoint.

## MCP (Claude, Claude Code, other agent runtimes)

```bash
claude mcp add --transport http eu-compliance https://mcp.eucompliance.tools/mcp
```

Tools: `check_counterparty_eu`, `screen_sanctions_eu`, `eu_vat_rules`, `validate_vat`, `validate_iban`.
Payment travels in-band with the tool call (`_meta`); an unpaid call returns machine-readable
payment requirements.

## Why pay-per-call

Compliance data is needed in bursts: onboarding a supplier, booking an invoice, paying a new payee.
Subscriptions price that badly for agents, and API keys require a human to sign up. With x402 an
agent can settle a $0.002 call on its own within its spending policy — no contract, no onboarding.

## Endpoints

| Method | Endpoint | Parameters |
|---|---|---|
| GET | `/x402/counterparty` | `name`, `vat`, `iban`, `country` (at least one of name/vat/iban) |
| GET | `/x402/sanctions` | `name` (required), `country`, `threshold` (default 0.85) |
| GET | `/x402/vat-rules` | `supplier`, `customer`, `b2b`, `type` (`service`\|`goods`) |
| GET | `/x402/vat/{vat_id}` | EU VAT ID, e.g. `ATU16170006` |
| GET | `/x402/iban/{iban}` | IBAN |
| POST | `/x402/extract` | multipart, field `file` (JPEG/PNG/WebP/PDF, max 10 MB) |

Base URL: `https://api.eucompliance.tools` · Protocol: x402 v2, scheme `exact`,
network `eip155:8453` (Base mainnet), asset USDC, facilitator: Coinbase CDP.

## Keywords

EU VAT validation API, VIES API, EU sanctions screening API, AML KYC screening, KYB onboarding,
reverse charge, place of supply, EN 16931, BT-151, BT-121, XRechnung, ZUGFeRD, Peppol BIS, OSS,
IBAN validation, receipt OCR, invoice extraction, x402, agent payments, USDC, Base, MCP server.

## Operator & disclaimer

Operated by Kaufmann Installationen, Vienna, Austria — office@installateur1210.at

Results are informational and based on the cited official sources. They are **not legal or tax
advice**. Verify against the cited source before acting on a result.

Licence: MIT (this repository — the examples and documentation).
