# Examples

## Polymarket Bridge from Base (dry)

```bash
python bridge.py polymarket --chain base --token USDC --amount 10.00 --dry-run
```

## After landing on Polygon via Across, direct to funder

```bash
export POLYGON_KEY=0x...
python bridge.py direct --chain polygon --token USDC --amount 25 --dry-run
```

## Custom token on Arbitrum to your proxy via Polymarket bridge

```bash
python bridge.py polymarket --chain arbitrum --token-address 0xYourTokenHere --amount 100 --dry-run
```

## Check supported on Polymarket bridge

```bash
curl https://bridge.polymarket.com/supported-assets | jq
```
