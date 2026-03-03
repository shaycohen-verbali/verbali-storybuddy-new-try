from __future__ import annotations

import base64
import hashlib
import os
from textwrap import shorten
from typing import Dict, List

import httpx


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
    provider = os.getenv("STORYBUDDY_IMAGE_PROVIDER", "mock").strip().lower()
    if provider in {"", "mock"}:
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

    raise RuntimeError(f"unsupported STORYBUDDY_IMAGE_PROVIDER: {provider}")
