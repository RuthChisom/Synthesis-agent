"""
Agent identity management.

The agent's identity is its Ethereum wallet — a cryptographic keypair that
gives it a globally unique, verifiable address with no central authority needed.

identity.json  — public identity (safe to commit, shareable)
.env           — private key (never committed)
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Optional

from eth_account import Account
from eth_account.messages import encode_defunct


IDENTITY_FILE = Path(__file__).parent / "identity.json"


def load_public_identity() -> dict:
    """Load the committed public identity manifest."""
    with open(IDENTITY_FILE) as f:
        return json.load(f)


def verify_identity(identity: dict) -> bool:
    """
    Verify that the signature in identity.json was produced by the private key
    corresponding to identity['address']. Anyone can do this — no trusted third
    party required.
    """
    fields = {k: v for k, v in identity.items() if k not in ("manifest_hash", "signature")}
    expected_hash = hashlib.sha256(
        json.dumps(fields, sort_keys=True).encode()
    ).hexdigest()

    if expected_hash != identity["manifest_hash"]:
        return False

    message = encode_defunct(hexstr=identity["manifest_hash"])
    recovered = Account.recover_message(message, signature=bytes.fromhex(identity["signature"]))
    return recovered.lower() == identity["address"].lower()


def get_agent_header() -> str:
    """Return a one-line identity string suitable for logging or API headers."""
    identity = load_public_identity()
    return (
        f"{identity['name']} | {identity['address']} | "
        f"hackathon={identity['hackathon']}"
    )


def sign_message(message: str) -> dict:
    """
    Sign an arbitrary message with the agent's private key.
    Returns {message, signer_address, signature}.
    Requires ETH_PRIVATE_KEY to be set.
    """
    private_key = os.environ.get("ETH_PRIVATE_KEY")
    if not private_key:
        raise EnvironmentError("ETH_PRIVATE_KEY is required for signing")

    account = Account.from_key(private_key)
    signable = encode_defunct(text=message)
    signed = account.sign_message(signable)

    return {
        "message": message,
        "signer_address": account.address,
        "signature": signed.signature.hex(),
    }


if __name__ == "__main__":
    identity = load_public_identity()
    print("=== Agent Identity ===")
    print(json.dumps(identity, indent=2))
    print("\n=== Verification ===")
    valid = verify_identity(identity)
    print(f"Signature valid: {valid}")
    print(f"\nHeader: {get_agent_header()}")
