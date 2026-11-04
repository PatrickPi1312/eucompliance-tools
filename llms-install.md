# Connect EUCompliance Tools to an agent

This is a **remote MCP server** with **41 tools**, including **10 free preparation,
routing and verification tools**. You do not need to host a server or build the
service. An MCP client connects to:

```
https://mcp.eucompliance.tools/mcp
```

Transport: Streamable HTTP. Discovery and free tools require no service account or
API key. Paid calls require a compatible x402 transport and the buyer's explicit
spending policy; merely connecting a wallet does not authorize a purchase.

## Claude Code

```bash
claude mcp add --transport http eucompliance https://mcp.eucompliance.tools/mcp
```

## Other Streamable HTTP clients

Add the remote URL through your client's MCP settings. For clients supporting the
following `mcpServers` configuration shape:

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

Clients that only support a local stdio process may use an operator-approved
bridge such as `mcp-remote`. Unlike the direct remote connection, this option
downloads and runs a local package and requires Node.js:

```json
{
  "mcpServers": {
    "eucompliance": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://mcp.eucompliance.tools/mcp"]
    }
  }
}
```

## Verify the connection without buying anything

Ask the assistant:

> "List this server's tools, then use the free recommend_agent_tool to find the
> invoice evidence workflow. Do not call paid tools or authorize a payment."

Compare the returned tools with the [live catalog](https://api.eucompliance.tools/agents.json).
The free router selects catalog entries; it does not execute the selected tools.

## What is free and what is paid

| Workflow | Charging |
|---|---|
| Tool discovery, `recommend_agent_tool`, the published preparation tools and receipt verification | Free; applicable abuse/rate limits still apply |
| `invoice_to_pay_dossier_eu` | 1.00 USDC per completed dossier; run `prepare_invoice_to_pay_eu` first |
| `spot_price` or `screen_sanctions_eu` | 0.10 USDC per successful result |
| Other paid tools | Inspect the exact catalog entry and live payment requirement before authorizing |

An agent without a payment transport still connects and can use free tools. An
unpaid paid-tool call returns payment requirements, not free evidence. HTTP
products use HTTP 402; MCP clients must preserve the tool-level payment/error
semantics and must not interpret a transport-level HTTP 200 as a paid success.

## Invoice workflow

1. Supply UBL/CII XML or a ZUGFeRD/Factur-X PDF with one embedded invoice XML,
   plus the required buyer-held context. Ordinary PDFs and scans are unsupported.
2. Run free `prepare_invoice_to_pay_eu`; inspect its result and quoted next step.
3. Only if the buyer has authorized the exact resource, amount, recipient and
   network, use its compatible transport for `invoice_to_pay_dossier_eu`.
4. Verify the signed dossier against the expected issuer, inputs and freshness.
   Route `READY_FOR_BUYER_APPROVAL` to the existing approval stage; keep HOLD,
   DO_NOT_PAY and unavailable evidence on hold. Never release an invoice payment.

The [optional Python adapter](integrations/invoice_guard/README.md) accepts XML
only and includes offline regression tests, not a ready-made wallet or payment
transport. It does not verify bank-account ownership, accounting-ledger duplicate
status, delivery or legal approval.

For a scoped business evaluation, the separate
[Invoice Guard Pilot](https://api.eucompliance.tools/invoice-guard-pilot) is
EUR 149 net once for 30 days, on request and with scope agreed before order.
It covers one bounded workflow: a format/integration check, synthetic example run,
and documented handover with open points. API-call costs are separate and agreed
in advance. There is no subscription. Optional setup from EUR 499 net requires a
separate offer. This does not change the 1.00-USDC API price.

## Troubleshooting

**"Connection failed"** — use the exact URL above and ensure the client supports
Streamable HTTP or an approved bridge.

**"Tool returned payment requirements"** — stop unless a compatible transport
and spending authorization already exist. Do not retry by guessing a price or
ask for a wallet seed/private key. Free tools remain available.

**"Metadata works, tools do not"** — discovery alone does not prove that a paid
operation works. Record the method, tool name and non-sensitive error/status for
diagnosis. Do not post production invoices, payment signatures or credentials in
public issues. The live catalog and [agent guide](https://api.eucompliance.tools/llms.txt)
provide current schemas and error behavior.
