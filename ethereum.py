"""
Ethereum integration for the Synthesis payment agent.
Provides transparent on-chain settlement with audit trail.
"""

import os
from decimal import Decimal
from typing import Optional
from web3 import Web3
from web3.types import TxReceipt
from eth_account import Account


class EthereumPaymentClient:
    """Handles on-chain payment operations with spending controls."""

    def __init__(
        self,
        rpc_url: str,
        private_key: str,
        approval_threshold_eth: float = 0.01,
        max_daily_spend_eth: float = 0.1,
    ):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.account = Account.from_key(private_key)
        self.approval_threshold = Web3.to_wei(approval_threshold_eth, "ether")
        self.max_daily_spend = Web3.to_wei(max_daily_spend_eth, "ether")
        self._daily_spent: int = 0  # wei spent today (reset on new day)
        self._last_reset_day: Optional[int] = None

    @property
    def address(self) -> str:
        return self.account.address

    def get_balance_eth(self) -> float:
        balance_wei = self.w3.eth.get_balance(self.account.address)
        return float(Web3.from_wei(balance_wei, "ether"))

    def _reset_daily_if_needed(self) -> None:
        import time
        today = int(time.time()) // 86400
        if self._last_reset_day != today:
            self._daily_spent = 0
            self._last_reset_day = today

    def requires_approval(self, amount_eth: float) -> bool:
        """Return True if this payment exceeds the approval threshold."""
        amount_wei = Web3.to_wei(amount_eth, "ether")
        return amount_wei >= self.approval_threshold

    def would_exceed_daily_cap(self, amount_eth: float) -> bool:
        """Return True if this payment would exceed the daily spend cap."""
        self._reset_daily_if_needed()
        amount_wei = Web3.to_wei(amount_eth, "ether")
        return (self._daily_spent + amount_wei) > self.max_daily_spend

    def send_payment(
        self,
        to_address: str,
        amount_eth: float,
        memo: str = "",
    ) -> dict:
        """
        Execute an on-chain ETH transfer.
        Returns a dict with tx hash, block number, and status.
        Raises ValueError if daily cap would be exceeded.
        """
        self._reset_daily_if_needed()
        amount_wei = Web3.to_wei(amount_eth, "ether")

        if (self._daily_spent + amount_wei) > self.max_daily_spend:
            raise ValueError(
                f"Payment of {amount_eth} ETH would exceed daily cap of "
                f"{Web3.from_wei(self.max_daily_spend, 'ether')} ETH"
            )

        nonce = self.w3.eth.get_transaction_count(self.account.address)
        gas_price = self.w3.eth.gas_price

        tx = {
            "nonce": nonce,
            "to": Web3.to_checksum_address(to_address),
            "value": amount_wei,
            "gas": 21000,
            "gasPrice": gas_price,
            "chainId": self.w3.eth.chain_id,
            # Store memo in data field as utf-8 bytes
            "data": memo.encode("utf-8") if memo else b"",
        }

        signed = self.account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt: TxReceipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)

        self._daily_spent += amount_wei

        return {
            "tx_hash": receipt["transactionHash"].hex(),
            "block_number": receipt["blockNumber"],
            "status": "success" if receipt["status"] == 1 else "failed",
            "amount_eth": amount_eth,
            "to": to_address,
            "memo": memo,
            "daily_spent_eth": float(Web3.from_wei(self._daily_spent, "ether")),
        }

    def estimate_gas_cost_eth(self, amount_eth: float) -> float:
        """Estimate the gas cost for a simple ETH transfer."""
        gas_price = self.w3.eth.gas_price
        gas_cost_wei = 21000 * gas_price
        return float(Web3.from_wei(gas_cost_wei, "ether"))
