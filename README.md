# YouTube Insights API

A small REST API, built with **Python and FastAPI**, that returns clean statistics for YouTube channels and videos. It also has a page metadata endpoint that reads a site's title, description and preview image.

## Why I built it

The official YouTube Data API is powerful but awkward to use directly. A channel can be given as an ID, a `@handle`, an old username or a full URL, and every request costs quota. I wanted one simple API that accepts whatever a user pastes in, returns tidy JSON, and is ready to deploy or sell on a marketplace such as RapidAPI.

## What it does

| Endpoint | What it returns |
|---|---|
| `GET /v1/youtube/channel` | Subscribers, total views, video count and profile details for a channel. Accepts `channel_id`, `handle`, `username` or `url`. |
| `GET /v1/youtube/latest` | The newest videos of a channel with their view, like and comment counts. |
| `GET /v1/youtube/video` | Stats for one video, from a video ID or any YouTube link (`watch?v=`, `youtu.be`, Shorts, embed). |
| `GET /v1/youtube/search` | Search results for videos or channels. |
| `GET /v1/meta` | Title, description, preview image and favicon of any web page. |
| `GET /health` | Health check for uptime monitors. |

Interactive docs (Swagger) are served at `/docs`.

Built in features:

- **API key authentication** through the `X-API-Key` header, plus RapidAPI headers.
- **Rate limiting** per API key or per IP address (SlowAPI).
- **Caching**: YouTube responses are kept for 5 minutes to save quota and speed up repeat requests.
- **Consistent errors**: every error has the same JSON shape and a `request_id` for tracing.
- **Monitoring**: Prometheus metrics at `/metrics`.

## Run it locally

You need Python 3.11 or newer and a free [YouTube Data API v3 key](https://console.cloud.google.com/apis/library/youtube.googleapis.com).

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env             # then put your YouTube key in .env
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000/docs to try every endpoint in the browser.

## Sample request and response

```bash
curl "http://localhost:8000/v1/youtube/video?url=https://youtu.be/dQw4w9WgXcQ" \
  -H "X-API-Key: change-me"
```

```json
{
  "ok": true,
  "fetched_at": "2026-11-27T17:08:24Z",
  "video_id": "dQw4w9WgXcQ",
  "channel_id": "UCuAXFkgsw1L7xaCfnd5JJOw",
  "channel_title": "Rick Astley",
  "title": "Rick Astley - Never Gonna Give You Up (Official Video)",
  "published_at": "2009-10-25T06:57:33Z",
  "duration": "PT3M33S",
  "thumbnail_url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
  "view_count": 1700000000,
  "like_count": 18000000,
  "comment_count": 2400000
}
```

The numbers above are illustrative. If something is wrong, the API answers with a clear error instead:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Provide a valid 11-character video_id or a YouTube video URL"
  },
  "request_id": "537d067c-d030-4d86-8d92-17f64d13054c"
}
```

## A problem I solved

**Finding a channel from whatever the user pastes.** People share channels in many forms: `@handle`, `youtube.com/channel/UC...`, `youtube.com/user/name`, `youtube.com/c/name`, or just a name. The YouTube API needs a different parameter for each, and custom `/c/` names cannot be looked up directly at all. I wrote a parser (`app/youtube.py`) that detects the format and picks the right lookup. For custom names it falls back to a channel search and then fetches full stats by ID. Combined with the 5 minute cache, this keeps quota use low while users never have to know what a "channel ID" is.

## Deploy

The repo includes a `render.yaml`, so it deploys on [Render](https://render.com) as a web service. Set `YOUTUBE_API_KEY` in the Render dashboard; `API_KEY` is generated automatically. It runs with Gunicorn and Uvicorn workers.

To publish on RapidAPI, import `https://<your-service>.onrender.com/openapi.json` and set `REQUIRE_RAPIDAPI_PROXY_SECRET=true` with the secret RapidAPI gives you.

## Project structure

```
app/
├── main.py       # FastAPI app, middleware, error handlers
├── api_v1.py     # /v1 endpoints and response models
├── youtube.py    # YouTube Data API client, URL parsing, caching
├── security.py   # API key and RapidAPI authentication
├── limiting.py   # Rate limiting rules
└── settings.py   # Configuration from environment variables
```

## Tech stack

Python 3.11, FastAPI, Pydantic, HTTPX (async), BeautifulSoup, SlowAPI, Prometheus, Gunicorn, Render.
