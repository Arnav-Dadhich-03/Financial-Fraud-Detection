# Deployment Guide

This app has two pieces with very different hosting needs:

- **The backend** (`app.py`) is a long-running Python process that holds open
  WebSocket connections. It needs a real server/container host.
- **The frontend** (`templates/index.html`, `static/`) is plain HTML/CSS/JS.
  It can be served by the backend itself, or hosted separately as a static
  site.

**Netlify only hosts static sites and short-lived serverless functions — it
cannot run the FastAPI process or hold open the WebSocket connection.** So
there are really two valid setups:

| Setup | Where | Effort | Recommended for |
|---|---|---|---|
| **A. Single deploy** | Everything on Render | Lowest | Just want it live, one URL |
| **B. Split deploy** | Backend on Render, dashboard on Netlify | A bit more | You specifically want a Netlify-hosted frontend (e.g. for a portfolio site) |

---

## Option A — Single deploy on Render (recommended)

Render runs your Dockerfile directly and gives you one URL that serves both
the API and the dashboard — no code changes needed.

1. **Push this repo to GitHub** (or GitLab/Bitbucket).
   ```bash
   git init
   git add .
   git commit -m "Live fraud detection dashboard"
   git branch -M main
   git remote add origin https://github.com/<you>/Financial-Fraud-Detection.git
   git push -u origin main
   ```

2. **Create the service on Render**
   - Go to [render.com](https://render.com) → **New** → **Web Service**.
   - Connect your GitHub account and select this repo.
   - Render will detect the `Dockerfile` automatically and set
     **Environment: Docker**.
   - **Instance Type:** the Free tier works fine for a demo (it spins down
     after 15 minutes of inactivity and takes ~30–50s to wake back up on the
     next request — fine for a portfolio link, not for production traffic).
   - No environment variables are required. (Render automatically injects a
     `PORT` variable, which `Dockerfile`'s `CMD` already respects.)
   - Click **Create Web Service**.

3. **Wait for the build.** Render builds the Docker image, installs
   dependencies, and starts `uvicorn`. Watch the logs for
   `Application startup complete`.

4. **Open your live URL** — Render gives you something like
   `https://fraud-detection-api.onrender.com`. The dashboard, the live
   WebSocket stream, `/predict`, and `/docs` all work immediately, since
   `dashboard.js` connects to the same origin by default.

That's it — one service, one URL, fully live.

---

## Option B — Split deploy: backend on Render, dashboard on Netlify

Use this if you specifically want the dashboard served from Netlify (e.g.
alongside other static portfolio pages) while the FastAPI backend still runs
on Render.

### 1. Deploy the backend on Render

Follow **Option A**, steps 1–4, for the backend repo (you can deploy the
whole repo as-is — the unused parts are harmless). Note the resulting URL,
e.g. `https://fraud-detection-api.onrender.com`.

### 2. Lock down CORS (recommended once you know your Netlify URL)

In Render's dashboard for this service, add an environment variable:

```
ALLOWED_ORIGINS = https://your-dashboard.netlify.app
```

(`app.py` already reads this — it defaults to `*` if unset, which is fine for
testing but should be restricted before you call it "production".)

### 3. Point the frontend at the Render backend

Edit **`static/js/dashboard.js`** and set:

```js
const API_BASE = "https://fraud-detection-api.onrender.com";
```

(Use your actual Render URL — no trailing slash.)

### 4. Create a standalone static folder for Netlify

Netlify needs a folder containing just the static assets — no Python, no
Jinja2 server. `templates/index.html` is already plain HTML (it has no
server-side template syntax), so you can publish it directly:

```bash
mkdir netlify-site
cp templates/index.html netlify-site/index.html
cp -r static netlify-site/static
```

Your `netlify-site/` folder should look like:

```
netlify-site/
├── index.html
└── static/
    ├── css/style.css
    └── js/dashboard.js   (with API_BASE set, per step 3)
```

### 5. Deploy `netlify-site/` to Netlify

**Easiest — drag and drop:**
- Go to [app.netlify.com](https://app.netlify.com) → **Add new site** →
  **Deploy manually** → drag the `netlify-site` folder onto the page.

**Or via Git:**
- Push `netlify-site/` to its own GitHub repo (or a subfolder of this one).
- In Netlify: **Add new site** → **Import an existing project** → connect
  the repo.
- **Build command:** leave blank (no build needed).
- **Publish directory:** `netlify-site` (or `.` if it's its own repo root).
- Deploy.

### 6. Verify

Open your Netlify URL. The dashboard should load, the connection indicator
should flip to **Live**, and the ledger should start streaming — now pulling
from your Render backend across origins via CORS.

---

## Notes

- **Cold starts:** Render's free tier sleeps after inactivity. The first
  request after a sleep will be slow; the WebSocket will connect once the
  instance is awake.
- **WebSockets need `wss://` over HTTPS.** Both Render and Netlify serve
  over HTTPS by default, and `dashboard.js` already upgrades to `wss://`
  automatically when the page is loaded over HTTPS.
- **Retraining for production:** if you want this running on real Kaggle
  data instead of the synthetic stand-in, retrain locally
  (`python fraud_detection_pipeline.py` with `creditcard.csv` in the root),
  commit the refreshed `fraud_model.pkl` / `stream_data.pkl`, and redeploy.
