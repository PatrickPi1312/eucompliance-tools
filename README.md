# EU compliance & on-chain pre-flight tools for AI agents

**Pay per call in USDC. No account, no API key, no subscription, no minimum.**
Also an **MCP server**, so an agent can simply use the tools.

An agent calls an endpoint, gets `HTTP 402 Payment Required` with the exact price,
pays a fraction of a cent in USDC on Base, repeats the call and gets the answer.
Every answer carries a signed receipt that verifies offline.

Catalog: **https://eucompliance.tools** ·
[`/.well-known/x402`](https://api.eucompliance.tools/.well-known/x402) ·
[`/llms.txt`](https://api.eucompliance.tools/llms.txt)

---

## Add it to your agent (30 seconds)

**Claude Code**
```bash
claude mcp add --transport http eucompliance https://mcp.eucompliance.tools/mcp
```

**Claude Desktop / Cursor / any MCP client** — add to the config:
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

Nothing to sign up for. Two tools are free (VAT ID and IBAN validation, 5000/day);
the rest are paid per call and an agent with a wallet settles them over x402 by
itself. Ask it *"validate VAT ID ATU16170006"* and it works immediately.

---

## What the tools answer

### On-chain pre-flight — for agents that transact

| Tool | What it answers | Price |
|---|---|---|
| **Transaction pre-flight** | Will this transaction go through? Gas vs. balance, full simulation with the **revert reason decoded into plain text**, stuck pending nonces, contract-or-wallet recipient, ERC-20 allowance, and a sanctions screen of the counterparty. One call, `send` / `do not send` with every blocking reason | $0.005 |
| **Token allowance & balance** | Enough allowance and balance for this amount? Live from the chain on Base, Ethereum, Arbitrum, Optimism and Polygon | $0.002 |
| **Transaction status** | What happened to my transaction — and if it failed, **why**, recovered by replaying the call against its block. The receipt alone does not carry that reason | $0.002 |

A failed transaction costs the full gas of the attempt. These calls cost a fraction of it.

### EU compliance — for agents that onboard, invoice or pay

| Tool | What it answers | Price |
|---|---|---|
| **Counterparty check (KYB)** | VAT ID + sanctions + IBAN + GLEIF LEI in one call → traffic light `clear / review / blocked` with reasons | $0.02 |
| **E-invoice validation** | EN 16931 / XRechnung / ZUGFeRD / Factur-X / Peppol BIS: mandatory fields, totals arithmetic, VAT category rules — each finding with its official `BR-` code | $0.02 |
| **EU VAT rules engine** | Place of supply, reverse charge, intra-Community supply, OSS threshold, with EN 16931 codes (`BT-151` category, `BT-121` exemption reason) | $0.005 |
| **Sanctions screening** | EU (Commission FSF) + UN Security Council + UK (HM Treasury OFSI) in one call, re-indexed daily, every hit auditable with list, reference and generation date | $0.01 |
| **Company register lookup** | Official national registers: FR, NO, CZ, FI, SK, PL — normalised | $0.01 |
| **Agent spend bookkeeping** | What did my agent spend, in EUR at the ECB rate of the payment date, with VAT treatment and CSV export | $0.10 |
| **VAT ID / IBAN validation** | Format plus live VIES registration check; IBAN mod-97 with bank identification | **free** via MCP |

### General purpose

| Tool | What it answers | Price |
|---|---|---|
| **Chat completion** | A model answer per call, no account and no API key. 8k chars in, 1k tokens out | $0.005 |
| **Chat completion (plus)** | Stronger model, 16k chars in, 2k tokens out | $0.02 |
| **Web page reader** | URL in, readable content out as clean markdown or text — direct fetch of the page, no third-party index in between | $0.005 |

---

## Try it without paying anything

Free, 5 calls a day per address, no signup:

```bash
curl "https://api.eucompliance.tools/free/vat-rules?supplier=AT&customer=DE&b2b=true&type=service"
curl "https://api.eucompliance.tools/free/sanctions?name=Some%20Company%20Ltd"
```

Or get a key with 100 free calls covering every tool at
**https://api.eucompliance.tools/kaufen** — email in, key out, no card.

---

## Verify any answer without trusting us

Every response carries a receipt: the result hash, the issue time and an EIP-191
signature. Checking it needs no network and no trust in the issuer:

```bash
pip install eucompliance-verify
```
```python
from eucompliance_verify import verify_receipt, PaymentPolicy

result = verify_receipt(response_json)          # recomputes the hash, recovers the signer
policy = PaymentPolicy(max_age_hours=24)
if not policy.check(response_json).allowed:
    raise RuntimeError("do not release this payment")
```

Source: [eucompliance-verify](https://github.com/PatrickPi1312/eucompliance-verify) (MIT).
Conformance fixtures for the VAT engine, public domain:
[eu-vat-conformance](https://github.com/PatrickPi1312/eu-vat-conformance).

---

## Paying

x402 v2, scheme `exact`, network `eip155:8453` (Base mainnet), asset USDC.
The payer needs no ETH — gas is sponsored via EIP-3009, with Coinbase CDP as
facilitator. Call any endpoint without payment to receive the challenge containing
the exact price and payment address.

Prefer an invoice? The operator is an Austrian trade business with a VAT ID and can
bill normally — see the signup page.

---

## Who runs this

**eucompliance.tools**, operated from Vienna, Austria.

Two things about the origin, because they explain why the tools are shaped the way
they are:

The VAT engine was not built for a market — it was built because we issue
reverse-charge invoices ourselves every week and nothing off the shelf produced the
codes our accountant, and from 2026 the EU e-invoicing mandates, require. Every rule
in it is one we file under. The conformance fixtures are public so you can check that
claim rather than believe it.

The on-chain tooling came out of running an agent wallet and getting tired of
transactions that failed for reasons the receipt did not explain. The pre-flight
check is the thing we wanted to exist.

Operator: Kaufmann Installationen, an Austrian sole proprietorship with a VAT ID —
which is why an ordinary invoice is an option alongside per-call USDC.
On-chain identity: ERC-8004 agent **#60255** on Base.

MIT licensed. No telemetry, no tracking, no key required to look.
