from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel
import ccxt
import os
from datetime import datetime, timezone
import csv
import asyncio
import httpx
from dotenv import load_dotenv
import time
import gspread
from google.oauth2.service_account import Credentials

load_dotenv()

GATE_API_KEY = os.getenv('GATEIO_API_KEY')
GATE_API_SECRET = os.getenv('GATEIO_API_SECRET')
REMOTE_NOTIFY_URL = os.getenv('REMOTE_NOTIFY_URL')
CSV_PATH = os.getenv('CSV_PATH', 'orders.csv')
MAX_RETRIES = int(os.getenv('MAX_RETRIES', 3))
RETRY_DELAY = int(os.getenv('RETRY_DELAY', 2))

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT", "/app/credentials/service_account.json")
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")

creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
gclient = gspread.authorize(creds)
sheet = gclient.open_by_key(SHEET_ID).sheet1

def log_order_to_gsheet(order: dict):
    row = [
        datetime.now(timezone.utc).isoformat(),
        order.get('id'),
        order.get('symbol'),
        order.get('side'),
        order.get('type'),
        order.get('price'),
        order.get('amount'),
        order.get('status'),
        order.get('filled'),
        order.get('remaining'),
        str(order.get('info') or '')
    ]
    sheet.append_row(row, value_input_option="RAW")

if not GATE_API_KEY or not GATE_API_SECRET:
    raise RuntimeError('Missing Gate.io API credentials in environment')

exchange = ccxt.gateio({
    'apiKey': GATE_API_KEY,
    'secret': GATE_API_SECRET,
    'enableRateLimit': True,
    'options': {
        'createMarketBuyOrderRequiresPrice': False # allow passing cost directly
    }
})

if os.getenv("GATEIO_SANDBOX", "false").lower() == "true":
    exchange.set_sandbox_mode(True)
    
app = FastAPI(title='TradingView -> Gate.io webhook')

class TVPayload(BaseModel):
    symbol: str
    action: str  # BUY or SELL
    quantity: float
    order_type: str = 'market'
    price: float | None = None
    client_id: str | None = None


def normalize_symbol(s: str) -> str:
    if '/' in s:
        return s
    return s.replace('_', '/').upper()


def place_order_with_retry(symbol: str, side: str, amount: float, order_type: str = 'market', price: float | None = None):
    print(f"Placing order: {side} {amount} {symbol} as {order_type} at {price}")
    last_exception = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            exchange.load_markets()
            if order_type == 'market':
                if side == 'buy':
                    order = exchange.create_market_buy_order(symbol, amount)
                else:
                    order = exchange.create_market_sell_order(symbol, amount)
            elif order_type == 'limit':
                if price is None:
                    raise ValueError('Missing price for limit order')
                if side == 'buy':
                    order = exchange.create_limit_buy_order(symbol, amount, price)
                else:
                    order = exchange.create_limit_sell_order(symbol, amount, price)
            else:
                raise ValueError('Unsupported order type')

            # Check for partial fill
            if order.get('status') in ('open', 'partial'):
                order = exchange.fetch_order(order['id'], symbol)
            return order

        except Exception as e:
            last_exception = e
            print(f"Order attempt {attempt} failed: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
    raise last_exception


def log_order(order: dict):
    header = ['timestamp_utc', 'id', 'symbol', 'side', 'type', 'price', 'amount', 'status', 'filled', 'remaining', 'info']
    exists = os.path.exists(CSV_PATH)
    with open(CSV_PATH, mode='a', newline='') as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(header)
        writer.writerow([
            datetime.now(timezone.utc).isoformat(),
            order.get('id'),
            order.get('symbol'),
            order.get('side'),
            order.get('type'),
            order.get('price'),
            order.get('amount'),
            order.get('status'),
            order.get('filled'),
            order.get('remaining'),
            str(order.get('info') or '')
        ])


async def notify_remote(order: dict):
    if not REMOTE_NOTIFY_URL:
        return
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(REMOTE_NOTIFY_URL, json=order, timeout=10.0)
            resp.raise_for_status()
        except Exception as e:
            print('Failed to notify remote:', e)


@app.post('/webhook')
async def webhook(payload: TVPayload, background_tasks: BackgroundTasks):
    symbol = normalize_symbol(payload.symbol)
    side = payload.action.strip().lower()
    if side not in ('buy', 'sell'):
        raise HTTPException(status_code=400, detail='action must be BUY or SELL')

    try:
        order = await asyncio.to_thread(
            place_order_with_retry,
            symbol, side, payload.quantity, payload.order_type, payload.price
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Order placement failed: {e}')

    background_tasks.add_task(log_order, order)
    background_tasks.add_task(log_order_to_gsheet, order)
    background_tasks.add_task(notify_remote, order)

    return {'status': 'ok', 'order_id': order.get('id'), 'raw': order}