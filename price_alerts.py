import json
import os
import sys
from decimal import Decimal
from urllib.request import urlopen
from urllib.parse import urlencode

os.environ["TELEGRAM_STATE_DIR"] = "/var/lib/crypto-news"
import telegram_delivery as tg

LEVELS = {"ETHUSDT": [2200, 2150, 2100, 2050, 1950, 1900, 1850], "BTCUSDT": [75000, 70000]}

def main():
    check = "--check" in sys.argv
    ledger = tg.read("delivery.json", {})
    for symbol, levels in LEVELS.items():
        remaining = [
            level for level in levels
            if True
        ]
        if not remaining and not check:
            continue
        query = urlencode({"symbol": symbol})
        url = "https://api.binance.com/api/v3/ticker/price?" + query
        with urlopen(url, timeout=20) as response:
            data = json.load(response)
        price = Decimal(data["price"])
        if not price.is_finite() or price <= 0:
            raise ValueError("Invalid Binance price")
        print(symbol, price, "USDT", flush=True)
        for level in remaining:
            if price > level or check:
                continue
            key = f"price:{symbol}:{level}:{int(__import__('time').time()//1800)}"
            text = (
                f"Binance: {symbol} — {price} USDT\n"
                f"Цена достигла порога {level} USDT или ниже."
            )
            result = tg.send_digest({"text": text, "event_keys": [key]})
            if not result.get("sent"):
                raise RuntimeError("Delivery not confirmed")
            print("SENT", symbol, level, flush=True)

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("ERROR:", type(error).__name__, getattr(error, "code", ""))
        sys.exit(1)
