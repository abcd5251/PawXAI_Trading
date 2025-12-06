import os
import json
import base64
import time
import sys
from typing import Optional

import requests
from dotenv import load_dotenv
load_dotenv()



LAMPORTS_PER_SOL = 1_000_000_000
SOL_MINT = "So11111111111111111111111111111111111111112"
ULTRA_SEARCH_URL = "https://lite-api.jup.ag/ultra/v1/search"
ULTRA_ORDER_URL = "https://lite-api.jup.ag/ultra/v1/order"
ULTRA_EXECUTE_URL = "https://lite-api.jup.ag/ultra/v1/execute"


def require_solders():
    try:
        from solders.keypair import Keypair  # noqa: F401
        from solders.transaction import VersionedTransaction  # noqa: F401
        from solders import message  # noqa: F401
    except ImportError:
        print(
            "Missing dependency: solders.\n"
            "Install it with: pip install solders\n"
            "If you also need a Solana RPC client later, install: pip install solana"
        )
        sys.exit(1)


def _b58decode(s: str) -> bytes:
    """Minimal Base58 decoder (Bitcoin alphabet) to support base58 private keys."""
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    # Convert the string to an integer
    num = 0
    for char in s:
        idx = alphabet.find(char)
        if idx == -1:
            raise ValueError("Invalid base58 character in private key")
        num = num * 58 + idx
    # Convert the integer to bytes
    b = num.to_bytes((num.bit_length() + 7) // 8, byteorder='big') if num > 0 else b''
    # Count leading zeros (represented by leading '1' in base58)
    n_pad = len(s) - len(s.lstrip('1'))
    return b'\x00' * n_pad + b


def load_keypair() -> "Keypair":
    """
    Load a Solana keypair for signing.

    Priority:
    1) SOLANA_KEYPAIR_PATH -> path to a JSON array (e.g. ~/.config/solana/id.json)
    2) SOL_PRIVATE_KEY or SOLANA_PRIVATE_KEY -> JSON array of 64 integers OR base58 string
    """
    from solders.keypair import Keypair

    # Try path first
    keypair_path = os.getenv("SOLANA_KEYPAIR_PATH", os.path.expanduser("~/.config/solana/id.json"))
    if os.path.isfile(keypair_path):
        with open(keypair_path, "r") as f:
            arr = json.load(f)
        if not isinstance(arr, list):
            raise ValueError("Keypair file does not contain a JSON array of integers.")
        return Keypair.from_bytes(bytes(arr))

    # Fallback to env: accept SOL_PRIVATE_KEY or SOLANA_PRIVATE_KEY
    pk_env = os.getenv("SOL_PRIVATE_KEY") or os.getenv("SOLANA_PRIVATE_KEY")
    if pk_env:
        # JSON array path
        if pk_env.strip().startswith("["):
            try:
                arr = json.loads(pk_env)
                if not isinstance(arr, list):
                    raise ValueError
                return Keypair.from_bytes(bytes(arr))
            except Exception:
                raise ValueError(
                    "SOL_PRIVATE_KEY/SOLANA_PRIVATE_KEY must be a JSON array of 64 integers or base58 string."
                )
        # Base58 path
        try:
            sk_bytes = _b58decode(pk_env.strip())
            if len(sk_bytes) == 64:
                return Keypair.from_bytes(sk_bytes)
            elif len(sk_bytes) == 32:
                try:
                    # Support 32-byte seed (ed25519). Available in solders >= versions that expose from_seed.
                    return Keypair.from_seed(sk_bytes)
                except Exception:
                    raise ValueError(
                        "Decoded a 32-byte seed. Provide a 64-byte secret key (JSON/base58) or ensure solders supports Keypair.from_seed."
                    )
            else:
                raise ValueError(
                    f"Decoded private key length {len(sk_bytes)} unsupported. Expected 64-byte secret or 32-byte seed."
                )
        except Exception as e:
            raise ValueError(
                f"Failed to decode SOL_PRIVATE_KEY/SOLANA_PRIVATE_KEY as base58: {e}"
            )

    raise FileNotFoundError(
        "No signing key found. Set SOLANA_KEYPAIR_PATH to your id.json or "
        "SOL_PRIVATE_KEY/SOLANA_PRIVATE_KEY to a JSON array (64 ints) or base58 secret key."
    )


def find_popcat_mint() -> str:
    """Resolve POPCAT mint via Jupiter Ultra search API."""
    resp = requests.get(ULTRA_SEARCH_URL, params={"query": "popcat"}, timeout=15)
    resp.raise_for_status()
    results = resp.json()
    if not isinstance(results, list):
        raise RuntimeError("Unexpected response from Ultra search")

    # Prefer exact symbol match, else fallback to first item containing 'popcat'
    for item in results:
        if str(item.get("symbol", "")).upper() == "POPCAT":
            return item["id"]
    for item in results:
        name = (item.get("name") or "").lower()
        symbol = (item.get("symbol") or "").lower()
        if "popcat" in name or "popcat" in symbol:
            return item["id"]

    raise RuntimeError("Could not resolve POPCAT mint via Ultra search")


def get_ultra_order(input_mint: str, output_mint: str, amount: int, taker_pubkey: str) -> dict:
    """Request an Ultra order which includes a base64 transaction to sign."""
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(amount),
        "taker": taker_pubkey,
    }
    resp = requests.get(ULTRA_ORDER_URL, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected Ultra order response")
    if "transaction" not in data or "requestId" not in data:
        raise RuntimeError("Ultra order missing transaction/requestId — route unavailable or taker missing")
    return data


def sign_and_execute(order: dict, keypair: "Keypair") -> dict:
    """Sign Ultra order transaction and execute via Ultra /execute.

    Aligns signature to the correct signer index in the VersionedTransaction
    to avoid invalid signature placements (common cause of 400 errors).
    """
    from solders.transaction import VersionedTransaction
    from solders import message as solders_message

    tx_b64 = order.get("transaction")
    request_id = order.get("requestId")
    if not tx_b64 or not request_id:
        raise RuntimeError("Ultra order missing transaction/requestId — cannot execute")

    raw_tx = VersionedTransaction.from_bytes(base64.b64decode(tx_b64))
    msg = raw_tx.message

    # Find the correct signature index for the taker within required signers
    required = msg.header.num_required_signatures
    signer_keys = list(msg.account_keys[:required])
    taker_pubkey = keypair.pubkey()

    try:
        sig_index = signer_keys.index(taker_pubkey)
    except ValueError:
        # For gasless JupiterZ routes, the taker might not be required signer
        sig_index = None

    if sig_index is not None:
        # Sign the transaction message and place signature at correct index
        signature = keypair.sign_message(solders_message.to_bytes_versioned(msg))
        sigs = raw_tx.signatures
        sigs[sig_index] = signature
        raw_tx.signatures = sigs
        signed_b64 = base64.b64encode(bytes(raw_tx)).decode("utf-8")
    else:
        # No taker signature required; submit original transaction
        signed_b64 = tx_b64

    # Execute via Ultra (handles sending + status)
    payload = {"signedTransaction": signed_b64, "requestId": request_id}
    resp = requests.post(ULTRA_EXECUTE_URL, json=payload, timeout=30)
    # Parse JSON even on non-2xx to expose server-side error details
    try:
        data = resp.json()
    except Exception:
        resp.raise_for_status()
        return {"status": "Unknown", "error": resp.text}

    if resp.status_code >= 400:
        # Return structured error without raising to aid debugging
        return data
    return data


def main():
    require_solders()
    from solders.keypair import Keypair

    # Load .env if present
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    # Load signer and derive taker pubkey
    keypair: Keypair = load_keypair()
    # solders Pubkey exposes base58 via __str__ in recent versions
    taker_pubkey = str(keypair.pubkey())

    # Amount: 0.05 SOL in lamports
    amount_lamports = int(0.05 * LAMPORTS_PER_SOL)

    # Resolve POPCAT mint via Ultra search
    popcat_mint = find_popcat_mint()
    print(f"Resolved POPCAT mint: {popcat_mint}")

    # Get Ultra order
    order = get_ultra_order(SOL_MINT, popcat_mint, amount_lamports, taker_pubkey)
    print(
        "Ultra order prepared. SlippageBps:", order.get("slippageBps"),
        " Route via:", (order.get("routePlan") or [{}])[0].get("swapInfo", {}).get("label")
    )

    # Sign and execute
    execute_res = sign_and_execute(order, keypair)

    status = execute_res.get("status")
    signature = execute_res.get("signature")
    if status == "Success":
        print("Swap successful!")
        if signature:
            print(f"Explorer: https://solscan.io/tx/{signature}")
    else:
        print("Swap failed:")
        print(json.dumps(execute_res, indent=2))


if __name__ == "__main__":
    main()