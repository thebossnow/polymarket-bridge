#!/usr/bin/env python3
"""
polymarket-bridge

Polished CLI for bridging ERC-20 tokens (USDC focus) to Polymarket proxy wallets
and general direct transfers. Polygon is the main focus but other chains + arbitrary ERC-20s are supported.

Subcommands:
  polymarket     Polymarket Bridge API (source chain -> your proxy collateral)
  direct         Direct ERC-20 transfer on any chain (use after Across to Polygon etc.)
  across-quote   Get Across quote for source->Polygon, then finish with 'direct'

All support --dry-run, --amount, --chain, --token / --token-address, --funder, --env-file
"""

import argparse
import json
import os
import sys
import time
from decimal import Decimal

import requests
from eth_account import Account
from web3 import Web3

BRIDGE_BASE = "https://bridge.polymarket.com"
DEFAULT_FUNDER = "0x1D8593D2723a920fFE859De0Eef8b0f832aA6008"

CHAIN_CONFIG = {
    "base": {"id": 8453, "rpc": os.getenv("BASE_RPC", "https://mainnet.base.org"), "name": "Base", "explorer": "https://basescan.org"},
    "ethereum": {"id": 1, "rpc": os.getenv("ETH_RPC", "https://eth.llamarpc.com"), "name": "Ethereum", "explorer": "https://etherscan.io"},
    "arbitrum": {"id": 42161, "rpc": os.getenv("ARB_RPC", "https://arb1.arbitrum.io/rpc"), "name": "Arbitrum", "explorer": "https://arbiscan.io"},
    "polygon": {"id": 137, "rpc": os.getenv("POLYGON_RPC", "https://polygon-rpc.com"), "name": "Polygon", "explorer": "https://polygonscan.com"},
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
                if "POLYMARKET_FUNDER" in line:
                    val = line.split("=", 1)[1].strip().strip("'\"").split()[0]
                    if Web3.is_address(val):
                        return Web3.to_checksum_address(val)
    return DEFAULT_FUNDER


def resolve_token(token, chain, explicit=None):
    if explicit:
        return Web3.to_checksum_address(explicit)
    t = token.upper()
    if t in TOKEN_MAP and chain in TOKEN_MAP[t]:
        return TOKEN_MAP[t][chain]
    raise ValueError(f"Unknown token {token} on {chain}. Use --token-address")


def get_w3(chain):
    cfg = CHAIN_CONFIG[chain]
    w3 = Web3(Web3.HTTPProvider(cfg["rpc"]))
    if not w3.is_connected():
        raise RuntimeError(f"Cannot connect to {chain}")
    return w3, cfg


def fetch_decimals(w3, addr):
    abi = [{"constant": True, "inputs": [], "name": "decimals", "outputs": [{"type": "uint8"}], "type": "function"}]
    return w3.eth.contract(address=Web3.to_checksum_address(addr), abi=abi).functions.decimals().call()


def build_transfer_tx(w3, token_addr, sender, recipient, amount_wei):
    abi = [
        {"inputs": [{"name": "to", "type": "address"}, {"name": "value", "type": "uint256"}], "name": "transfer", "outputs": [{"type": "bool"}], "type": "function"},
        {"inputs": [{"name": "spender", "type": "address"}, {"name": "value", "type": "uint256"}], "name": "approve", "outputs": [{"type": "bool"}], "type": "function"},
    ]
    c = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=abi)
    tx = c.functions.transfer(recipient, amount_wei).build_transaction({
        "from": sender,
        "nonce": w3.eth.get_transaction_count(sender),
        "gas": 100000,
    })
    tx["gasPrice"] = w3.eth.gas_price
    return tx


def sign_and_send(w3, tx, privkey, dry_run):
    acct = Account.from_key(privkey)
    tx = dict(tx)
    tx["from"] = acct.address
    if dry_run:
        print("[DRY-RUN] Would send:")
        print(json.dumps({k: (v.hex() if hasattr(v,'hex') else v) for k,v in tx.items() if k != "data"}, indent=2, default=str))
        return None
    signed = acct.sign_transaction(tx)
    txh = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"Tx sent: {txh.hex()}")
    rec = w3.eth.wait_for_transaction_receipt(txh, timeout=300)
    print(f"Block {rec.blockNumber} status={rec.status}")
    return txh.hex()


# --- Polymarket Bridge ---

def pm_get_deposit(funder):
    r = requests.post(f"{BRIDGE_BASE}/deposit", json={"address": funder})
    r.raise_for_status()
    return r.json()


def pm_poll_status(deposit_addr, timeout=900):
    url = f"{BRIDGE_BASE}/status/{deposit_addr}"
    start = time.time()
    while time.time() - start < timeout:
        try:
            j = requests.get(url).json()
            if j.get("transactions"):
                st = j["transactions"][0].get("status")
                print(f"  status: {st}")
                if st in ("COMPLETED", "FAILED"):
                    return j
        except Exception:
            pass
        time.sleep(12)
    return None


def cmd_polymarket(args):
    funder = args.funder or load_funder(args.env_file)
    print(f"Funder/proxy: {funder}")
    dep = pm_get_deposit(funder)
    evm = dep["address"]["evm"]
    print(f"Send to this deposit address on {args.chain}: {evm}")

    w3, _ = get_w3(args.chain)
    tok = resolve_token(args.token, args.chain, args.token_address)
    dec = fetch_decimals(w3, tok)
    amt = int(Decimal(args.amount) * (10 ** dec))

    key = os.getenv("FUNDING_PRIVATE_KEY") or os.getenv("SOURCE_KEY")
    if not key:
        print("ERROR: Set FUNDING_PRIVATE_KEY (source chain key)")
        sys.exit(1)
    sender = Account.from_key(key).address
    print(f"From {sender}")

    tx = build_transfer_tx(w3, tok, sender, evm, amt)
    sign_and_send(w3, tx, key, args.dry_run)

    if not args.dry_run:
        print("\nPolling Polymarket bridge status...")
        res = pm_poll_status(evm)
        print("Result:", res)


# --- Direct transfer (any chain, any ERC20) ---

def cmd_direct(args):
    w3, _ = get_w3(args.chain)
    tok = resolve_token(args.token, args.chain, args.token_address)
    dec = fetch_decimals(w3, tok)
    amt = int(Decimal(args.amount) * (10 ** dec))
    to = Web3.to_checksum_address(args.to or (args.funder or load_funder(args.env_file)))
    print(f"Direct on {args.chain}: {amt} raw of {tok} -> {to}")

    key = os.getenv("POLYGON_KEY") or os.getenv("FUNDING_PRIVATE_KEY")
    if not key:
        print("ERROR: Set POLYGON_KEY or FUNDING_PRIVATE_KEY")
        sys.exit(1)
    sender = Account.from_key(key).address
    tx = build_transfer_tx(w3, tok, sender, to, amt)
    sign_and_send(w3, tx, key, args.dry_run)


# --- Across quote helper ---

def cmd_across_quote(args):
    print("Across suggested-fees (then use 'direct' on polygon)")
    tok = resolve_token(args.token, args.chain, args.token_address)
    params = {
        "inputToken": tok,
        "outputToken": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
        "originChainId": CHAIN_CONFIG[args.chain]["id"],
        "destinationChainId": 137,
        "amount": str(int(Decimal(args.amount) * 1000000)),
        "recipient": args.to or DEFAULT_FUNDER,
    }
    try:
        r = requests.get("https://api.across.to/suggested-fees", params=params, timeout=15)
        print(json.dumps(r.json() if r.ok else {"error": r.text}, indent=2))
    except Exception as e:
        print("(endpoint may have changed)", e)
    print(f"\nAfter fill: python bridge.py direct --chain polygon --token {args.token} --amount {args.amount} --to {params['recipient']}")


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dry-run", action="store_true")
    common.add_argument("--env-file", default="/root/Ides-of-March/.env")
    common.add_argument("--funder")

    p = argparse.ArgumentParser(description="Programmatic bridging tools for Polymarket")
    subs = p.add_subparsers(dest="cmd", required=True)

    pm = subs.add_parser("polymarket", parents=[common], help="Polymarket Bridge API")
    pm.add_argument("--chain", required=True, choices=list(CHAIN_CONFIG.keys()))
    pm.add_argument("--token", default="USDC")
    pm.add_argument("--token-address")
    pm.add_argument("--amount", required=True)
    pm.set_defaults(func=cmd_polymarket)

    dr = subs.add_parser("direct", parents=[common], help="Direct ERC-20 transfer on chain (after bridge)")
    dr.add_argument("--chain", required=True, choices=list(CHAIN_CONFIG.keys()))
    dr.add_argument("--token", default="USDC")
    dr.add_argument("--token-address")
    dr.add_argument("--amount", required=True)
    dr.add_argument("--to")
    dr.set_defaults(func=cmd_direct)

    aq = subs.add_parser("across-quote", parents=[common], help="Across quote helper -> use direct")
    aq.add_argument("--chain", required=True, choices=list(CHAIN_CONFIG.keys()))
    aq.add_argument("--token", default="USDC")
    aq.add_argument("--token-address")
    aq.add_argument("--amount", required=True)
    aq.add_argument("--to")
    aq.set_defaults(func=cmd_across_quote)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
