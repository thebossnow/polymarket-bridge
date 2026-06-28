# polymarket-bridge

Polished CLI tools for programmatically funding Polymarket proxy wallets and general ERC-20 transfers (Polygon focus).

## Features
- **Polymarket Bridge mode**: Use Polymarket's official bridge API to send from Base/Ethereum/Arbitrum/etc. directly to your proxy (auto converts to collateral).
- **Direct mode**: Send any ERC-20 on Polygon (or other chains) to any address (e.g. your funder after bridging with Across or elsewhere).
- Supports USDC and arbitrary ERC-20 tokens (by symbol or address).
- Full --dry-run support, tx preview, status polling.
- CLI flags: --amount, --chain (source), --token, --token-address, --dry-run, --env-file, --funder

## Installation

```bash
git clone https://github.com/thebossnow/polymarket-bridge.git
cd polymarket-bridge
pip install -r requirements.txt
```

Or drop on your VPS:

```bash
git clone https://github.com/thebossnow/polymarket-bridge.git /root/polymarket-bridge
```

## Usage

### Polymarket Bridge (recommended for direct to proxy)

```bash
# Dry run from Base
python bridge.py polymarket --chain base --token USDC --amount 25.00 --dry-run

# Real send (requires funding key on source chain with USDC + gas)
FUNDING_PRIVATE_KEY=0x... python bridge.py polymarket --chain base --token USDC --amount 10.00

# Custom token
python bridge.py polymarket --chain base --token-address 0x... --amount 100 --dry-run
```

After success, funds are bridged and converted to collateral for your `POLYMARKET_FUNDER`.

Poll status automatically or manually:
```bash
curl https://bridge.polymarket.com/status/<deposit-addr>
```

### Direct transfer on Polygon (or any chain) - use after any bridge

Use this after bridging with Across (or any method) to land on a Polygon funding wallet, then send to the proxy.

```bash
# From Polygon funding wallet to your Polymarket funder
POLYGON_KEY=0x... python bridge.py direct --chain polygon --token USDC --amount 25.00 --to 0x1D8593D2723a920fFE859De0Eef8b0f832aA6008 --dry-run

# Arbitrary ERC20 on any supported chain
python bridge.py direct --chain base --token-address 0xYourToken --amount 100 --to 0xRecipient --dry-run
```

## Environment

- `FUNDING_PRIVATE_KEY` or `POLYGON_KEY` etc for the source chain wallet.
- `--env-file` to load `POLYMARKET_FUNDER` automatically (defaults to the known one for temporal-arb).

## Supported

Common chains: base, ethereum, arbitrum, polygon.
Tokens: USDC (with aliases), or any via `--token-address`.

For other tokens/chains, provide `--token-address` and the script fetches decimals automatically.

## Testing

Both paths support `--dry-run` which calls APIs and builds transactions without sending.

See `python bridge.py --help` and subcommand help.

## Safety

- Always use `--dry-run` first.
- Test with small amounts.
- The script never sends unless you explicitly run without --dry-run and provide keys.
- Private keys are only used locally for signing.

## How it works

**Polymarket mode**: Calls `POST /deposit` to get a per-wallet deposit address on the source chain, sends the token to it. Polymarket handles the bridge and onramp to your proxy as collateral.

**Direct mode**: Simple ERC-20 transfer on the specified chain to the target address (perfect for landing on Polygon via Across then topping the exact proxy).

Polygon is the focus because that's where Polymarket collateral lives for the proxy wallets.

## Extending

Add more chains/tokens in the config inside the script.

PRs welcome!
