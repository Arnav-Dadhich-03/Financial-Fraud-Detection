"""
app.py
------
FastAPI service for the Financial Fraud Detection project.

Endpoints:
  GET  /                -> the live monitoring dashboard (HTML)
  POST /predict          -> score a single transaction (the original, classic endpoint)
  WS   /ws/stream         -> live, real-time transaction feed: each message is a
                             transaction scored by the model the instant it "arrives"
  GET  /api/stats         -> current snapshot of running analytics (for first paint)
  POST /api/reset         -> reset the in-memory analytics counters

The live stream replays a held-out, labeled pool of transactions produced by
fraud_detection_pipeline.py (stream_data.pkl), oversampled for fraud so the
dashboard actually demonstrates the model catching something. Each transaction
is scored by the real model in real time -- nothing about the prediction is
canned. A few cosmetic fields (merchant, city, card network) are randomly
attached purely for UI flavor, since the underlying V1-V28 features are
anonymized/PCA-style and carry no real merchant data.
"""

import asyncio
import pickle
import random
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Optional

import os

import numpy as np
import pandas as pd
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, create_model

BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = BASE_DIR / "fraud_model.pkl"
STREAM_DATA_PATH = BASE_DIR / "stream_data.pkl"

app = FastAPI(title="Financial Fraud Detection API", version="2.0.0")

# Allow the dashboard to be hosted on a different domain than the API
# (e.g. a static frontend on Netlify calling a backend on Render).
# Set ALLOWED_ORIGINS as a comma-separated list in production instead of "*".
_allowed_origins = os.getenv("ALLOWED_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _allowed_origins == "*" else _allowed_origins.split(","),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# ---------------------------------------------------------------------------
# Load model + stream pool at startup
# ---------------------------------------------------------------------------
with open(MODEL_PATH, "rb") as f:
    _bundle = pickle.load(f)
    MODEL = _bundle["model"]
    FEATURE_COLUMNS = _bundle["feature_columns"]

with open(STREAM_DATA_PATH, "rb") as f:
    STREAM_POOL: pd.DataFrame = pickle.load(f)

# Cosmetic-only metadata for the live feed (model never sees these)
MERCHANT_CATEGORIES = [
    "Electronics", "Grocery", "Travel", "Online Retail", "Restaurants",
    "Fuel Station", "Subscription", "ATM Withdrawal", "Jewelry", "Utilities",
]
CITIES = [
    "New York, US", "London, UK", "Mumbai, IN", "Singapore, SG", "Toronto, CA",
    "Sydney, AU", "Berlin, DE", "Tokyo, JP", "Dubai, AE", "Jaipur, IN",
]
CARD_NETWORKS = ["Visa", "Mastercard", "Amex", "Discover"]

# ---------------------------------------------------------------------------
# Pydantic schema for POST /predict, built dynamically from FEATURE_COLUMNS
# so it always matches whatever the pipeline trained on.
# ---------------------------------------------------------------------------
_predict_fields = {col: (float, Field(..., description=f"Feature: {col}")) for col in FEATURE_COLUMNS}
TransactionIn = create_model("TransactionIn", **_predict_fields)


class PredictionOut(BaseModel):
    status: str
    is_fraud: bool
    fraud_probability: float


def score_row(row: dict) -> PredictionOut:
    x = pd.DataFrame([[row[col] for col in FEATURE_COLUMNS]], columns=FEATURE_COLUMNS)
    proba = float(MODEL.predict_proba(x)[0, 1])
    is_fraud = proba >= 0.5
    return PredictionOut(
        status="ALERT" if is_fraud else "Approved",
        is_fraud=is_fraud,
        fraud_probability=round(proba, 4),
    )


@app.post("/predict", response_model=PredictionOut)
def predict(transaction: TransactionIn):
    """Score a single transaction. Mirrors the original microservice's
    classic synchronous endpoint -- handy for curl / Swagger / integration
    testing alongside the live dashboard."""
    return score_row(transaction.dict())


# ---------------------------------------------------------------------------
# In-memory live analytics (reset on restart, or via /api/reset)
# ---------------------------------------------------------------------------
class Analytics:
    def __init__(self):
        self.total = 0
        self.fraud = 0
        self.amount_sum = 0.0
        self.correct = 0
        self.recent_alerts = deque(maxlen=25)
        self.timeline = deque(maxlen=60)  # rolling (timestamp, fraud_count_in_bucket, total_in_bucket)
        self.started_at = time.time()

    def record(self, tx: dict):
        self.total += 1
        self.amount_sum += tx["amount"]
        if tx["is_fraud"]:
            self.fraud += 1
            self.recent_alerts.appendleft(tx)
        if tx["is_fraud"] == bool(tx["actual"]):
            self.correct += 1

    def snapshot(self) -> dict:
        fraud_rate = (self.fraud / self.total) if self.total else 0.0
        avg_amount = (self.amount_sum / self.total) if self.total else 0.0
        accuracy = (self.correct / self.total) if self.total else 0.0
        return {
            "total": self.total,
            "fraud": self.fraud,
            "normal": self.total - self.fraud,
            "fraud_rate": round(fraud_rate, 4),
            "avg_amount": round(avg_amount, 2),
            "accuracy": round(accuracy, 4),
            "uptime_seconds": int(time.time() - self.started_at),
            "recent_alerts": list(self.recent_alerts),
        }

    def reset(self):
        self.__init__()


ANALYTICS = Analytics()


def build_live_transaction() -> dict:
    """Pick the next replayed transaction from the stream pool, score it with
    the real model, and dress it up with cosmetic display fields."""
    row = STREAM_POOL.sample(n=1).iloc[0]
    feature_row = {col: float(row[col]) for col in FEATURE_COLUMNS}
    prediction = score_row(feature_row)

    tx = {
        "id": str(uuid.uuid4())[:8].upper(),
        "occurred_at": time.time(),
        "amount": round(float(row["Amount"]), 2),
        "card_network": random.choice(CARD_NETWORKS),
        "merchant": random.choice(MERCHANT_CATEGORIES),
        "city": random.choice(CITIES),
        "status": prediction.status,
        "is_fraud": prediction.is_fraud,
        "fraud_probability": prediction.fraud_probability,
        "actual": int(row["Class"]),
    }
    return tx


@app.get("/api/stats")
def get_stats():
    return ANALYTICS.snapshot()


@app.post("/api/reset")
def reset_stats():
    ANALYTICS.reset()
    return {"ok": True}


@app.websocket("/ws/stream")
async def stream_transactions(websocket: WebSocket):
    """Pushes one freshly-scored transaction at a time, at a randomized
    interval, to simulate a live production transaction feed."""
    await websocket.accept()
    try:
        while True:
            tx = build_live_transaction()
            ANALYTICS.record(tx)
            await websocket.send_json({"transaction": tx, "stats": ANALYTICS.snapshot()})
            await asyncio.sleep(random.uniform(0.45, 1.5))
    except WebSocketDisconnect:
        pass


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(request, "index.html")
