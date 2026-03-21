"""
Agent self-registration for The Synthesis Hackathon.

This script does three things:
  1. Loads (or generates) the agent's Ethereum identity
  2. Verifies the cryptographic signature on the identity manifest
  3. Announces the agent's registration with a signed timestamp

The agent's on-chain address IS its identity — no central authority or
pre-existing account needed. This is the Ethereum-native approach.

Usage:
  python register.py                  # announce registration
  python register.py --generate       # generate a brand-new identity
  python register.py --verify         # verify existing identity only
"""

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from eth_account import Account
from eth_account.messages import encode_defunct
from dotenv import load_dotenv

from identity import IDENTITY_FILE, load_public_identity, verify_identity

load_dotenv()


def generate_identity(name: str = "SynthesisPayAgent") -> dict:
    """Generate a new Ethereum keypair and write identity.json."""
    account = Account.create()

    identity = {
        "name": name,
        "version": "1.0.0",
        "address": account.address,
        "public_key": account._key_obj.public_key.to_hex(),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "hackathon": "The Synthesis — March 2026",
        "track": "Agents that pay",
        "repo": "https://github.com/RuthChisom/Synthesis-agent",
    }

    manifest_hash = hashlib.sha256(
        json.dumps(identity, sort_keys=True).encode()
    ).hexdigest()

    signed = account.sign_message(encode_defunct(hexstr=manifest_hash))

    identity["manifest_hash"] = manifest_hash
    identity["signature"] = signed.signature.hex()

    with open(IDENTITY_FILE, "w") as f:
        json.dump(identity, f, indent=2)
        f.write("\n")

    print(f"[✓] New identity generated and saved to {IDENTITY_FILE}")
    print(f"    Address : {account.address}")
    print(f"    Name    : {name}")
    print()
    print("IMPORTANT — save this private key to your .env file:")
    print(f"  ETH_PRIVATE_KEY={account.key.hex()}")
    print("  (It will NOT be stored anywhere by this script.)")

    return identity


def announce_registration(identity: dict) -> None:
    """
    Print a signed registration announcement.
    The agent signs the current timestamp to prove liveness.
    """
    private_key = os.environ.get("ETH_PRIVATE_KEY")

    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    announcement = {
        "event": "hackathon_registration",
        "agent_name": identity["name"],
        "agent_address": identity["address"],
        "hackathon": identity["hackathon"],
        "track": identity["track"],
        "repo": identity["repo"],
        "timestamp": timestamp,
    }

    if private_key:
        account = Account.from_key(private_key)
        payload = json.dumps(announcement, sort_keys=True)
        signed = account.sign_message(encode_defunct(text=payload))
        announcement["registration_signature"] = signed.signature.hex()
        announcement["signed_by"] = account.address
        print("[✓] Registration signed with agent private key")
    else:
        print("[!] ETH_PRIVATE_KEY not set — announcement is unsigned")

    print()
    print("=== REGISTRATION ANNOUNCEMENT ===")
    print(json.dumps(announcement, indent=2))
    print()

    # Save for reference
    out_path = Path(__file__).parent / "registration.json"
    with open(out_path, "w") as f:
        json.dump(announcement, f, indent=2)
        f.write("\n")
    print(f"[✓] Saved to {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent self-registration for The Synthesis")
    parser.add_argument("--generate", action="store_true", help="Generate a new identity")
    parser.add_argument("--verify", action="store_true", help="Verify identity only")
    args = parser.parse_args()

    if args.generate:
        identity = generate_identity()
    else:
        if not IDENTITY_FILE.exists():
            print("[!] No identity.json found. Run with --generate first.")
            sys.exit(1)
        identity = load_public_identity()

    print("=== Agent Identity ===")
    print(f"  Name    : {identity['name']}")
    print(f"  Address : {identity['address']}")
    print(f"  Created : {identity['created_at']}")
    print(f"  Track   : {identity['track']}")
    print()

    valid = verify_identity(identity)
    if not valid:
        print("[✗] Identity signature is INVALID — identity.json may be tampered.")
        sys.exit(1)
    print("[✓] Identity signature verified — this agent controls its address")
    print()

    if not args.verify:
        announce_registration(identity)


if __name__ == "__main__":
    main()
