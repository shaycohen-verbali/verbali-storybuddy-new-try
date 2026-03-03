# StoryBuddy v2

StoryBuddy is an AI reading-comprehension app for non-verbal children.

This v2 implementation adds a Python backend that handles:
- story package ingestion from text or PDF
- character/scene/object extraction
- style profile extraction from reference images
- character-to-style-reference mapping
- answer option generation with one fact-backed correct answer
- per-card participant + style ref selection
- model-adapter image generation (`nano-banana-2` / `pro` / `standard`)
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

Default mode is mock (safe local fallback):
- `STORYBUDDY_IMAGE_PROVIDER=mock`

To use a real OpenAI-compatible image endpoint:

```bash
export STORYBUDDY_IMAGE_PROVIDER=openai_compatible
export STORYBUDDY_IMAGE_API_KEY=YOUR_KEY
export STORYBUDDY_IMAGE_BASE_URL=https://api.openai.com/v1
```

The app will call `/images/generations` with the selected model (`nano-banana-2`, `pro`, `standard`).

## API endpoints

- `GET /api/health`
- `GET /api/packages`
- `GET /api/packages/{package_id}`
- `DELETE /api/packages/{package_id}`
- `POST /api/setup/ingest`
- `POST /api/ask`

## Notes

- PDF extraction in backend uses `pypdf`.
- For scanned/image-only PDFs, provide OCR text manually in setup.
- If real image generation fails, backend falls back to mock image generation and records the error in card debug payload.
- On Vercel serverless, package JSON files are stored in `/tmp` (ephemeral). For persistent storage, wire a real DB/blob store.
