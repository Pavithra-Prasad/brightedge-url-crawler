# BrightEdge URL Metadata & Topic Extractor

A service that takes a URL as input, fetches the page, and extracts structured metadata — title, description, body content, Open Graph tags, and language — along with a ranked list of topics/keywords describing what the page is about. Topic extraction uses unsupervised statistical analysis (YAKE), requiring no pre-trained models or external APIs.

## Assignment Deliverables

This repository contains all three parts of the BrightEdge Engineering assignment:

- **Part 1: Working service** — Source code in `app/`, deployed live (see Live Demo above), with full API documentation in this README
- **Part 2: Production design** — See `DESIGN.md` for the architecture, storage, schema, SLOs/SLAs, monitoring, cost analysis, and reliability strategy at billion-URL scale
- **Part 3: PoC plan** — See `POC_PLAN.md` for the phased rollout plan, blockers, release strategy, and success criteria

## Live Demo

- **API**: `https://brightedge-crawler-773180407845.us-central1.run.app`
- **Interactive docs**: https://brightedge-crawler-773180407845.us-central1.run.app/docs

## Stack

- **Python 3.11 + FastAPI** — async I/O for high concurrency per worker
- **httpx** — async HTTP client with connection pooling and timeout controls
- **BeautifulSoup4 + lxml** — fast, robust HTML parsing (lxml is C-based, ~10x faster than the pure-Python parser)
- **YAKE** — statistical keyword extraction, no models to load (chosen over KeyBERT/transformer alternatives for memory and cost efficiency at scale)
- **uvicorn** — production ASGI server

## Local Setup

1. Clone the repo:
   ```bash
   git clone https://github.com/Pavithra-Prasad/brightedge-url-crawler.git
   cd brightedge-url-crawler
   ```
2. Create a virtual environment and activate it:
   ```bash
   python -m venv venv && source venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Start the server:
   ```bash
   python -m app.main
   ```
5. Open [http://localhost:8000/docs](http://localhost:8000/docs) to test via the Swagger UI.

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/extract` | Main extraction endpoint (JSON body) |
| GET | `/extract?url=...` | Convenience GET for browser testing |
| GET | `/health` | Health check for load balancers |
| GET | `/docs` | Auto-generated Swagger UI |

## Example

```bash
curl -X POST https://brightedge-crawler-773180407845.us-central1.run.app/extract \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.cnn.com/2025/09/23/tech/google-study-90-percent-tech-jobs-ai"}'
```

Response (trimmed):

```json
{
  "url": "https://www.cnn.com/2025/09/23/tech/google-study-90-percent-tech-jobs-ai",
  "status": "success",
  "http_status_code": 200,
  "metadata": {
    "title": "Google says 90% of tech workers are now using AI at work | CNN Business",
    "description": "The overwhelming majority of tech industry workers use artificial intelligence on the job for tasks like writing and modifying code, a new Google study has found.",
    "og_type": "article",
    "language": "en"
  },
  "topics": [
    {"keyword": "Google", "score": 0.974},
    {"keyword": "software development", "score": 0.9711},
    {"keyword": "artificial intelligence", "score": 0.963}
  ],
  "fetch_time_ms": 497.0
}
```

## Deployment (Google Cloud Run)

1. Install the gcloud CLI:
   ```bash
   # macOS
   brew install --cask google-cloud-sdk
   # Or download from https://cloud.google.com/sdk/docs/install
   ```
2. Authenticate and set your project:
   ```bash
   gcloud auth login
   gcloud config set project YOUR_PROJECT_ID
   ```
3. Deploy from the project directory:
   ```bash
   gcloud run deploy brightedge-crawler \
     --source . \
     --region us-central1 \
     --allow-unauthenticated
   ```
4. Cloud Run automatically sets the `PORT` environment variable; the Dockerfile respects it.

## Known Limitations

- **JavaScript-rendered pages** (e.g., REI blog) return metadata from `<head>` only — body content extraction requires a headless browser. See `DESIGN.md` for the production fallback strategy.
- **Anti-bot challenge pages** (Cloudflare, Akamai) are detected via challenge phrase matching combined with upstream 4xx status and surfaced as `502 upstream_error`. Paywalled pages with valid `og:title` still return 200.

## AI Tools Used

This project was developed using the Antigravity IDE with Claude Opus 4.6 (Thinking) as a coding assistant. AI was used for:

- Scaffolding initial file structure and boilerplate (FastAPI app, Pydantic models, Dockerfile)
- Pair-debugging integration issues (notably a Brotli decoding bug with httpx, and a BeautifulSoup mutation-during-iteration bug in the noise-tag stripper)
- Drafting docstrings and inline comments

All architectural and design decisions were made by me, including:

- Choice of stack (Python/FastAPI/httpx/BeautifulSoup/YAKE) and the rationale for each — notably YAKE over transformer-based alternatives for memory and cost efficiency at scale
- API design and response schema
- The metadata extraction priority cascade (OG tags → meta tags → JSON-LD → body fallback)
- Error handling strategy (distinguishing fetch failures, upstream errors with usable metadata, and anti-bot challenge detection)
- Deployment platform choice (Cloud Run for PoC; production architecture described in `DESIGN.md`)

Edge cases like anti-bot challenge pages (e.g., Cloudflare's "Just a moment..." walls) were identified during my own testing and the handling was specified by me before being implemented.
