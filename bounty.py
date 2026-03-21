"""
Detect ETH bounty amounts in GitHub issue text.

Searches the issue body and comments for patterns like:
  "0.05 ETH bounty", "bounty: 0.1 ETH", "Reward: 0.5 ETH"
Returns the first match as a float (ETH), or None if none found.
"""

import re
from typing import Optional

# Patterns ordered from most specific to most general.
_PATTERNS = [
    # "bounty: 0.05 ETH" / "reward: 0.05 ETH"
    r"(?:bounty|reward|prize)\s*[:\-]\s*(\d+(?:\.\d+)?)\s*ETH",
    # "0.05 ETH bounty" / "0.05 ETH reward"
    r"(\d+(?:\.\d+)?)\s*ETH\s*(?:bounty|reward|prize)",
    # bare "0.05 ETH" (least specific — last resort)
    r"(\d+(?:\.\d+)?)\s*ETH",
]


def parse_bounty_eth(text: str) -> Optional[float]:
    """Return the ETH bounty amount found in *text*, or None."""
    for pattern in _PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            value = float(m.group(1))
            # Sanity bounds: ignore unrealistically large or zero values.
            if 0 < value <= 1000:
                return value
    return None


def extract_from_issue(body: str, comment_bodies: list[str]) -> Optional[float]:
    """Check issue body first, then comments, and return the first ETH amount found."""
    result = parse_bounty_eth(body or "")
    if result is not None:
        return result
    for comment in comment_bodies:
        result = parse_bounty_eth(comment or "")
        if result is not None:
            return result
    return None
