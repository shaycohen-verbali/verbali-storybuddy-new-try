# StoryBuddy v2

StoryBuddy is an AI reading-comprehension app for non-verbal children.

This v2 implementation adds a Python backend that handles:
- story package ingestion from text or PDF
- character/scene/object extraction
- style profile extraction from reference images
- character/scene-to-style-reference mapping
- answer option generation with one fact-backed correct answer (including character-list question handling)
- per-card participant + style ref selection
- replicate image generation (`nano-banana-2` / `nano-banana` / `nano-banana-pro`)
- full debug bundle + telemetry timeline

## Answer model configuration

StoryBuddy answer option generation uses Google Gemini through the Google Generative Language API.

Required:

```bash
export GEMINI_API_KEY=YOUR_GOOGLE_AI_STUDIO_KEY
```

Also accepted (fallback name):
- `GOOGLE_API_KEY`

Optional:
- `STORYBUDDY_ANSWER_MODEL` (default `gemini-2.5-flash`)
- `STORYBUDDY_GEMINI_BASE_URL` (default `https://generativelanguage.googleapis.com/v1beta`)
- `STORYBUDDY_ALLOW_RULE_BASED_FALLBACK` (default `false`; when `true`, uses local heuristic fallback if Gemini fails)

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
- `STORYBUDDY_REPLICATE_WAIT_SECONDS` (default `20`, max `55`)
- `STORYBUDDY_REPLICATE_POLL_INTERVAL_SECONDS` (default `1.0`)
- `STORYBUDDY_REPLICATE_POLL_MAX_ATTEMPTS` (default `24`)
- `STORYBUDDY_CARD_ASPECT_RATIO` (`4:3` default, or `1:1`)
- `STORYBUDDY_REF_MAX_WIDTH` (default `896`)
- `STORYBUDDY_REF_JPEG_QUALITY` (default `78`)
- `STORYBUDDY_LOG_LEVEL` (default `INFO`)

Ask behavior:
- Each card calls Replicate directly with `prompt`, `image_input` (up to 3 style refs), fixed card `aspect_ratio` (`4:3` default), and `output_format=jpg`.
- Reference images are compressed/downscaled before send to reduce model-side timeouts.
- If any card generation fails, `POST /api/ask` returns `502` (no fallback image path).
- Style references now include source metadata and editable character/scene hints for better illustration consistency.

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
- Browser PDF reference extraction now crops text-heavy sidebars and prefers illustration regions.
- Setup includes a reference image editor so users can rename, remove, and adjust character/scene hints before saving.
- Library includes an "Add New Book" action that resets setup into create mode.
- Library packages are cached in browser local storage so refresh keeps recent books when serverless storage is empty.
- Ask UI includes a live elapsed timer from click until all card images are loaded.
- Ask image generation is fail-fast: no OpenAI provider path and no mock fallback in the ask pipeline.
- Replicate + pipeline logs include `trace=card-N` and explicit reference image names/ids used per card.
- On Vercel serverless, package JSON files are stored in `/tmp` (ephemeral). For persistent storage, wire a real DB/blob store.
- `POST /api/ask` now accepts either `packageId` or full `package` payload; frontend sends the full package to avoid serverless filesystem misses.
