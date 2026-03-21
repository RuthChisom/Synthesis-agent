# Agents that Pay — Synthesis Hackathon Submission

> A Claude-powered autonomous payment agent with transparent on-chain Ethereum settlement and human-in-the-loop spending controls.

## The Problem

AI agents increasingly need to make payments autonomously. The risk: unconstrained agents can drain wallets, get exploited, or act without accountability. Centralised payment rails offer no audit trail and no programmable guardrails.

## The Solution

This agent uses **Ethereum** as trust infrastructure:

- Every payment is settled **on-chain** with a memo field — creating a permanent, verifiable audit trail
- **Human approval gates** are enforced in code for transactions above a configurable threshold
- A **hard daily spending cap** is applied at the wallet level, not just in policy
- Claude's reasoning is exposed at each step, so operators can see *why* a payment was made

```
User: "Pay 0.005 ETH to 0xAbc… for invoice #42"
         │
         ▼
    Claude reasons:
      ✓ Check balance + gas
      ✓ Validate address
      ✓ Amount below threshold → no approval needed
      ✓ Daily cap not exceeded
         │
         ▼
    On-chain tx with memo: "invoice #42 — freelance work"
    TX hash: 0x…  Block: 19…
```

## Architecture

```
agent.py          Claude agent loop + tool routing
ethereum.py       Web3 payment client with spending controls
.env              API keys and configuration
```

### Tools available to the agent

| Tool | Description |
|---|---|
| `check_balance` | Query current ETH balance |
| `estimate_gas` | Estimate gas cost before committing |
| `send_payment` | Execute on-chain transfer (with approval gate) |
| `get_spending_summary` | Daily spend vs. cap |

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env with your API keys

# 3. Run (live mode)
python agent.py "send 0.001 ETH to 0xRecipient for 'coffee'"

# 4. Run (demo mode — no ETH keys needed)
ANTHROPIC_API_KEY=sk-ant-… python agent.py
```

## Spending Controls

| Control | Default | Description |
|---|---|---|
| `APPROVAL_THRESHOLD_ETH` | 0.01 ETH | Human must approve transactions ≥ this |
| `MAX_DAILY_SPEND_ETH` | 0.1 ETH | Hard daily cap enforced in code |

## Why Ethereum?

- **Transparency**: every tx is public and auditable
- **Immutability**: the spending history can't be edited
- **Programmability**: future versions can use smart contracts for multi-sig approval, time-locked spending, and cross-agent escrow (track: *Agents that cooperate*)

## Hackathon Track

**Primary:** Agents that pay
**Secondary:** Agents that trust (on-chain memo = verifiable payment attestation)

---

Built for [The Synthesis Hackathon](https://synthesis.md) — March 2026
