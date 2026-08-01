# Installation for AI agents

This is a **remote MCP server**. Nothing is installed, nothing is built, no
runtime is needed — it is one URL:

```
https://mcp.eucompliance.tools/mcp
```

Transport: streamable HTTP. No authentication, no API key, no account.

## Claude Code

```bash
claude mcp add --transport http eucompliance https://mcp.eucompliance.tools/mcp
```

## Claude Desktop, Cline, Cursor, Windsurf — config file

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

Clients that only speak the older stdio transport can bridge to it:

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

## Verify it works

Ask the assistant:

> *"Validate the VAT ID ATU16170006"*

It should answer that the ID is registered and return the registered name. That
tool is free and needs no wallet, so a successful answer proves the connection.

## What is free and what is paid

| | |
|---|---|
| **Free, no limit worth mentioning** | `validate_vat`, `validate_iban` (5000/day), `verify_compliance_receipt` |
| **Paid per call** | everything else, from $0.002, settled automatically over [x402](https://x402.org) in USDC on Base if the agent has a wallet |

An agent without a wallet still connects and can use the free tools; the paid
ones answer with `HTTP 402` and the exact price instead of failing.

## Tools

`validate_vat` · `validate_iban` · `screen_sanctions_eu` · `eu_vat_rules` ·
`check_counterparty_eu` · `validate_einvoice_eu` · `lookup_company_eu` ·
`agent_spend_statement` · `must_verify_before_pay` · `verify_compliance_receipt`

## Troubleshooting

**"Connection failed"** — check the URL ends in `/mcp`. A trailing slash is fine,
a missing `/mcp` is not.

**"Tool returned 402"** — that is not an error. It is the price quote for a paid
tool; the agent needs a funded wallet to settle it, or use the free tools.

**Nothing at all happens** — the server answers `GET https://mcp.eucompliance.tools/.well-known/mcp/server-card.json`
with its metadata. If that returns JSON, the server is up and the problem is on
the client side.
