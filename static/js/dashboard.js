/* FraudGuard.live — dashboard client
   Connects to the /ws/stream WebSocket, renders each scored transaction
   into the live ledger the instant it arrives, and keeps the analytics
   panels (stat cards, donut, trend, recent alerts) in sync. */

// If the frontend is deployed separately from the backend (e.g. this page
// on Netlify, the API on Render), set API_BASE to the backend's https URL,
// e.g. "https://fraud-detection-api.onrender.com". Leave it as "" when the
// frontend is served by the same FastAPI app (the default for this repo).
const API_BASE = "";

const LEDGER_MAX_ROWS = 120;
const TREND_WINDOW = 20;   // rolling window size used to compute the trend point
const TREND_MAX_POINTS = 30;

const els = {
  connText: document.getElementById("connText"),
  pulseDot: document.getElementById("pulseDot"),
  ledger: document.getElementById("ledger"),
  alertsList: document.getElementById("alertsList"),
  statTotal: document.getElementById("statTotal"),
  statFraud: document.getElementById("statFraud"),
  statRate: document.getElementById("statRate"),
  statAmount: document.getElementById("statAmount"),
  statAccuracy: document.getElementById("statAccuracy"),
  resetBtn: document.getElementById("resetBtn"),
};

const fmtMoney = (n) => `$${Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const fmtPct = (n) => `${(n * 100).toFixed(2)}%`;
const fmtTime = (epochSeconds) => {
  const d = new Date(epochSeconds * 1000);
  return d.toLocaleTimeString(undefined, { hour12: false });
};

// ---------------------------------------------------------------------
// Charts
// ---------------------------------------------------------------------
const donutChart = new Chart(document.getElementById("donutChart"), {
  type: "doughnut",
  data: {
    labels: ["Normal", "Fraud"],
    datasets: [{
      data: [0, 0],
      backgroundColor: ["#34d399", "#f2495c"],
      borderColor: "#11151c",
      borderWidth: 3,
    }],
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    cutout: "68%",
    plugins: {
      legend: {
        position: "bottom",
        labels: { color: "#8891a3", font: { family: "Inter", size: 11 }, boxWidth: 10 },
      },
    },
  },
});

const trendChart = new Chart(document.getElementById("trendChart"), {
  type: "line",
  data: {
    labels: [],
    datasets: [{
      data: [],
      borderColor: "#5eead4",
      backgroundColor: "rgba(94, 234, 212, 0.12)",
      fill: true,
      tension: 0.35,
      pointRadius: 0,
      borderWidth: 2,
    }],
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 250 },
    scales: {
      x: { display: false },
      y: {
        min: 0,
        ticks: { color: "#5b6473", font: { family: "JetBrains Mono", size: 10 }, callback: (v) => `${v}%` },
        grid: { color: "#232a36" },
      },
    },
    plugins: { legend: { display: false } },
  },
});

const recentStatuses = []; // rolling buffer of 1 (fraud) / 0 (normal) for the trend line

function pushTrendPoint(isFraud) {
  recentStatuses.push(isFraud ? 1 : 0);
  if (recentStatuses.length > TREND_WINDOW) recentStatuses.shift();
  const ratePct = (recentStatuses.reduce((a, b) => a + b, 0) / recentStatuses.length) * 100;

  trendChart.data.labels.push("");
  trendChart.data.datasets[0].data.push(ratePct.toFixed(1));
  if (trendChart.data.labels.length > TREND_MAX_POINTS) {
    trendChart.data.labels.shift();
    trendChart.data.datasets[0].data.shift();
  }
  trendChart.update();
}

// ---------------------------------------------------------------------
// Ledger + alerts rendering
// ---------------------------------------------------------------------
function renderLedgerRow(tx) {
  const row = document.createElement("div");
  row.className = "ledger-row" + (tx.is_fraud ? " is-fraud" : "");
  row.innerHTML = `
    <span class="col-id">#${tx.id}</span>
    <span class="col-time">${fmtTime(tx.occurred_at)}</span>
    <span class="col-merchant">${tx.merchant}</span>
    <span class="col-city">${tx.city}</span>
    <span class="col-card">${tx.card_network}</span>
    <span class="col-amount">${fmtMoney(tx.amount)}</span>
    <span class="verdict ${tx.is_fraud ? "alert" : "approved"}">${tx.is_fraud ? "● ALERT" : "✓ Approved"}</span>
  `;
  els.ledger.prepend(row);
  while (els.ledger.children.length > LEDGER_MAX_ROWS) {
    els.ledger.removeChild(els.ledger.lastChild);
  }
}

function renderAlert(tx) {
  const empty = els.alertsList.querySelector(".empty-state");
  if (empty) empty.remove();

  const item = document.createElement("div");
  item.className = "alert-item";
  item.innerHTML = `
    <div class="alert-item-top">
      <span>#${tx.id} · ${tx.merchant}</span>
      <span class="alert-item-amount">${fmtMoney(tx.amount)}</span>
    </div>
    <div class="alert-item-meta">${tx.city} · ${tx.card_network} · ${(tx.fraud_probability * 100).toFixed(1)}% confidence · ${fmtTime(tx.occurred_at)}</div>
  `;
  els.alertsList.prepend(item);
  while (els.alertsList.children.length > 25) {
    els.alertsList.removeChild(els.alertsList.lastChild);
  }
}

function updateStats(stats) {
  els.statTotal.textContent = stats.total.toLocaleString();
  els.statFraud.textContent = stats.fraud.toLocaleString();
  els.statRate.textContent = fmtPct(stats.fraud_rate);
  els.statAmount.textContent = fmtMoney(stats.avg_amount);
  els.statAccuracy.textContent = stats.total > 0 ? fmtPct(stats.accuracy) : "—";

  donutChart.data.datasets[0].data = [stats.normal, stats.fraud];
  donutChart.update();
}

// ---------------------------------------------------------------------
// WebSocket connection (auto-reconnects if the server restarts)
// ---------------------------------------------------------------------
let socket;

function connect() {
  const wsUrl = API_BASE
    ? API_BASE.replace(/^http/, "ws") + "/ws/stream"
    : `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/ws/stream`;
  socket = new WebSocket(wsUrl);

  socket.onopen = () => {
    els.connText.textContent = "Live";
    els.pulseDot.classList.add("live");
  };

  socket.onmessage = (event) => {
    const payload = JSON.parse(event.data);
    const tx = payload.transaction;
    renderLedgerRow(tx);
    pushTrendPoint(tx.is_fraud);
    if (tx.is_fraud) renderAlert(tx);
    updateStats(payload.stats);
  };

  socket.onclose = () => {
    els.connText.textContent = "Reconnecting…";
    els.pulseDot.classList.remove("live");
    setTimeout(connect, 1500);
  };

  socket.onerror = () => socket.close();
}

connect();

// ---------------------------------------------------------------------
// Reset session
// ---------------------------------------------------------------------
els.resetBtn.addEventListener("click", async () => {
  await fetch(`${API_BASE}/api/reset`, { method: "POST" });
  els.ledger.innerHTML = "";
  els.alertsList.innerHTML = '<div class="empty-state">No alerts yet. Watching the feed…</div>';
  recentStatuses.length = 0;
  trendChart.data.labels = [];
  trendChart.data.datasets[0].data = [];
  trendChart.update();
  updateStats({ total: 0, fraud: 0, normal: 0, fraud_rate: 0, avg_amount: 0, accuracy: 0 });
});

// Initial paint from /api/stats in case the WS takes a beat to connect
fetch(`${API_BASE}/api/stats`).then((r) => r.json()).then(updateStats).catch(() => {});
