from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import time
from typing import Dict, List, Optional

import httpx
try:
    from PIL import Image
except Exception:  # pragma: no cover - optional dependency in runtime
    Image = None

logger = logging.getLogger("storybuddy.image")


MODEL_SLUGS: Dict[str, str] = {
    "nano-banana-2": "google/nano-banana-2",
    "nano-banana": "google/nano-banana",
    "nano-banana-pro": "google/nano-banana-pro",
}

MODEL_ALIASES: Dict[str, str] = {
    "standard": "nano-banana",
    "pro": "nano-banana-pro",
}

ALLOWED_ASPECT_RATIOS = {"1:1", "4:3"}


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


def _get_fixed_aspect_ratio() -> str:
    ratio = os.getenv("STORYBUDDY_CARD_ASPECT_RATIO", "4:3").strip()
    return ratio if ratio in ALLOWED_ASPECT_RATIOS else "4:3"


def _compress_data_url_image(data_url: str, *, max_width: int, quality: int) -> str:
    if not data_url.startswith("data:image/") or "," not in data_url:
        return data_url
    if not Image:
        return data_url

    header, b64 = data_url.split(",", 1)
    if ";base64" not in header:
        return data_url

    try:
        raw = base64.b64decode(b64)
        with Image.open(io.BytesIO(raw)) as src:
            img = src.convert("RGB")
            width, height = img.size
            if width > max_width:
                resized_height = max(1, int(height * (max_width / float(width))))
                img = img.resize((max_width, resized_height), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=quality, optimize=True)
            payload = base64.b64encode(out.getvalue()).decode("ascii")
            return f"data:image/jpeg;base64,{payload}"
    except Exception:
        return data_url


def _prepare_style_ref_images(style_ref_images: List[str]) -> List[str]:
    max_width = max(512, min(2048, int(os.getenv("STORYBUDDY_REF_MAX_WIDTH", "896"))))
    quality = max(40, min(95, int(float(os.getenv("STORYBUDDY_REF_JPEG_QUALITY", "78")))))
    out: List[str] = []
    for ref in style_ref_images[:3]:
        if not isinstance(ref, str) or not ref.strip():
            continue
        out.append(_compress_data_url_image(ref, max_width=max_width, quality=quality))
    return out


async def _poll_replicate_prediction(
    *,
    client: httpx.AsyncClient,
    get_url: str,
    headers: Dict[str, str],
    max_attempts: int,
    poll_interval_seconds: float,
    trace_id: str,
) -> Dict[str, object]:
    last_status = "unknown"
    prediction_id = "unknown"
    for attempt in range(max_attempts):
        poll = await client.get(get_url, headers=headers)
        if poll.status_code >= 400:
            raise RuntimeError(f"replicate polling failed ({poll.status_code}): {poll.text[:300]}")
        data = poll.json()
        status = str(data.get("status") or "unknown")
        prediction_id = str(data.get("id") or prediction_id)
        last_status = status
        logger.info(
            "replicate poll trace=%s prediction=%s attempt=%s/%s status=%s",
            trace_id,
            prediction_id,
            attempt + 1,
            max_attempts,
            status,
        )
        if status in {"succeeded", "failed", "canceled"}:
            return data
        if attempt < max_attempts - 1:
            await asyncio.sleep(poll_interval_seconds)
    raise RuntimeError(
        "replicate prediction timed out while polling "
        f"(trace={trace_id}, prediction={prediction_id}, lastStatus={last_status}, "
        f"attempts={max_attempts}, intervalSeconds={poll_interval_seconds})"
    )


async def generate_image(
    *,
    prompt: str,
    model: str,
    style_ref_images: List[str],
    style_ref_labels: Optional[List[str]] = None,
    trace_id: str = "unknown",
) -> str:
    started_at = time.monotonic()
    token = os.getenv("REPLICATE_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("REPLICATE_API_TOKEN is required")

    canonical_model = canonicalize_model(model)
    owner, name = MODEL_SLUGS[canonical_model].split("/", 1)
    base_url = os.getenv("STORYBUDDY_REPLICATE_BASE_URL", "https://api.replicate.com/v1").rstrip("/")
    wait_seconds = max(1, min(55, int(os.getenv("STORYBUDDY_REPLICATE_WAIT_SECONDS", "20"))))
    poll_interval_seconds = max(0.5, float(os.getenv("STORYBUDDY_REPLICATE_POLL_INTERVAL_SECONDS", "1.0")))
    poll_max_attempts = max(1, min(60, int(os.getenv("STORYBUDDY_REPLICATE_POLL_MAX_ATTEMPTS", "24"))))
    request_timeout_seconds = max(40.0, float(wait_seconds + 20))

    image_inputs = _prepare_style_ref_images(style_ref_images)
    aspect_ratio = _get_fixed_aspect_ratio()
    input_payload: Dict[str, object] = {
        "prompt": prompt,
        "image_input": image_inputs,
        "aspect_ratio": aspect_ratio,
        "output_format": "jpg",
    }
    endpoint = f"{base_url}/models/{owner}/{name}/predictions"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Prefer": f"wait={wait_seconds}",
    }
    logger.info(
        "replicate request start trace=%s model=%s endpoint=%s wait=%ss pollAttempts=%s pollInterval=%ss refs=%s refNames=%s aspectRatio=%s",
        trace_id,
        canonical_model,
        endpoint,
        wait_seconds,
        poll_max_attempts,
        poll_interval_seconds,
        len(image_inputs),
        ", ".join((style_ref_labels or [])[:3]) if style_ref_labels else "none",
        aspect_ratio,
    )

    async with httpx.AsyncClient(timeout=request_timeout_seconds) as client:
        response = await client.post(endpoint, headers=headers, json={"input": input_payload})
        if response.status_code >= 400:
            raise RuntimeError(f"replicate request failed ({response.status_code}): {response.text[:500]}")
        data = response.json()
        prediction_id = str(data.get("id") or "unknown")
        logger.info(
            "replicate request accepted trace=%s prediction=%s status=%s",
            trace_id,
            prediction_id,
            data.get("status"),
        )

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
                    trace_id=trace_id,
                )
            else:
                raise RuntimeError("replicate prediction is pending and no polling URL was returned")

    if data.get("status") != "succeeded":
        err = data.get("error") or data.get("status") or "unknown replicate failure"
        logger.error(
            "replicate generation failed trace=%s prediction=%s status=%s error=%s",
            trace_id,
            data.get("id"),
            data.get("status"),
            err,
        )
        raise RuntimeError(f"replicate generation failed: {err}")

    image_url = _first_image_url(data.get("output"))
    if not image_url:
        raise RuntimeError("replicate succeeded but returned no image output")
    logger.info(
        "replicate generation success trace=%s prediction=%s elapsedMs=%s",
        trace_id,
        data.get("id"),
        int((time.monotonic() - started_at) * 1000),
    )
    return image_url
