See previous README content plus updates.

# Updated Usage

## Polymarket (Bridge API)

```bash
python bridge.py polymarket --chain base --token USDC --amount 25.00 --dry-run
FUNDING_PRIVATE_KEY=0xyourkey python bridge.py polymarket --chain base --token USDC --amount 5 --env-file /root/Ides-of-March/.env
```

## Direct on Polygon (after Across or any bridge)

```bash
POLYGON_KEY=0x... python bridge.py direct --chain polygon --token USDC --amount 10 --dry-run
```

## Across quote helper

```bash
python bridge.py across-quote --chain base --token USDC --amount 100
```

Then use the `direct` command to finish moving from your Polygon landing address to the exact proxy.

Other tokens: `--token-address 0xYourErc20Addr --amount X`

The script auto-fetches decimals and builds safe transfers.

Repo also contains examples.md.
