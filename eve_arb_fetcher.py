"""
EVE Online Cross-Hub Arbitrage Finder
======================================
Fetches live market data from ESI for all 5 major trade hubs and
finds the best buy-low / sell-high opportunities across them.

Run with: python3 eve_arb_fetcher.py
Outputs:  eve_arb_data.json  (paste into the Claude artifact for analysis)

Requirements: pip install requests
"""

import requests
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Major Trade Hubs ──────────────────────────────────────────────────────────
HUBS = {
    "Jita":    {"region": 10000002, "station": 60003760},
    "Amarr":   {"region": 10000043, "station": 60008494},
    "Dodixie": {"region": 10000032, "station": 60011866},
    "Rens":    {"region": 10000030, "station": 60004588},
    "Hek":     {"region": 10000042, "station": 60005686},
}

ESI = "https://esi.evetech.net/latest"
FUZZ = "https://market.fuzzwork.co.uk/aggregates/"

# ── Item list: popular trade goods ───────────────────────────────────────────
# These are well-known liquid items. Add/remove type IDs as you like.
ITEM_IDS = [
    34, 35, 36, 37, 38, 39, 40,          # Tritanium → Zydrine (minerals)
    11399,                                 # Morphite
    16262, 16263, 16264, 16265,           # R4 moon goo
    16272, 16274, 16275, 16306,           # R8 moon goo
    16634, 16635, 16636, 16637, 16638,    # R16 moon goo
    16643, 16644, 16646, 16647, 16648,    # R32 moon goo
    16650, 16651, 16652, 16653,           # R64 moon goo
    34, 35, 36, 37, 38, 39, 40,           # minerals again (deduped below)
    9848, 9832,                            # Isogen-10, Megacyte
    44, 45,                                # Megacyte, Zydrine
    2267, 2268, 2269, 2270, 2272, 2274,  # Fuel blocks
    17864, 17865, 17866, 17867,           # Heavy water / Liquid ozone etc
    16274, 16275,
    35, 36,
]
ITEM_IDS = sorted(set(ITEM_IDS))


def get_type_names(type_ids):
    """Fetch item names from ESI in batches."""
    names = {}
    batch_size = 200
    for i in range(0, len(type_ids), batch_size):
        batch = type_ids[i:i+batch_size]
        try:
            r = requests.post(f"{ESI}/universe/names/", json=batch, timeout=10)
            for item in r.json():
                if item.get("category") == "inventory_type":
                    names[item["id"]] = item["name"]
        except Exception as e:
            print(f"  Name lookup error: {e}")
        time.sleep(0.1)
    return names


def get_fuzzwork_stats(type_ids, region_id):
    """Fetch aggregated buy/sell stats from Fuzzwork for a region."""
    ids_str = ",".join(str(i) for i in type_ids)
    url = f"{FUZZ}?region={region_id}&types={ids_str}"
    try:
        r = requests.get(url, timeout=15)
        return r.json()
    except Exception as e:
        print(f"  Fuzzwork error for region {region_id}: {e}")
        return {}


def fetch_hub_data(hub_name, hub_info, type_ids):
    print(f"  Fetching {hub_name} (region {hub_info['region']})...")
    data = get_fuzzwork_stats(type_ids, hub_info["region"])
    return hub_name, data


def find_arbitrage(hub_data, names, min_margin_pct=10, min_profit_per_unit=100_000):
    """
    For each item, find the hub with the lowest sell price (buy there)
    and the hub with the highest buy price (sell there).
    """
    opportunities = []

    # Collect all type_ids seen
    all_ids = set()
    for hub_name, items in hub_data.items():
        all_ids.update(int(k) for k in items.keys())

    for type_id in all_ids:
        tid = str(type_id)
        best_sell = None   # lowest sell (where to buy)
        best_buy  = None   # highest buy (where to sell)

        hub_prices = {}
        for hub_name, items in hub_data.items():
            if tid not in items:
                continue
            item = items[tid]
            sell = float(item.get("sell", {}).get("min", 0) or 0)
            buy  = float(item.get("buy",  {}).get("max", 0) or 0)
            vol  = float(item.get("sell", {}).get("volume", 0) or 0)
            if sell > 0:
                hub_prices[hub_name] = {"sell": sell, "buy": buy, "volume": vol}

        if len(hub_prices) < 2:
            continue

        sell_prices = {h: v["sell"] for h, v in hub_prices.items() if v["sell"] > 0}
        buy_prices  = {h: v["buy"]  for h, v in hub_prices.items() if v["buy"]  > 0}

        if not sell_prices or not buy_prices:
            continue

        cheapest_hub  = min(sell_prices, key=sell_prices.get)
        expensive_hub = max(buy_prices,  key=buy_prices.get)

        buy_at   = sell_prices[cheapest_hub]
        sell_at  = buy_prices[expensive_hub]

        if cheapest_hub == expensive_hub:
            continue
        if sell_at <= buy_at:
            continue

        profit   = sell_at - buy_at
        margin   = (profit / buy_at) * 100
        vol      = hub_prices.get(cheapest_hub, {}).get("volume", 0)

        if margin >= min_margin_pct and profit >= min_profit_per_unit:
            opportunities.append({
                "type_id":      type_id,
                "name":         names.get(type_id, f"Type {type_id}"),
                "buy_hub":      cheapest_hub,
                "buy_price":    round(buy_at, 2),
                "sell_hub":     expensive_hub,
                "sell_price":   round(sell_at, 2),
                "profit_per_unit": round(profit, 2),
                "margin_pct":   round(margin, 2),
                "sell_volume":  int(vol),
                "hub_prices":   {h: {"sell": v["sell"], "buy": v["buy"]} for h, v in hub_prices.items()},
            })

    opportunities.sort(key=lambda x: x["margin_pct"], reverse=True)
    return opportunities


def main():
    print("EVE Online Arbitrage Finder")
    print("=" * 40)
    print(f"Scanning {len(ITEM_IDS)} items across {len(HUBS)} hubs...\n")

    # Fetch names
    print("Resolving item names...")
    names = get_type_names(ITEM_IDS)
    print(f"  Got {len(names)} names\n")

    # Fetch market data from all hubs in parallel
    print("Fetching market data...")
    hub_data = {}
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {
            ex.submit(fetch_hub_data, name, info, ITEM_IDS): name
            for name, info in HUBS.items()
        }
        for future in as_completed(futures):
            hub_name, data = future.result()
            hub_data[hub_name] = data

    print(f"\nFinding arbitrage opportunities (≥10% margin, ≥100k ISK profit/unit)...")
    opps = find_arbitrage(hub_data, names)
    print(f"  Found {len(opps)} opportunities!\n")

    output = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "hubs": list(HUBS.keys()),
        "item_count": len(ITEM_IDS),
        "opportunities": opps,
        "all_hub_data_summary": {
            hub: len(items) for hub, items in hub_data.items()
        }
    }

    with open("eve_arb_data.json", "w") as f:
        json.dump(output, f, indent=2)

    print("Saved to eve_arb_data.json")
    print("\nTop 5 opportunities:")
    for o in opps[:5]:
        print(f"  {o['name']:<30} {o['buy_hub']:>8} → {o['sell_hub']:<8}  "
              f"{o['margin_pct']:6.1f}% margin  {o['profit_per_unit']:>12,.0f} ISK/unit")

    print("\nPaste the contents of eve_arb_data.json into the Claude analyzer artifact.")


if __name__ == "__main__":
    main()
