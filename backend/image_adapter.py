from __future__ import annotations

import asyncio
import os
from typing import Dict, List

import httpx


MODEL_SLUGS: Dict[str, str] = {
    "nano-banana-2": "google/nano-banana-2",
    "nano-banana": "google/nano-banana",
    "nano-banana-pro": "google/nano-banana-pro",
}

MODEL_ALIASES: Dict[str, str] = {
    "standard": "nano-banana",
    "pro": "nano-banana-pro",
}


def canonicalize_model(model: str) -> str:
    normalized = (model or "").strip().lower()
    canonical = MODEL_ALIASES.get(normalized, normalized)
    if canonical not in MODEL_SLUGS:
        supported = ", ".join(sorted(MODEL_SLUGS.keys()))
        raise RuntimeError(f"unsupported model '{model}'. Supported models: {supported}")
    return canonical


def _first_image_url(output: object) -> str:
    def extract(value: object) -> str:
        if isinstance(value, str):
            if value.startswith(("http://", "https://", "data:image/")):
                return value
            return ""
        if isinstance(value, list):
            for item in value:
                found = extract(item)
                if found:
                    return found
            return ""
        if isinstance(value, dict):
            direct = value.get("url")
            if isinstance(direct, str) and direct.startswith(("http://", "https://", "data:image/")):
                return direct
            for item in value.values():
                found = extract(item)
                if found:
                    return found
            return ""
        return ""

    found = extract(output)
    return found if found else ""


async def _poll_replicate_prediction(
    *,
    client: httpx.AsyncClient,
    get_url: str,
    headers: Dict[str, str],
    max_attempts: int,
    poll_interval_seconds: float,
) -> Dict[str, object]:
    for attempt in range(max_attempts):
        poll = await client.get(get_url, headers=headers)
        if poll.status_code >= 400:
            raise RuntimeError(f"replicate polling failed ({poll.status_code}): {poll.text[:300]}")
        data = poll.json()
        status = data.get("status")
        if status in {"succeeded", "failed", "canceled"}:
            return data
        if attempt < max_attempts - 1:
            await asyncio.sleep(poll_interval_seconds)
    raise RuntimeError("replicate prediction timed out while polling")


async def generate_image(
    *,
    prompt: str,
    model: str,
    style_ref_images: List[str],
) -> str:
    token = os.getenv("REPLICATE_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("REPLICATE_API_TOKEN is required")

    canonical_model = canonicalize_model(model)
    owner, name = MODEL_SLUGS[canonical_model].split("/", 1)
    base_url = os.getenv("STORYBUDDY_REPLICATE_BASE_URL", "https://api.replicate.com/v1").rstrip("/")
    wait_seconds = max(1, min(20, int(os.getenv("STORYBUDDY_REPLICATE_WAIT_SECONDS", "8"))))
    poll_interval_seconds = max(0.5, float(os.getenv("STORYBUDDY_REPLICATE_POLL_INTERVAL_SECONDS", "1.0")))
    poll_max_attempts = max(1, min(30, int(os.getenv("STORYBUDDY_REPLICATE_POLL_MAX_ATTEMPTS", "12"))))

    image_inputs = [ref for ref in style_ref_images if isinstance(ref, str) and ref.strip()][:3]
    input_payload: Dict[str, object] = {
        "prompt": prompt,
        "image_input": image_inputs,
        "aspect_ratio": "match_input_image",
        "output_format": "jpg",
    }
    endpoint = f"{base_url}/models/{owner}/{name}/predictions"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Prefer": f"wait={wait_seconds}",
    }

    async with httpx.AsyncClient(timeout=35.0) as client:
        response = await client.post(endpoint, headers=headers, json={"input": input_payload})
        if response.status_code >= 400:
            raise RuntimeError(f"replicate request failed ({response.status_code}): {response.text[:500]}")
        data = response.json()

        status = data.get("status")
        if status not in {"succeeded", "failed", "canceled"}:
            get_url = ((data.get("urls") or {}).get("get") or "").strip()
            if get_url:
                data = await _poll_replicate_prediction(
                    client=client,
                    get_url=get_url,
                    headers=headers,
                    max_attempts=poll_max_attempts,
                    poll_interval_seconds=poll_interval_seconds,
                )
            else:
                raise RuntimeError("replicate prediction is pending and no polling URL was returned")

    if data.get("status") != "succeeded":
        err = data.get("error") or data.get("status") or "unknown replicate failure"
        raise RuntimeError(f"replicate generation failed: {err}")

    image_url = _first_image_url(data.get("output"))
    if not image_url:
        raise RuntimeError("replicate succeeded but returned no image output")
    return image_url
