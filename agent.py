"""
Synthesis Hackathon — "Agents that pay"

A Claude-powered autonomous payment agent with transparent on-chain Ethereum
settlement and human-in-the-loop spending controls.

Design principles:
  - Every payment decision is reasoned by Claude (auditable chain-of-thought)
  - Transactions above a threshold require explicit human approval
  - A hard daily cap is enforced in code, not just policy
  - All payments carry an on-chain memo for full auditability
"""

import json
import os
import sys
from typing import Any

import anthropic
from dotenv import load_dotenv

from ethereum import EthereumPaymentClient

load_dotenv()

# ---------------------------------------------------------------------------
# Tool definitions exposed to Claude
# ---------------------------------------------------------------------------

TOOLS: list[dict] = [
    {
        "name": "check_balance",
        "description": "Check the agent's current ETH balance.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "estimate_gas",
        "description": "Estimate the gas cost in ETH for sending a payment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "amount_eth": {
                    "type": "number",
                    "description": "Amount of ETH to send (used for context, gas is fixed for simple transfers).",
                }
            },
            "required": ["amount_eth"],
        },
    },
    {
        "name": "send_payment",
        "description": (
            "Send an ETH payment to a recipient address. "
            "If the amount exceeds the approval threshold, this tool will "
            "pause and request human approval before proceeding."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to_address": {
                    "type": "string",
                    "description": "Recipient Ethereum address (0x…).",
                },
                "amount_eth": {
                    "type": "number",
                    "description": "Amount of ETH to send.",
                },
                "memo": {
                    "type": "string",
                    "description": "Short description of the payment purpose, stored on-chain.",
                },
            },
            "required": ["to_address", "amount_eth", "memo"],
        },
    },
    {
        "name": "get_spending_summary",
        "description": "Get today's spending summary and remaining daily allowance.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------


def execute_tool(
    name: str,
    inputs: dict[str, Any],
    eth_client: EthereumPaymentClient,
) -> str:
    """Execute a tool call and return the result as a JSON string."""

    if name == "check_balance":
        balance = eth_client.get_balance_eth()
        return json.dumps({"balance_eth": balance, "address": eth_client.address})

    elif name == "estimate_gas":
        amount_eth = inputs["amount_eth"]
        gas_eth = eth_client.estimate_gas_cost_eth(amount_eth)
        return json.dumps(
            {
                "estimated_gas_eth": gas_eth,
                "total_cost_eth": amount_eth + gas_eth,
            }
        )

    elif name == "get_spending_summary":
        eth_client._reset_daily_if_needed()
        from web3 import Web3

        spent = float(Web3.from_wei(eth_client._daily_spent, "ether"))
        cap = float(Web3.from_wei(eth_client.max_daily_spend, "ether"))
        threshold = float(Web3.from_wei(eth_client.approval_threshold, "ether"))
        return json.dumps(
            {
                "daily_spent_eth": spent,
                "daily_cap_eth": cap,
                "remaining_allowance_eth": max(0.0, cap - spent),
                "approval_threshold_eth": threshold,
            }
        )

    elif name == "send_payment":
        to_address: str = inputs["to_address"]
        amount_eth: float = inputs["amount_eth"]
        memo: str = inputs.get("memo", "")

        # Human-in-the-loop gate
        if eth_client.requires_approval(amount_eth):
            print(
                f"\n[APPROVAL REQUIRED] The agent wants to send {amount_eth} ETH "
                f"to {to_address}.\nMemo: {memo}"
            )
            answer = input("Approve? (yes/no): ").strip().lower()
            if answer not in ("yes", "y"):
                return json.dumps(
                    {"status": "rejected", "reason": "Human operator denied approval."}
                )

        # Daily cap check
        if eth_client.would_exceed_daily_cap(amount_eth):
            return json.dumps(
                {
                    "status": "rejected",
                    "reason": "Payment would exceed the daily spending cap.",
                }
            )

        try:
            receipt = eth_client.send_payment(to_address, amount_eth, memo)
            return json.dumps(receipt)
        except Exception as exc:
            return json.dumps({"status": "error", "reason": str(exc)})

    else:
        return json.dumps({"error": f"Unknown tool: {name}"})


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------


def run_agent(user_request: str, eth_client: EthereumPaymentClient) -> str:
    """
    Run the Claude agent loop for a single user request.
    Returns the final text response.
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    system_prompt = f"""You are a transparent, autonomous Ethereum payment agent.

Your wallet address: {eth_client.address}

You help users send ETH payments on-chain. For every payment you:
1. Check the current balance and gas costs
2. Verify the request is reasonable
3. Include a descriptive memo stored on-chain for auditability
4. Respect spending controls (approval threshold and daily cap)

Always reason through your decisions explicitly. If something seems suspicious
or if a payment would leave insufficient funds for gas, say so clearly.
"""

    messages = [{"role": "user", "content": user_request}]

    while True:
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=4096,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )

        # Collect any text blocks for display
        for block in response.content:
            if hasattr(block, "text"):
                print(f"\nAgent: {block.text}")

        if response.stop_reason == "end_turn":
            # Extract final text response
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            return ""

        if response.stop_reason == "tool_use":
            # Execute tool calls
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    print(f"\n[Tool call] {block.name}({json.dumps(block.input)})")
                    result = execute_tool(block.name, block.input, eth_client)
                    print(f"[Tool result] {result}")
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        }
                    )

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
        else:
            # Unexpected stop reason
            break

    return ""


# ---------------------------------------------------------------------------
# Demo / CLI entry point
# ---------------------------------------------------------------------------


def demo_mode() -> None:
    """Run a simulated demo without a live Ethereum connection."""
    print("=== Synthesis Hackathon — Agents that Pay (DEMO MODE) ===")
    print("No ETH_RPC_URL or ETH_PRIVATE_KEY set. Running in simulation mode.\n")

    class MockEthClient:
        address = "0xDemoAgent0000000000000000000000000000001"
        approval_threshold = int(0.01 * 1e18)
        max_daily_spend = int(0.1 * 1e18)
        _daily_spent = 0
        _last_reset_day = None

        def get_balance_eth(self):
            return 0.5

        def estimate_gas_cost_eth(self, _):
            return 0.0002

        def requires_approval(self, amount_eth):
            return amount_eth >= 0.01

        def would_exceed_daily_cap(self, amount_eth):
            return False

        def _reset_daily_if_needed(self):
            pass

    mock_client = MockEthClient()

    test_request = (
        "Please send 0.005 ETH to 0xRecipient123 for 'invoice #42 — freelance work'."
    )
    print(f"User: {test_request}\n")
    run_agent(test_request, mock_client)  # type: ignore[arg-type]


def main() -> None:
    rpc_url = os.environ.get("ETH_RPC_URL", "")
    private_key = os.environ.get("ETH_PRIVATE_KEY", "")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")

    if not anthropic_key:
        print("Error: ANTHROPIC_API_KEY is required.", file=sys.stderr)
        sys.exit(1)

    if not rpc_url or not private_key:
        demo_mode()
        return

    approval_threshold = float(os.environ.get("APPROVAL_THRESHOLD_ETH", "0.01"))
    max_daily_spend = float(os.environ.get("MAX_DAILY_SPEND_ETH", "0.1"))

    eth_client = EthereumPaymentClient(
        rpc_url=rpc_url,
        private_key=private_key,
        approval_threshold_eth=approval_threshold,
        max_daily_spend_eth=max_daily_spend,
    )

    if len(sys.argv) > 1:
        user_request = " ".join(sys.argv[1:])
    else:
        print("Enter your payment request (or Ctrl-C to quit):")
        user_request = input("> ").strip()

    run_agent(user_request, eth_client)


if __name__ == "__main__":
    main()
