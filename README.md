
# File: README.md
```md
# CCXT Gate.io Webhook (Dockerized)

This project implements a small FastAPI server that accepts TradingView webhooks and places orders on Gate.io via CCXT. It logs orders to a CSV and posts the order data to a remote server.

## Features
- Retry logic with configurable retries and delay (`MAX_RETRIES`, `RETRY_DELAY`)
- Partial fill detection using `fetch_order`
- CSV logging of all orders with filled/remaining amounts
- Remote server notification (optional)

## Quickstart

1. Copy `.env.example` to `.env` and fill in your Gate.io API keys and REMOTE_NOTIFY_URL.
2. Build and run with Docker Compose:

```bash
docker-compose up --build -d
```

3. Expose `http://<host>:5000/webhook` as your TradingView Webhook URL.

4. Example TradingView alert payload (JSON):

```json
{
  "symbol": "BTC_USDT",
  "action": "BUY",
  "quantity": 0.001,
  "order_type": "market"
}
```

## Notes
- Add authentication for webhooks before production.
- Tune retry settings depending on API reliability.
- Monitor for partial fills and adjust strategy accordingly.
```