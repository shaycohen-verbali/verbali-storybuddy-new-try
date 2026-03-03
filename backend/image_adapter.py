from __future__ import annotations

import base64
import hashlib
import json
import os
from textwrap import shorten
from typing import Dict, List

import httpx


def get_image_provider() -> str:
    provider = os.getenv("STORYBUDDY_IMAGE_PROVIDER", "mock").strip().lower()
    return provider if provider else "mock"


def _seeded_palette(seed_text: str) -> List[str]:
    digest = hashlib.sha256(seed_text.encode("utf-8")).hexdigest()
    a = int(digest[:2], 16)
    b = int(digest[2:4], 16)
    c = int(digest[4:6], 16)
    return [
        f"#{a:02x}{(180 + b // 3) % 255:02x}{(120 + c // 4) % 255:02x}",
        f"#{(130 + c // 2) % 255:02x}{a:02x}{(160 + b // 2) % 255:02x}",
        f"#{(110 + b // 3) % 255:02x}{(95 + c // 3) % 255:02x}{a:02x}",
    ]


def _mock_svg_data_url(text: str, scene: str, characters: List[str], seed: str) -> str:
    p1, p2, p3 = _seeded_palette(seed)
    chars = ", ".join(characters[:3]) or "Story characters"
    safe_text = shorten(text, width=42, placeholder="...").replace("&", "and")
    safe_scene = shorten(scene, width=48, placeholder="...").replace("&", "and")

    svg = f"""<svg xmlns='http://www.w3.org/2000/svg' width='1024' height='768'>
  <defs>
    <linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>
      <stop offset='0%' stop-color='{p1}' />
      <stop offset='100%' stop-color='{p2}' />
    </linearGradient>
  </defs>
  <rect width='100%' height='100%' fill='url(#g)'/>
  <ellipse cx='160' cy='120' rx='130' ry='60' fill='rgba(255,255,255,0.28)'/>
  <rect x='0' y='560' width='1024' height='208' fill='{p3}'/>
  <rect x='40' y='595' width='944' height='130' fill='rgba(22,25,30,0.66)'/>
  <text x='70' y='660' font-size='44' font-family='Trebuchet MS, sans-serif' fill='#fbf2da' font-weight='700'>{safe_text}</text>
  <text x='70' y='715' font-size='26' font-family='Trebuchet MS, sans-serif' fill='#f5ead0'>Scene: {safe_scene}</text>
  <text x='70' y='748' font-size='22' font-family='Trebuchet MS, sans-serif' fill='#f5ead0'>Characters: {shorten(chars, width=70, placeholder='...')}</text>
</svg>"""
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


async def generate_image(
    *,
    prompt: str,
    model: str,
    scene: str,
    characters: List[str],
    style_ref_summaries: List[Dict[str, str]],
) -> str:
    provider = get_image_provider()
    if provider == "mock":
        seed = f"{model}|{scene}|{','.join(characters)}|{prompt[:120]}"
        return _mock_svg_data_url(prompt, scene, characters, seed)

    if provider in {"openai", "openai_compatible"}:
        api_key = os.getenv("STORYBUDDY_IMAGE_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("STORYBUDDY_IMAGE_API_KEY is required for openai_compatible provider")

        base_url = os.getenv("STORYBUDDY_IMAGE_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        refs = ", ".join(ref["name"] for ref in style_ref_summaries[:2]) or "storybook references"
        payload = {
            "model": model,
            "prompt": f"{prompt}\n\nUse style references: {refs}",
            "size": "1024x1024",
            "response_format": "b64_json",
        }

        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(
                f"{base_url}/images/generations",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        if response.status_code >= 400:
            raise RuntimeError(f"image generation failed ({response.status_code}): {response.text[:300]}")

        data = response.json()
        b64 = (data.get("data") or [{}])[0].get("b64_json")
        if not b64:
            raise RuntimeError("image generation response missing b64_json")
        return f"data:image/png;base64,{b64}"

    if provider == "replicate":
        return await _generate_image_replicate(
            prompt=prompt,
            model=model,
            style_ref_summaries=style_ref_summaries,
        )

    raise RuntimeError(f"unsupported STORYBUDDY_IMAGE_PROVIDER: {provider}")


def generate_mock_image(
    *,
    prompt: str,
    model: str,
    scene: str,
    characters: List[str],
) -> str:
    seed = f"{model}|{scene}|{','.join(characters)}|{prompt[:120]}"
    return _mock_svg_data_url(prompt, scene, characters, seed)


def _replicate_model_identifier(model: str) -> str:
    slug = model.upper().replace("-", "_")
    per_model = os.getenv(f"STORYBUDDY_REPLICATE_MODEL_{slug}", "").strip()
    if per_model:
        return per_model

    generic = os.getenv("STORYBUDDY_REPLICATE_MODEL", "").strip()
    if generic:
        return generic

    return ""


def _first_image_url(output: object) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        for item in output:
            if isinstance(item, str):
                return item
    if isinstance(output, dict):
        for value in output.values():
            if isinstance(value, str) and value.startswith(("http://", "https://", "data:image/")):
                return value
    return ""


async def _poll_replicate_prediction(
    *,
    client: httpx.AsyncClient,
    get_url: str,
    headers: Dict[str, str],
) -> Dict[str, object]:
    for _ in range(30):
        poll = await client.get(get_url, headers=headers)
        if poll.status_code >= 400:
            raise RuntimeError(f"replicate polling failed ({poll.status_code}): {poll.text[:300]}")
        data = poll.json()
        status = data.get("status")
        if status in {"succeeded", "failed", "canceled"}:
            return data
    raise RuntimeError("replicate prediction timed out while polling")


async def _generate_image_replicate(
    *,
    prompt: str,
    model: str,
    style_ref_summaries: List[Dict[str, str]],
) -> str:
    token = os.getenv("REPLICATE_API_TOKEN", "").strip() or os.getenv("STORYBUDDY_IMAGE_API_KEY", "").strip()
    if not token:
        raise RuntimeError("REPLICATE_API_TOKEN (or STORYBUDDY_IMAGE_API_KEY) is required for replicate provider")

    identifier = _replicate_model_identifier(model)
    if not identifier:
        raise RuntimeError(
            "Replicate model identifier missing. Set STORYBUDDY_REPLICATE_MODEL_NANO_BANANA_2 or STORYBUDDY_REPLICATE_MODEL."
        )

    id_field = os.getenv("STORYBUDDY_REPLICATE_IDENTIFIER_FIELD", "version").strip().lower() or "version"
    if id_field not in {"version", "model"}:
        raise RuntimeError("STORYBUDDY_REPLICATE_IDENTIFIER_FIELD must be 'version' or 'model'")

    prompt_field = os.getenv("STORYBUDDY_REPLICATE_PROMPT_FIELD", "prompt").strip() or "prompt"
    base_url = os.getenv("STORYBUDDY_REPLICATE_BASE_URL", "https://api.replicate.com/v1").rstrip("/")
    refs = ", ".join(ref["name"] for ref in style_ref_summaries[:2])

    extra_input_raw = os.getenv("STORYBUDDY_REPLICATE_EXTRA_INPUT_JSON", "").strip()
    extra_input = {}
    if extra_input_raw:
        try:
            extra_input = json.loads(extra_input_raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid STORYBUDDY_REPLICATE_EXTRA_INPUT_JSON: {exc}") from exc

    payload = {
        id_field: identifier,
        "input": {
            prompt_field: f"{prompt}\n\nUse style references: {refs}" if refs else prompt,
            **extra_input,
        },
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Prefer": "wait=60",
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(f"{base_url}/predictions", headers=headers, json=payload)
        if response.status_code >= 400:
            raise RuntimeError(f"replicate request failed ({response.status_code}): {response.text[:500]}")
        data = response.json()

        status = data.get("status")
        if status not in {"succeeded", "failed", "canceled"}:
            get_url = ((data.get("urls") or {}).get("get") or "").strip()
            if get_url:
                data = await _poll_replicate_prediction(client=client, get_url=get_url, headers=headers)

    if data.get("status") != "succeeded":
        err = data.get("error") or data.get("status") or "unknown replicate failure"
        raise RuntimeError(f"replicate generation failed: {err}")

    image_url = _first_image_url(data.get("output"))
    if not image_url:
        raise RuntimeError("replicate succeeded but returned no image output")
    return image_url
