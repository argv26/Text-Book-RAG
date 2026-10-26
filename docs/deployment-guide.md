# Production Deployment Guide — TextBook RAG

This guide details how to deploy the **TextBook RAG** application to production across various cloud platforms and Docker environments.

---

## 1. Quick Local Docker Deployment

The application is completely self-contained. The 97MB `textbook_rag.db` database (containing 12,010 indexed chunks across 3,666 pages) is packaged directly into the container image, requiring zero external database setup.

### Step-by-Step:
1. **Configure Environment:**
   ```bash
   cp .env.example .env
   # Edit .env and set GEMINI_API_KEY (optional, app works offline without it)
   ```

2. **Build and Run:**
   ```bash
   docker compose up --build -d web
   ```

3. **Verify:**
   Open your browser at `http://localhost:8000`.

---

## 2. Deploying to Google Cloud Run (Recommended Serverless)

Google Cloud Run provides automatic scaling, HTTPS, and zero costs when idle.

### Commands:
```bash
# 1. Authenticate with Google Cloud
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

# 2. Build and push container to Google Artifact Registry
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/textbook-rag

# 3. Deploy to Cloud Run
gcloud run deploy textbook-rag \
  --image gcr.io/YOUR_PROJECT_ID/textbook-rag \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --port 8000 \
  --memory 1Gi \
  --set-env-vars "DAILY_REQUEST_LIMIT=50,GEMINI_API_KEY=YOUR_KEY"
```

---

## 3. Deploying to Render / Railway

### Render:
1. Connect your GitHub repository to [Render.com](https://render.com).
2. Choose **Web Service** with **Docker** runtime.
3. In the **Environment Variables** section, add:
   - `PORT`: `8000`
   - `DAILY_REQUEST_LIMIT`: `50`
   - `GEMINI_API_KEY`: `your-gemini-key`
4. Click **Deploy**. Render will build the `Dockerfile` and provide an automatic `https://` URL.

### Railway:
1. Open [Railway.app](https://railway.app) and select **Deploy from GitHub repo**.
2. Railway detects the `Dockerfile` automatically.
3. Add environment variables in the Railway dashboard (`GEMINI_API_KEY`, `DAILY_REQUEST_LIMIT`).
4. Generate a public domain under service settings.

---

## 4. Deploying to any Linux VPS (Ubuntu / Debian)

### Using Docker:
```bash
git clone https://github.com/argv26/Text-Book-RAG.git
cd Text-Book-RAG
cp .env.example .env
# Edit .env with your nano / vim
docker compose up -d web
```

### Using Systemd (without Docker):
```bash
# 1. Install Python 3.11 and create venv
sudo apt update && sudo apt install -y python3-venv python3-pip
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Create systemd service (/etc/systemd/system/textbook-rag.service)
[Unit]
Description=TextBook RAG Web Service
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/Text-Book-RAG
EnvironmentFile=/home/ubuntu/Text-Book-RAG/.env
ExecStart=/home/ubuntu/Text-Book-RAG/.venv/bin/uvicorn ui.app:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target

# 3. Enable and start
sudo systemctl daemon-reload
sudo systemctl enable --now textbook-rag
```

---

## 5. Production Configuration Reference

| Environment Variable | Recommended Value | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | *(Secret key)* | Enables Gemini 2.5 Flash agentic tool calling |
| `OPENAI_API_KEY` | *(Secret key)* | Optional alternative LLM |
| `DAILY_REQUEST_LIMIT` | `50` | Maximum queries per IP/day (prevents API cost spikes). Set `0` for unlimited. |
| `PORT` | `8000` | Port for the HTTP web server |
| `DATABASE_URL` | *(Omit for SQLite)* | Optional PostgreSQL connection string for multi-container clusters |
