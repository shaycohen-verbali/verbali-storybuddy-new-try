# StoryBuddy v2

StoryBuddy is an AI reading-comprehension app for non-verbal children.

This v2 implementation adds a Python backend that handles:
- story package ingestion from text or PDF
- character/scene/object extraction
- style profile extraction from reference images
- character-to-style-reference mapping
- answer option generation with one fact-backed correct answer
- per-card participant + style ref selection
- replicate image generation (`nano-banana-2` / `nano-banana` / `nano-banana-pro`)
- full debug bundle + telemetry timeline

## Architecture

- Frontend: `index.html`, `styles.css`, `app.js`
- Backend API: `backend/main.py` (FastAPI)
- Vercel API entry: `api/index.py`
- Pipeline logic: `backend/pipeline.py`
- Model adapter: `backend/image_adapter.py`
- Package storage: `backend_data/packages/*.json`

## Local run

1. Install dependencies:

```bash
cd /tmp/verbali-storybuddy-new-try
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Run backend + frontend on one server:

```bash
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

3. Open:
- http://localhost:8000

## Image model configuration

StoryBuddy ask flow is Replicate-only and fail-fast for image generation.

Required:

```bash
export REPLICATE_API_TOKEN=YOUR_REPLICATE_TOKEN
```

Supported models:
- `nano-banana-2` -> `google/nano-banana-2`
- `nano-banana` -> `google/nano-banana`
- `nano-banana-pro` -> `google/nano-banana-pro`

Legacy aliases accepted by API:
- `standard` -> `nano-banana`
- `pro` -> `nano-banana-pro`

Optional setting:
- `STORYBUDDY_REPLICATE_BASE_URL` (default `https://api.replicate.com/v1`)

Ask behavior:
- Each card calls Replicate directly with `prompt`, `image_input` (up to 3 style refs), `aspect_ratio=match_input_image`, and `output_format=jpg`.
- If any card generation fails, `POST /api/ask` returns `502` (no fallback image path).

## API endpoints

- `GET /api/health`
- `GET /api/config`
- `GET /api/packages`
- `GET /api/packages/{package_id}`
- `DELETE /api/packages/{package_id}`
- `POST /api/setup/ingest`
- `POST /api/ask`

## Notes

- PDF extraction in backend uses `pypdf`.
- For scanned/image-only PDFs, provide OCR text manually in setup.
- Ask image generation is fail-fast: no OpenAI provider path and no mock fallback in the ask pipeline.
- On Vercel serverless, package JSON files are stored in `/tmp` (ephemeral). For persistent storage, wire a real DB/blob store.
- `POST /api/ask` now accepts either `packageId` or full `package` payload; frontend sends the full package to avoid serverless filesystem misses.
