# Metadata Enrichment API (FastAPI)

A deploy-ready API you can publish on Render.com.

## Features
- API key auth (`X-API-Key`)
- Rate limiting (SlowAPI)
- OpenAPI/Swagger docs at `/docs`
- Health check at `/health`
- Prometheus metrics at `/metrics` (disable via `ENABLE_METRICS=false`)
- Metadata enrichment at `/v1/meta?url=...`
- YouTube channel stats at `/v1/youtube/channel`
- YouTube latest videos at `/v1/youtube/latest`
- YouTube video stats at `/v1/youtube/video`
- YouTube search at `/v1/youtube/search`

## Run locally
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open:
- http://localhost:8000/docs

## Example requests
```bash
curl "http://localhost:8000/v1/meta?url=https://example.com" \
	-H "X-API-Key: change-me"

curl "http://localhost:8000/v1/whoami" \
	-H "X-API-Key: change-me"

# YouTube endpoints require a Google YouTube Data API v3 key
# Set YOUTUBE_API_KEY in .env / Render dashboard
curl "http://localhost:8000/v1/youtube/channel?handle=@GoogleDevelopers" \
	-H "X-API-Key: change-me"

curl "http://localhost:8000/v1/youtube/latest?channel_id=UC_x5XG1OV2P6uZZ5FSM9Ttw&max_results=5" \
	-H "X-API-Key: change-me"

curl "http://localhost:8000/v1/youtube/video?url=https://youtu.be/dQw4w9WgXcQ" \
	-H "X-API-Key: change-me"

curl "http://localhost:8000/v1/youtube/search?q=python%20fastapi&type=video&max_results=5" \
	-H "X-API-Key: change-me"
```

## Deploy on Render
This repo includes `render.yaml` (added next). Create a new **Web Service** from your GitHub repo and Render will pick it up.

## RapidAPI ready
This API supports RapidAPI-style authentication:
- Clients calling through RapidAPI will send `X-RapidAPI-Key`.
- Optionally, you can also verify `X-RapidAPI-Proxy-Secret` to ensure traffic is coming via RapidAPI.

Recommended for production on RapidAPI:
- Set `REQUIRE_API_KEY=true`
- Set `REQUIRE_RAPIDAPI_PROXY_SECRET=true`
- Set `RAPIDAPI_PROXY_SECRET` to the value RapidAPI provides for your backend

Rate limiting is applied per `X-RapidAPI-Key` when present (per-subscriber), otherwise per `X-API-Key`, otherwise per IP.

### RapidAPI OpenAPI import
Use your deployed OpenAPI JSON URL:
- `https://<your-service>.onrender.com/openapi.json`

RapidAPI will handle `X-RapidAPI-Key` automatically for subscribers. If you enable proxy-secret verification, configure RapidAPI to forward `X-RapidAPI-Proxy-Secret` and set `RAPIDAPI_PROXY_SECRET` on Render.
