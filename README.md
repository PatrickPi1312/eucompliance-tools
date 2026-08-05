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
| **E-invoice validation** | EN 16931 / XRechnung / ZUGFeRD / Factur-X / Peppol BIS: the **complete official EN 16931 Schematron rule set** (CEN validation artefacts, used unmodified) plus totals arithmetic and VAT category rules — every finding with its official `BR-` code and severity, result signed and independently verifiable. [Runnable example ↓](#validate-an-e-invoice-in-one-call) | $0.10 |
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
| **Web content extraction** | URL in, readable content out as clean markdown or text — title, author, date, main text, tables, links. Direct fetch of the page, no third-party search index resold | $0.003 |

### Market & social data — for agents that trade or monitor

| Tool | What it answers | Price |
|---|---|---|
| **Crypto spot price** | Median of Binance, Coinbase and Kraken public tickers — every single quote, the spread and the timestamp disclosed, so the median is verifiable. Refuses to answer from a single source | $0.001 |
| **Wallet funding QR** | Your human tops you up with Apple Pay: prefilled onramp link (MoonPay) + QR code for any supported asset and destination wallet. Minimums enforced, address validated — funds settle directly with the licensed onramp, never through this service | $0.005 |
| **Open social search** | Recent public posts from **Bluesky, Hacker News and Mastodon** in one call: author, text, time, engagement, link. Failing sources are named. Twitter/X and TikTok are deliberately out of scope — their terms forbid scraping | $0.002 |
| **Perp market data** | Mark and oracle price, hourly and annualised funding rate, open interest, 24h volume and max leverage per market, from the Hyperliquid public API, sorted by volume | $0.005 |
| **Market brief / yield / gas** | Aggregated crypto-market indicators computed from several upstream x402 feeds, scoring rule disclosed; archived time series at `/x402/history` | $0.005 |

---

## Validate an e-invoice in one call

Check a UBL or CII invoice (XRechnung, ZUGFeRD / Factur-X, Peppol BIS) against the
**complete official EN 16931 Schematron** — the same rule set the official validators
use, run unmodified — plus totals arithmetic and VAT category rules. Every finding
comes back with its official `BR-` code and severity, and the result is signed so it
can be attached to the document as validation evidence.

No wallet needed to try it — [`examples/validate_einvoice.py`](examples/validate_einvoice.py)
runs against the free tier and catches a deliberately broken sample out of the box:

```bash
pip install requests
python examples/validate_einvoice.py                 # broken sample → BR-02, BR-06, BR-CO-26 …
python examples/validate_einvoice.py my-invoice.xml  # your UBL/CII XML
python examples/validate_einvoice.py zugferd.pdf     # or a ZUGFeRD / Factur-X PDF
```

For volume it is one x402 call at `/x402/einvoice` ($0.10) — see
[`examples/pay_and_call.py`](examples/pay_and_call.py).

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

Prefer an invoice? The operator holds an Austrian trade licence and a VAT ID and
can bill normally — see the signup page.

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

Operator: Patrick Kaufmann, sole proprietor in Vienna, Austria (trade licence,
Austrian VAT ID) — which is why an ordinary invoice is an option alongside
per-call USDC.
On-chain identity: ERC-8004 agent **#60255** on Base.

MIT licensed. No telemetry, no tracking, no key required to look.
