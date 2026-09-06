"""Optional offline verifier; requires eth-account, never a private key."""
from __future__ import annotations

import re

from .adapter import receipt_message, result_hash


def make_verifier(*, trusted_issuer: str):
    """Pin an issuer obtained through the operator's trusted setup channel.

    Never set trusted_issuer from the receipt currently being verified.
    Dependency failure is explicit at setup, before any paid transport call.
    """
    if not isinstance(trusted_issuer, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", trusted_issuer):
        raise ValueError("a pinned EVM issuer address is required")
    from eth_account import Account
    from eth_account.messages import encode_defunct

    def verify(document: dict) -> bool:
        try:
            receipt = document["receipt"]
            if (receipt["standard"] != "eucompliance-receipt/2"
                    or receipt["issuer_key"].lower() != trusted_issuer.lower()
                    or receipt["result_hash"] != result_hash(document)
                    or receipt["signed_message"] != receipt_message(receipt)):
                return False
            recovered = Account.recover_message(
                encode_defunct(text=receipt_message(receipt)), signature=receipt["signature"])
            return recovered.lower() == trusted_issuer.lower()
        except Exception:
            return False

    return verify
