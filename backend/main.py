from __future__ import annotations

from pathlib import Path
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .models import AskRequest, AskResponse, SetupIngestRequest, SetupIngestResponse, StoryPackage
from .pipeline import ingest_setup, run_ask_pipeline
from .storage import delete_package, list_packages, load_package, save_package

app = FastAPI(title="StoryBuddy API", version="2.0.0")
BASE_DIR = Path(__file__).resolve().parent.parent

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "service": "storybuddy-api"}


@app.get("/api/config")
def config() -> dict:
    provider = os.getenv("STORYBUDDY_IMAGE_PROVIDER", "mock").strip().lower() or "mock"
    has_api_key = bool(os.getenv("STORYBUDDY_IMAGE_API_KEY", "").strip())
    return {
        "imageProvider": provider,
        "hasImageApiKey": has_api_key,
    }


@app.get("/api/packages")
def packages_list() -> list[dict]:
    packages = list_packages()
    return [package.model_dump(by_alias=True) for package in packages]


@app.get("/api/packages/{package_id}")
def packages_get(package_id: str) -> dict:
    package = load_package(package_id)
    if not package:
        raise HTTPException(status_code=404, detail="package not found")
    return package.model_dump(by_alias=True)


@app.delete("/api/packages/{package_id}")
def packages_delete(package_id: str) -> dict:
    deleted = delete_package(package_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="package not found")
    return {"deleted": True}


@app.post("/api/setup/ingest", response_model=SetupIngestResponse)
def setup_ingest(req: SetupIngestRequest) -> SetupIngestResponse:
    existing: StoryPackage | None = None
    if req.package_id:
        existing = load_package(req.package_id)

    try:
        result = ingest_setup(req, existing=existing)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    save_package(result.package)
    return result


@app.post("/api/ask", response_model=AskResponse)
async def ask(req: AskRequest) -> AskResponse:
    package = req.package
    if not package and req.package_id:
        package = load_package(req.package_id)
    if not package:
        raise HTTPException(status_code=404, detail="package not found; resave package or include package payload")
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="question is required")

    return await run_ask_pipeline(package, req.question, req.model)


app.mount("/", StaticFiles(directory=str(BASE_DIR), html=True), name="frontend")
