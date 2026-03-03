# StoryBuddy MVP

A zero-dependency browser MVP of StoryBuddy for non-verbal reading-comprehension workflows.

## What this app does

- Setup flow to create reusable story packages from story text.
- Optional style/reference image upload per package.
- Local library to reopen/update/delete packages.
- Ask flow with typed question or browser speech recognition.
- Generates exactly 3 answer cards with one `isCorrect: true` option (fact-backed from learned story facts).
- Per-card generated illustration (simulated image model) in a stable story-style palette.
- Full debug bundle per card:
  - prompts
  - selected participants
  - style refs used
  - model used
  - generation errors
- Performance telemetry:
  - step timings
  - per-card timings
  - event timeline from t0 to "last image interactive"

## Local run

From the project directory:

```bash
python3 -m http.server 8000
```

Then open:

- http://localhost:8000/index.html

## Notes

- This MVP stores story packages in browser `localStorage`.
- PDF upload now attempts automatic extraction in-browser (`pdf.js` via CDN), then falls back to a heuristic parser if CDN loading fails.
- For scanned/image-only PDFs, paste OCR/extracted text manually for best results.
- Image generation is simulated client-side (canvas) while preserving package-specific style consistency and debug metadata.
