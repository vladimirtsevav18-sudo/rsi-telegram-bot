#!/usr/bin/env python3
"""Hourly CoinMarketCap TOP-100 RSI scanner with Telegram delivery."""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


CMC_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
BYBIT_URL = "https://api.bybit.com/v5/market"
TELEGRAM_URL = "https://api.telegram.org"


def load_dotenv(path: str = ".env") -> None:
    file = Path(path)
    if not file.exists():
        return
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("\"").strip("'")
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Config:
    cmc_api_key: str
    telegram_bot_token: str
    telegram_chat_id: str
    min_volume_usd: float = 20_000_000
    rsi_period: int = 14
    rsi_low: float = 30
    rsi_high: float = 70
    top_n: int = 100
    request_pause: float = 0.07

    @classmethod
    def from_env(cls) -> "Config":
        missing = [
            name
            for name in ("CMC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
            if not os.getenv(name)
        ]
        if missing:
            raise ValueError("Не заполнены переменные: " + ", ".join(missing))
        return cls(
            cmc_api_key=os.environ["CMC_API_KEY"],
            telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
            telegram_chat_id=os.environ["TELEGRAM_CHAT_ID"],
            min_volume_usd=float(os.getenv("MIN_VOLUME_USD", "20000000")),
            rsi_period=int(os.getenv("RSI_PERIOD", "14")),
            rsi_low=float(os.getenv("RSI_LOW", "30")),
            rsi_high=float(os.getenv("RSI_HIGH", "70")),
            top_n=int(os.getenv("TOP_N", "100")),
            request_pause=float(os.getenv("REQUEST_PAUSE", "0.07")),
        )


def api_json(url: str, params: dict[str, Any] | None = None,
             headers: dict[str, str] | None = None, retries: int = 3) -> dict[str, Any]:
    if params:
        url = f"{url}?{urlencode(params)}"
    request = Request(url, headers=headers or {})
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=25) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 429 and attempt + 1 < retries:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"HTTP {exc.code}: {body[:300]}") from exc
        except (URLError, TimeoutError) as exc:
            if attempt + 1 == retries:
                raise RuntimeError(f"Ошибка сети: {exc}") from exc
            time.sleep(2 ** attempt)
    raise RuntimeError("Запрос не выполнен")


def get_cmc_top(config: Config) -> list[dict[str, Any]]:
    payload = api_json(
        CMC_URL,
        {"start": 1, "limit": config.top_n, "convert": "USD", "sort": "market_cap"},
        {"X-CMC_PRO_API_KEY": config.cmc_api_key, "Accept": "application/json"},
    )
    if payload.get("status", {}).get("error_code"):
        raise RuntimeError(payload["status"].get("error_message", "Ошибка CoinMarketCap"))
    result = []
    for coin in payload.get("data", []):
        quote = coin.get("quote", {}).get("USD", {})
        volume = float(quote.get("volume_24h") or 0)
        if volume > config.min_volume_usd:
            result.append({
                "rank": int(coin.get("cmc_rank") or 0),
                "name": coin.get("name", ""),
                "symbol": coin.get("symbol", "").upper(),
                "volume": volume,
            })
    return result


def get_bybit_spot_symbols() -> set[str]:
    symbols: set[str] = set()
    cursor = ""
    while True:
        params: dict[str, Any] = {"category": "spot", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        payload = api_json(f"{BYBIT_URL}/instruments-info", params)
        if payload.get("retCode") != 0:
            raise RuntimeError(payload.get("retMsg", "Ошибка Bybit instruments-info"))
        result = payload.get("result", {})
        symbols.update(
            item["symbol"] for item in result.get("list", [])
            if item.get("status") == "Trading" and item.get("quoteCoin") == "USDT"
        )
        cursor = result.get("nextPageCursor") or ""
        if not cursor:
            return symbols


def get_closed_hourly_closes(symbol: str, limit: int = 250, cutoff: int | None = None) -> list[float]:
    if cutoff is None:
        cutoff = int(time.time() // 3600) * 3600000
    payload = api_json(
        f"{BYBIT_URL}/kline",
        {"category": "spot", "symbol": symbol, "interval": "60", "limit": limit, "end": cutoff - 1},
    )
    if payload.get("retCode") != 0:
        raise RuntimeError(payload.get("retMsg", f"Ошибка Bybit для {symbol}"))
    current_hour_ms = cutoff
    candles = [row for row in payload.get("result", {}).get("list", []) if int(row[0]) < current_hour_ms]
    candles.sort(key=lambda row: int(row[0]))
    if len(candles) < 200 or int(candles[-1][0]) != cutoff - 3600000:
        raise ValueError("Недостаточно истории или нет последней закрытой свечи")
    if any(int(b[0]) - int(a[0]) != 3600000 for a, b in zip(candles, candles[1:])):
        raise ValueError("Пропуск или повтор часовой свечи")
    return [float(row[4]) for row in candles]


def wilder_rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        raise ValueError(f"Для RSI({period}) нужно минимум {period + 1} закрытий")
    changes = [b - a for a, b in zip(closes, closes[1:])]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def scan(config: Config) -> tuple[list[dict[str, Any]], list[str]]:
    cutoff = int(time.time() // 3600) * 3600000
    coins = get_cmc_top(config)
    bybit_symbols = get_bybit_spot_symbols()
    signals: list[dict[str, Any]] = []
    skipped: list[str] = []
    checked = 0
    errors = 0
    for coin in coins:
        pair = f"{coin['symbol']}USDT"
        if pair not in bybit_symbols:
            skipped.append(coin["symbol"])
            continue
        try:
            value = wilder_rsi(get_closed_hourly_closes(pair, cutoff=cutoff), config.rsi_period)
            checked += 1
            if value <= config.rsi_low or value >= config.rsi_high:
                signals.append({**coin, "pair": pair, "rsi": value})
        except Exception as exc:  # One missing market must not abort the hourly scan.
            errors += 1
            logging.warning("%s пропущен: %s", pair, exc)
            skipped.append(coin["symbol"])
        time.sleep(config.request_pause)
    logging.info("Прошли объём: %d; проверено: %d; пропущено: %d; ошибок: %d", len(coins), checked, len(skipped), errors)
    if coins and checked == 0:
        raise RuntimeError("Ни одна монета не проверена: проверьте доступ к Bybit и наличие пар")
    signals.sort(key=lambda item: item["rsi"])
    return signals, skipped


def format_volume(value: float) -> str:
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.1f}B"
    return f"${value / 1_000_000:.0f}M"


def build_messages(signals: list[dict[str, Any]], config: Config, skipped: list[str]) -> list[str]:
    stamp = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    header = (
        f"<b>RSI-сканер TOP-{config.top_n}</b>\n"
        f"1H · RSI({config.rsi_period}) · закрытые свечи\n"
        f"Объём 24ч &gt; {format_volume(config.min_volume_usd)}\n"
        f"{stamp}\n"
    )
    if not signals:
        return [header + f"\nСреди проверенных монет сигналов RSI ≤ {config.rsi_low:g} или RSI ≥ {config.rsi_high:g} нет.\nПропущено без пары/данных: {len(skipped)}.\nИсточник свечей: Bybit Spot."]
    lines = []
    for item in signals:
        marker = "🔻" if item["rsi"] <= config.rsi_low else "🔺"
        lines.append(
            f"{marker} <b>{html.escape(item['symbol'])}</b> — RSI {item['rsi']:.2f} "
            f"· #{item['rank']} · {format_volume(item['volume'])}"
        )
    footer = f"\nИсточник свечей: Bybit Spot. Пропущено без пары/данных: {len(skipped)}."
    messages, current = [], header
    for line in lines:
        if len(current) + len(line) + len(footer) + 2 > 3900:
            messages.append(current.rstrip())
            current = "<b>Продолжение RSI-сканера</b>\n"
        current += "\n" + line
    messages.append(current + footer)
    return messages


def telegram_call(token: str, method: str, data: dict[str, Any]) -> dict[str, Any]:
    encoded = urlencode(data).encode("utf-8")
    request = Request(f"{TELEGRAM_URL}/bot{token}/{method}", data=encoded)
    try:
        with urlopen(request, timeout=25) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            details = json.loads(exc.read().decode("utf-8"))
            description = str(details.get("description", "Ответ без описания"))
        except (ValueError, UnicodeError):
            description = "Ответ не в формате Telegram JSON"
        for secret in (token, os.getenv("CMC_API_KEY", "")):
            if secret:
                description = description.replace(secret, "[REDACTED]")
        raise RuntimeError(f"Telegram {method}: HTTP {exc.code}: {description[:400]}") from None
    if not payload.get("ok"):
        raise RuntimeError("Ошибка Telegram: " + str(payload.get("error_code", "unknown")))
    return payload


def send_messages(config: Config, messages: list[str]) -> None:
    for message in messages:
        telegram_call(config.telegram_bot_token, "sendMessage", {
            "chat_id": config.telegram_chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        })


def run_once(config: Config, send: bool = True) -> list[dict[str, Any]]:
    logging.info("Запускаю RSI-сканирование")
    signals, skipped = scan(config)
    messages = build_messages(signals, config, skipped)
    if send:
        send_messages(config, messages)
        logging.info("Отправлено сообщений: %d; сигналов: %d", len(messages), len(signals))
    else:
        print("\n\n".join(messages))
    return signals


def seconds_until_next_hour_minute_one() -> float:
    now = datetime.now(timezone.utc)
    next_hour = now.replace(minute=1, second=0, microsecond=0)
    if next_hour <= now:
        next_hour = next_hour.replace(hour=(next_hour.hour + 1) % 24)
        if next_hour.hour == 0:
            from datetime import timedelta
            next_hour = (now + timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
    return (next_hour - now).total_seconds()


def main() -> int:
    parser = argparse.ArgumentParser(description="CMC TOP-100 / Bybit RSI(14) 1H Telegram scanner")
    parser.add_argument("--once", action="store_true", help="Один запуск и завершение")
    parser.add_argument("--dry-run", action="store_true", help="Не отправлять в Telegram, вывести результат")
    parser.add_argument("--test-telegram", action="store_true", help="Отправить тестовое сообщение")
    parser.add_argument("--show-chat-id", action="store_true", help="Показать chat_id после сообщения боту")
    args = parser.parse_args()
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.show_chat_id:
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            raise ValueError("Сначала заполните TELEGRAM_BOT_TOKEN в .env")
        result = telegram_call(token, "getUpdates", {})
        found = {(item.get("message") or item.get("channel_post") or {}).get("chat", {}).get("id")
                 for item in result.get("result", [])}
        print("Найденные chat_id:", ", ".join(map(str, filter(None, found))) or "нет; сначала отправьте боту /start")
        return 0

    config = Config.from_env()
    if args.test_telegram:
        send_messages(config, ["✅ RSI-бот подключён к Telegram."])
        print("Тестовое сообщение отправлено.")
        return 0
    if args.once or args.dry_run:
        run_once(config, send=not args.dry_run)
        return 0

    logging.info("Бот запущен. Сканирование каждый час в HH:01 UTC.")
    while True:
        wait = seconds_until_next_hour_minute_one()
        logging.info("Следующий запуск через %.0f сек.", wait)
        time.sleep(wait)
        try:
            run_once(config)
        except Exception:
            logging.exception("Сканирование завершилось ошибкой; повтор через час")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        logging.error("%s", exc)
        raise SystemExit(1)
