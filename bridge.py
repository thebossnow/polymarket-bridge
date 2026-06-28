#!/usr/bin/env python3
"""
polymarket-bridge

Polished CLI for bridging ERC-20 tokens to Polymarket proxy wallets
and general direct transfers (Polygon focus, but multi-chain).

Features:
- polymarket: Polymarket Bridge API (send from source chain to your exact proxy)
- direct: Send any ERC-20 on a chain (ideal on Polygon after Across or other bridge)
- across-quote: Get quote info for Across source -> Polygon, then use 'direct'

Supports arbitrary ERC-20s via --token-address. USDC has convenient aliases.

Dry-run is fully supported for safe testing.
"""

import argparse
import json
import os
import sys
import time
from decimal import Decimal

from eth_account import Account
import requests
from web3 import Web3

BRIDGE_BASE = "https://bridge.polymarket.com"
DEFAULT_FUNDER = "0x1D8593D2723a920fFE859De0Eef8b0f832aA6008"

CHAIN_CONFIG = {
    "base": {
        "id": 8453,
        "rpc": os.getenv("BASE_RPC", "https://mainnet.base.org"),
        "name": "Base",
        "explorer": "https://basescan.org",
    },
    "ethereum": {
        "id": 1,
        "rpc": os.getenv("ETH_RPC", "https://eth.llamarpc.com"),
        "name": "Ethereum",
        "explorer": "https://etherscan.io",
    },
    "arbitrum": {
        "id": 42161,
        "rpc": os.getenv("ARB_RPC", "https://arb1.arbitrum.io/rpc"),
        "name": "Arbitrum",
        "explorer": "https://arbiscan.io",
    },
    "polygon": {
        "id": 137,
        "rpc": os.getenv("POLYGON_RPC", "https://polygon-rpc.com"),
        "name": "Polygon",
        "explorer": "https://polygonscan.com",
    },
}

TOKEN_MAP = {
    "USDC": {
        "base": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "ethereum": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "arbitrum": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        "polygon": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",  # USDC.e
    },
}


def load_funder(env_file):
    if env_file and os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line.startswith("POLYMARKET_FUNDER"):
                    val = line.split("=", 1)[1].strip().strip("'\"").split()[0]
                    if Web3.is_address(val):
                        return Web3.to_checksum_address(val)
    return DEFAULT_FUNDER


def resolve_token_address(token, chain, explicit=None):
    if explicit:
        return Web3.to_checksum_address(explicit)
    t = token.upper()
    if t in TOKEN_MAP and chain in TOKEN_MAP[t]:
        return TOKEN_MAP[t][chain]
    raise ValueError(f"No address for {token} on {chain}. Use --token-address 0x...")


def get_w3(chain):
    cfg = CHAIN_CONFIG.get(chain)
    if not cfg:
        raise ValueError(f"Chain {chain} not configured")
    w3 = Web3(Web3.HTTPProvider(cfg["rpc"]))
    if not w3.is_connected():
        raise RuntimeError(f"Failed to connect to {chain} RPC")
    return w3, cfg


def get_decimals(w3, addr):
    c = w3.eth.contract(address=addr, abi=[{"inputs":[],"name":"decimals","outputs":[{"type":"uint8"}],"type":"function"}])
    return c.functions.decimals().call()


def build_transfer_tx(w3, token_addr, sender, recipient, amount_wei):
    abi = [
        {"inputs":[{"name":"to","type":"address"},{"name":"value","type":"uint256"}],"name":"transfer","outputs":[{"type":"bool"}],"type":"function"},
        {"inputs":[{"name":"spender","type":"address"},{"name":"value","type":"uint256"}],"name":"approve","outputs":[{"type":"bool"}],"type":"function"},
    ]
    token = w3.eth.contract(address=token_addr, abi=abi)
    tx = token.functions.transfer(recipient, amount_wei).build_transaction({
        "from": sender,
        "nonce": w3.eth.get_transaction_count(sender),
        "gas": 120000,
    })
    tx["gasPrice"] = w3.eth.gas_price
    return tx


def send_tx(w3, tx, privkey, dry_run):
    acct = Account.from_key(privkey)
    tx = dict(tx)  # copy
    tx.setdefault("from", acct.address)
    if dry_run:
        print("[DRY RUN] Transaction that would be sent:")
        safe = {k: (v.hex() if hasattr(v, 'hex') else v) for k, v in tx.items() if k != "data"}
        print(json.dumps(safe, indent=2, default=str))
        print("(data omitted for brevity)")
        return None
    signed = acct.sign_transaction(tx)
    txh = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"Transaction sent: {txh.hex()}")
    rec = w3.eth.wait_for_transaction_receipt(txh, timeout=300)
    print(f"Included in block {rec.blockNumber} | status={rec.status}")
    return txh.hex()


# ---------------- POLYMARKET BRIDGE ----------------

def polymarket_deposit(funder):
    r = requests.post(f"{BRIDGE_BASE}/deposit", json={"address": funder})
    r.raise_for_status()
    return r.json()


def polymarket_status(deposit_addr, timeout=1200):
    url = f"{BRIDGE_BASE}/status/{deposit_addr}"
    start = time.time()
    while time.time() - start < timeout:
        try:
            j = requests.get(url).json()
            txs = j.get("transactions") or []
            if txs:
                st = txs[0].get("status")
                print(f"  status: {st}")
                if st in ("COMPLETED", "FAILED"):
                    return j
        except Exception as e:
            pass
        time.sleep(10)
    return None


def run_polymarket(args):
    funder = args.funder or load_funder(args.env_file)
    print(f"Target funder (proxy): {funder}")

    dep = polymarket_deposit(funder)
    evm = dep["address"]["evm"]
    print(f"Send USDC (or token) on {args.chain} to this deposit address: {evm}")

    w3, cfg = get_w3(args.chain)
    token = resolve_token_address(args.token, args.chain, args.token_address)
    dec = get_decimals(w3, token)
    amt = int(Decimal(args.amount) * (10 ** dec))

    key = os.getenv("FUNDING_PRIVATE_KEY") or os.getenv("SOURCE_KEY")
    if not key:
        print("ERROR: Export FUNDING_PRIVATE_KEY with a key that holds the token + gas on the SOURCE chain.")
        sys.exit(1)

    sender = Account.from_key(key).address
    print(f"From: {sender} | amount (raw): {amt}")

    tx = build_transfer_tx(w3, token, sender, evm, amt)
    tx_hash = send_tx(w3, tx, key, args.dry_run)

    if args.dry_run:
        print("Dry-run complete.")
        return

    print("\nWaiting for Polymarket bridge to process...")
    res = polymarket_status(evm)
    print("Result:", json.dumps(res, indent=2) if res else "timeout")


# ---------------- DIRECT (Polygon or any chain) ----------------

def run_direct(args):
    w3, cfg = get_w3(args.chain)
    token = resolve_token_address(args.token, args.chain, args.token_address)
    dec = get_decimals(w3, token)
    amt = int(Decimal(args.amount) * (10 ** dec))

    recipient = Web3.to_checksum_address(args.to or DEFAULT_FUNDER)
    print(f"Direct {args.token} transfer on {args.chain} ({cfg['name']}) to {recipient}")

    key = os.getenv("POLYGON_KEY") or os.getenv("FUNDING_PRIVATE_KEY") or os.getenv("DIRECT_KEY")
    if not key:
        print("ERROR: Set POLYGON_KEY (or FUNDING_PRIVATE_KEY) for the sending wallet.")
        sys.exit(1)

    sender = Account.from_key(key).address
    tx = build_transfer_tx(w3, token, sender, recipient, amt)
    send_tx(w3, tx, key, args.dry_run)


# ---------------- ACROSS QUOTE HELPER ----------------

def run_across_quote(args):
    print("Across quote helper (then use 'direct' on Polygon).")
    token = resolve_token_address(args.token, args.chain, args.token_address)
    origin_id = CHAIN_CONFIG[args.chain]["id"]
    dest_id = 137  # Polygon

    params = {
        "inputToken": token,
        "outputToken": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",  # USDC.e on Polygon as example
        "originChainId": origin_id,
        "destinationChainId": dest_id,
        "amount": str(int(Decimal(args.amount) * 1_000_000)),  # 6 dec example
        "recipient": args.to or DEFAULT_FUNDER,
    }
    try:
        r = requests.get("https://api.across.to/suggested-fees", params=params, timeout=15)
        print("Suggested fees / quote (raw):")
        print(json.dumps(r.json() if r.ok else {"error": r.text}, indent=2))
    except Exception as e:
        print("Quote request failed (endpoint may have changed):", e)

    print("\nAfter a successful Across fill to your Polygon address, top up the proxy with:")
    print(f"  python bridge.py direct --chain polygon --token {args.token} --amount {args.amount} --to {params['recipient']} [--dry-run]")


def main():
    p = argparse.ArgumentParser(description="Programmatic bridging to Polymarket (and general ERC-20)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--env-file", default="/root/Ides-of-March/.env")
    p.add_argument("--funder", help="Override the target proxy/funder address")

    subs = p.add_subparsers(dest="command", required=True)

    # polymarket
    pm = subs.add_parser("polymarket", help="Polymarket Bridge API flow (recommended)")
    pm.add_argument("--chain", required=True, choices=list(CHAIN_CONFIG.keys()))
    pm.add_argument("--token", default="USDC")
    pm.add_argument("--token-address")
    pm.add_argument("--amount", required=True)
    pm.set_defaults(func=run_polymarket)

    # direct
    dr = subs.add_parser("direct", help="Direct ERC-20 send on a chain (after bridge to Polygon)")
    dr.add_argument("--chain", required=True, choices=list(CHAIN_CONFIG.keys()))
    dr.add_argument("--token", default="USDC")
    dr.add_argument("--token-address")
    dr.add_argument("--amount", required=True)
    dr.add_argument("--to", help="Recipient address (default = funder)")
    dr.set_defaults(func=run_direct)

    # across quote
    aq = subs.add_parser("across-quote", help="Get Across quote for source -> Polygon then direct")
    aq.add_argument("--chain", required=True, choices=list(CHAIN_CONFIG.keys()))
    aq.add_argument("--token", default="USDC")
    aq.add_argument("--token-address")
    aq.add_argument("--amount", required=True)
    aq.add_argument("--to")
    aq.set_defaults(func=run_across_quote)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
