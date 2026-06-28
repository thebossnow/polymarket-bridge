#!/usr/bin/env python3
"""
polymarket-bridge

Polished CLI for bridging ERC-20 tokens (USDC focus) to Polymarket and general direct transfers.

Supports:
- Polymarket official Bridge API (source chain -> your proxy collateral)
- Direct ERC-20 transfers on any supported chain (use after Across or other bridges to Polygon)

Usage examples in README.
"""

import argparse
import os
import sys
import time
from decimal import Decimal

from eth_account import Account
import requests
from web3 import Web3

# Defaults for the temporal-arb setup
DEFAULT_FUNDER = "0x1D8593D2723a920fFE859De0Eef8b0f832aA6008"
BRIDGE_BASE = "https://bridge.polymarket.com"

# Chain config (add more as needed)
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
        "name": "Arbitrum One",
        "explorer": "https://arbiscan.io",
    },
    "polygon": {
        "id": 137,
        "rpc": os.getenv("POLYGON_RPC", "https://polygon-rpc.com"),
        "name": "Polygon",
        "explorer": "https://polygonscan.com",
    },
}

# Common token addresses (extend freely)
TOKEN_MAP = {
    "USDC": {
        "base": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "ethereum": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "arbitrum": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        "polygon": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",  # USDC.e (legacy for Polymarket)
    },
    # Add others e.g. "USDT", "WETH" with addresses per chain
}


def load_funder(env_file: str = None) -> str:
    if env_file and os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                if line.strip().startswith("POLYMARKET_FUNDER"):
                    val = line.split("=", 1)[1].strip().strip('"\'').split()[0]
                    if Web3.is_address(val):
                        return Web3.to_checksum_address(val)
    return DEFAULT_FUNDER


def get_token_address(token: str, chain: str, explicit_addr: str = None) -> str:
    if explicit_addr:
        return Web3.to_checksum_address(explicit_addr)
    token = token.upper()
    if token in TOKEN_MAP and chain in TOKEN_MAP[token]:
        return TOKEN_MAP[token][chain]
    raise ValueError(f"Unknown token {token} on {chain}. Use --token-address")


def get_web3(chain: str) -> Web3:
    if chain not in CHAIN_CONFIG:
        raise ValueError(f"Unsupported chain: {chain}. Add to CHAIN_CONFIG.")
    rpc = CHAIN_CONFIG[chain]["rpc"]
    w3 = Web3(Web3.HTTPProvider(rpc))
    if not w3.is_connected():
        raise RuntimeError(f"Cannot connect to {chain} RPC: {rpc}")
    return w3


def fetch_decimals(w3: Web3, token_addr: str) -> int:
    abi = [{"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"}]
    c = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=abi)
    return c.functions.decimals().call()


def build_erc20_transfer_tx(w3: Web3, token_addr: str, from_addr: str, to_addr: str, amount_wei: int, gas_price: int = None):
    abi = [
        {"constant": False, "inputs": [{"name": "_to", "type": "address"}, {"name": "_value", "type": "uint256"}], "name": "transfer", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
        {"constant": False, "inputs": [{"name": "_spender", "type": "address"}, {"name": "_value", "type": "uint256"}], "name": "approve", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
    ]
    token = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=abi)

    # Simple transfer (assume allowance or user handles approve separately for simplicity in direct mode)
    # For full safety we can add approve if needed in the caller.
    tx = token.functions.transfer(to_addr, amount_wei).build_transaction({
        "from": from_addr,
        "nonce": w3.eth.get_transaction_count(from_addr),
        "gas": 100000,
    })
    if gas_price is None:
        tx["gasPrice"] = w3.eth.gas_price
    else:
        tx["gasPrice"] = gas_price
    return tx


def sign_and_send(w3: Web3, tx: dict, privkey: str, dry_run: bool = False):
    acct = Account.from_key(privkey)
    if tx.get("from") != acct.address:
        tx["from"] = acct.address
    if dry_run:
        print("[DRY-RUN] Would send transaction:")
        print(json.dumps({k: v for k, v in tx.items() if k != "data"}, indent=2))
        print("Data len:", len(tx.get("data", "0x")))
        return None
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"Tx sent: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
    print(f"Confirmed in block {receipt.blockNumber}, status: {receipt.status}")
    return tx_hash.hex()


# ============ POLYMARKET BRIDGE ============

def polymarket_get_deposit(funder: str):
    resp = requests.post(
        f"{BRIDGE_BASE}/deposit",
        json={"address": funder},
        headers={"Content-Type": "application/json"}
    )
    resp.raise_for_status()
    data = resp.json()
    return data


def polymarket_poll_status(deposit_addr: str, timeout: int = 1800):
    url = f"{BRIDGE_BASE}/status/{deposit_addr}"
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(url)
            data = r.json()
            txs = data.get("transactions") or []
            if txs:
                status = txs[0].get("status", "UNKNOWN")
                print(f"Bridge status: {status}")
                if status in ("COMPLETED", "FAILED"):
                    return data
            else:
                print("No deposit detected yet...")
        except Exception as e:
            print("Status check error:", e)
        time.sleep(15)
    print("Timeout waiting for bridge completion")
    return None


def cmd_polymarket(args):
    funder = args.funder or load_funder(args.env_file)
    print(f"Using funder: {funder}")

    deposit_data = polymarket_get_deposit(funder)
    evm_deposit = deposit_data["address"]["evm"]
    print(f"Polymarket EVM deposit address on source: {evm_deposit}")

    token_addr = get_token_address(args.token, args.chain, args.token_address)
    print(f"Token address on {args.chain}: {token_addr}")

    w3 = get_web3(args.chain)
    decimals = fetch_decimals(w3, token_addr)
    amount_wei = int(Decimal(args.amount) * (10 ** decimals))

    privkey = os.environ.get("FUNDING_PRIVATE_KEY") or os.environ.get("SOURCE_PRIVATE_KEY")
    if not privkey:
        print("ERROR: Set FUNDING_PRIVATE_KEY (or SOURCE_PRIVATE_KEY) in env for the SOURCE chain wallet.")
        sys.exit(1)

    from_addr = Account.from_key(privkey).address
    print(f"Sending from {from_addr} on {args.chain}")

    # Build simple transfer (for production consider full approve flow if needed)
    tx = build_erc20_transfer_tx(w3, token_addr, from_addr, evm_deposit, amount_wei)

    tx_hash = sign_and_send(w3, tx, privkey, dry_run=args.dry_run)

    if args.dry_run or not tx_hash:
        print("Dry run complete. No funds moved.")
        return

    print("\nDeposit sent. Polling Polymarket bridge status...")
    status = polymarket_poll_status(evm_deposit)
    print("Final status:", status)

    if status and status.get("transactions"):
        print("Success! Funds should be available in your proxy shortly.")
        print("Check with: python auth_check.py or your bot balance call.")


# ============ DIRECT TRANSFER (for Polygon focus or after any bridge) ============

def cmd_direct(args):
    w3 = get_web3(args.chain)
    token_addr = get_token_address(args.token, args.chain, args.token_address)
    decimals = fetch_decimals(w3, token_addr)
    amount_wei = int(Decimal(args.amount) * (10 ** decimals))

    to_addr = Web3.to_checksum_address(args.to or DEFAULT_FUNDER)
    print(f"Direct transfer on {args.chain}: {amount_wei} of {token_addr} -> {to_addr}")

    privkey = os.environ.get("POLYGON_KEY") or os.environ.get("FUNDING_PRIVATE_KEY") or os.environ.get("DIRECT_PRIVATE_KEY")
    if not privkey:
        print("ERROR: Set POLYGON_KEY or FUNDING_PRIVATE_KEY for the sending wallet on the target chain.")
        sys.exit(1)

    from_addr = Account.from_key(privkey).address

    tx = build_erc20_transfer_tx(w3, token_addr, from_addr, to_addr, amount_wei)
    sign_and_send(w3, tx, privkey, dry_run=args.dry_run)


# ============ ACROSS HELPER (quote + notes for full bridge then direct) ============

def cmd_across_quote(args):
    # Simple Across quote helper. Full deposit execution is advanced (SpokePool).
    # After successful Across fill to Polygon, use `direct` command to send to funder.
    print("Across quote helper (Polygon dest recommended).")
    # Example endpoint (check current Across docs for exact)
    params = {
        "token": get_token_address(args.token, args.chain, args.token_address),
        "originChainId": CHAIN_CONFIG[args.chain]["id"],
        "destinationChainId": 137,  # Polygon
        "amount": str(int(Decimal(args.amount) * 10**6)),  # assume 6 dec for demo; script can improve
        "recipient": args.to or DEFAULT_FUNDER,
    }
    try:
        r = requests.get("https://api.across.to/suggested-fees", params=params)
        print("Quote response (example):", r.json() if r.ok else r.text)
    except Exception as e:
        print("Quote call (may need updated endpoint):", e)
    print("\nAfter Across delivers to your Polygon wallet, run:")
    print(f"  python bridge.py direct --chain polygon --token {args.token} --amount {args.amount} --to {params['recipient']} ")


def main():
    parser = argparse.ArgumentParser(description="Polymarket and general ERC-20 bridge/funding CLI")
    parser.add_argument("--dry-run", action="store_true", help="Build txs but do not send")
    parser.add_argument("--env-file", default="/root/Ides-of-March/.env", help="Path to .env for POLYMARKET_FUNDER")
    parser.add_argument("--funder", help="Override funder/proxy address")

    subparsers = parser.add_subparsers(dest="cmd", required=True)

    # polymarket
    p1 = subparsers.add_parser("polymarket", help="Use Polymarket Bridge API (source -> your proxy)")
    p1.add_argument("--chain", required=True, choices=list(CHAIN_CONFIG.keys()), help="Source chain")
    p1.add_argument("--token", default="USDC", help="Token symbol (USDC etc)")
    p1.add_argument("--token-address", help="Override token contract address")
    p1.add_argument("--amount", required=True, help="Amount in human units, e.g. 25.5")
    p1.set_defaults(func=cmd_polymarket)

    # direct
    p2 = subparsers.add_parser("direct", help="Direct ERC-20 transfer on a chain (great after Across to Polygon)")
    p2.add_argument("--chain", required=True, choices=list(CHAIN_CONFIG.keys()))
    p2.add_argument("--token", default="USDC")
    p2.add_argument("--token-address", help="Override token address")
    p2.add_argument("--amount", required=True)
    p2.add_argument("--to", help="Recipient (defaults to funder)")
    p2.set_defaults(func=cmd_direct)

    # across quote helper
    p3 = subparsers.add_parser("across-quote", help="Get Across quote info for source -> Polygon, then use direct")
    p3.add_argument("--chain", required=True, choices=list(CHAIN_CONFIG.keys()), help="Source chain")
    p3.add_argument("--token", default="USDC")
    p3.add_argument("--token-address", help="Override")
    p3.add_argument("--amount", required=True)
    p3.add_argument("--to", default=DEFAULT_FUNDER)
    p3.set_defaults(func=cmd_across_quote)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
