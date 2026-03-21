# SynthesisPayAgent — Autonomous GitHub Issue Solver

> An autonomous AI agent that finds paid GitHub issues, solves them with Claude,
> submits pull requests, and collects ETH bounties on-chain.

Built for [The Synthesis Hackathon](https://synthesis.md) — March 2026
**Track:** Agents that pay

---

## What it does

```
┌─────────────────────────────────────────────────────────────┐
│                     Autonomous Loop                          │
│                                                              │
│  1. SCAN ──► GitHub repos for open issues with ETH bounties  │
│  2. TRIAGE ─► Claude (Haiku) decides if it is solvable       │
│  3. SOLVE ──► Claude (Opus) reads files + generates a fix    │
│  4. SUBMIT ─► Fork repo → push branch → open PR             │
│  5. MONITOR ► Poll PRs for merge                             │
│  6. COLLECT ► Log expected ETH payment to agent wallet       │
└─────────────────────────────────────────────────────────────┘
```

### Agent identity

The agent has a permanent, verifiable Ethereum identity:

| Field | Value |
|---|---|
| Name | SynthesisPayAgent |
| Address | `0x98678e12D036EB6C56D22e82bD57FDaf0Ece6920` |
| Track | Agents that pay |

The agent signs its own identity manifest — no central registry needed.

```bash
python register.py --verify   # cryptographically verify identity
python register.py            # announce registration with signed timestamp
```

---

## Architecture

```
main.py           Orchestration loop (scan → triage → solve → submit → monitor)
solver.py         Claude Opus agent with file-reading tools; generates code fixes
github_client.py  GitHub API: issues, forks, Git Data API commits, PRs
bounty.py         Regex parser: extracts ETH amounts from issue text
state.py          Persistent job state (state.json, atomic writes)
ethereum.py       ETH payment client with approval gates and daily cap
identity.py       Agent identity: load, verify, sign
register.py       Self-registration script
```

---

## Quick start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY, GITHUB_TOKEN, GITHUB_USERNAME, TARGET_REPOS

# 3. Register the agent identity
python register.py --verify

# 4. Run the autonomous loop
python main.py
```

---

## Bounty detection

The agent parses ETH amounts from issue bodies and comments using these patterns
(in priority order):

| Pattern | Example |
|---|---|
| `bounty: <N> ETH` | `bounty: 0.05 ETH` |
| `<N> ETH bounty` | `0.1 ETH bounty` |
| bare `<N> ETH` | `paying 0.05 ETH for this` |

---

## How the solver works

Claude is given three tools:

| Tool | Purpose |
|---|---|
| `list_directory(path)` | Explore repo structure |
| `read_file(path)` | Read source files (truncated at 40 000 chars) |
| `submit_fix(changes, pr_title, pr_body, confidence)` | Finalize the fix |

The solver is two-phase:
1. **Triage** (claude-haiku-4-5) — cheap check: is this solvable without human input?
2. **Solve** (claude-opus-4-6) — reads up to 12 tool-use turns, then submits a fix.

Solutions with `confidence=low` are rejected and not submitted.

---

## Spending controls

Bounty payments flow *to* the agent wallet. Outbound payments (via `agent.py`)
require explicit human approval above a configurable threshold:

| Control | Default | Description |
|---|---|---|
| `APPROVAL_THRESHOLD_ETH` | 0.01 ETH | Human must approve outbound tx ≥ this |
| `MAX_DAILY_SPEND_ETH` | 0.1 ETH | Hard daily outbound cap in code |

---

## State file

`state.json` tracks every issue the agent has seen:

```json
{
  "https://github.com/owner/repo/issues/42": {
    "status": "merged",
    "repo": "owner/repo",
    "issue_number": 42,
    "pr_url": "https://github.com/owner/repo/pull/7",
    "bounty_eth": 0.05,
    "submitted_at": "2026-03-21T10:00:00Z",
    "merged_at": "2026-03-21T14:30:00Z"
  }
}
```

Statuses: `submitted` → `merged` or `failed` | `skipped`

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `GITHUB_TOKEN` | Yes | GitHub personal access token (repo + fork scope) |
| `GITHUB_USERNAME` | Yes | GitHub username matching the token |
| `TARGET_REPOS` | Yes | Comma-separated `owner/repo` list to scan |
| `ETH_RPC_URL` | No | Ethereum RPC for wallet monitoring |
| `ETH_PRIVATE_KEY` | No | Agent wallet private key |
| `POLL_INTERVAL` | No | Seconds between scans (default: 300) |
| `MIN_BOUNTY_ETH` | No | Minimum bounty to consider (default: 0.001) |

---

## Why Ethereum?

- **Transparent** — every bounty payment is public and auditable on-chain
- **Trustless** — the agent's wallet address is its identity; no platform needed
- **Programmable** — future: smart-contract escrow released on PR merge
  (track: *Agents that cooperate*)
