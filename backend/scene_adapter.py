from __future__ import annotations

import json
import os
import re
from typing import Dict, List

import httpx


def _normalize_model_name(model: str) -> str:
    cleaned = (model or "").strip()
    if not cleaned:
        return "gemini-3.1-flash-lite-preview"
    return cleaned[7:] if cleaned.startswith("models/") else cleaned


def _extract_json_text(raw: str) -> str:
    text = raw.strip()
    fenced = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text, flags=re.I)
    if fenced:
        text = fenced.group(1).strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        return match.group(0)
    raise RuntimeError("Gemini scene response did not contain JSON")


def _parse_response_text(data: Dict[str, object]) -> str:
    candidates = data.get("candidates") or []
    if not isinstance(candidates, list):
        raise RuntimeError("Gemini scene response missing candidates")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content") or {}
        if not isinstance(content, dict):
            continue
        parts = content.get("parts") or []
        if not isinstance(parts, list):
            continue
        chunks = []
        for part in parts:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                chunks.append(part["text"])
        text = "\n".join(chunks).strip()
        if text:
            return text
    raise RuntimeError("Gemini scene response had no text")


def _normalize_pdf_base64(pdf_base64: str) -> str:
    value = str(pdf_base64 or "").strip()
    if not value:
        return ""
    if "," in value and value.lower().startswith("data:"):
        value = value.split(",", 1)[1].strip()
    return re.sub(r"\s+", "", value)


def _clean_scene_name(name: str) -> str:
    out = re.sub(r"\s+", " ", str(name or "").strip())
    out = out.strip(" .,:;")
    out = re.sub(r"[\x00-\x1F]", "", out)
    # Keep scene names as location settings ("In the hallway", "At lunch", etc.).
    out = re.sub(r"^(scene|setting)\s*[:\-]\s*", "", out, flags=re.I).strip()
    words = out.split()
    if len(words) > 10:
        out = " ".join(words[:10])
    if out and not re.match(r"^(in|at|on|near|inside|outside|by)\b", out, flags=re.I):
        out = f"In {out}"
    if out and out[:1].islower():
        out = out[:1].upper() + out[1:]
    return out


def _clean_description(text: str) -> str:
    out = re.sub(r"\s+", " ", str(text or "").strip())
    out = out.strip(" .,:;")
    out = re.sub(r"[\x00-\x1F]", "", out)
    words = out.split()
    if len(words) > 24:
        out = " ".join(words[:24])
    return out


def _clean_character_names(value: object) -> List[str]:
    raw: List[str] = []
    if isinstance(value, list):
        raw = [str(item) for item in value]
    elif isinstance(value, str):
        raw = [piece.strip() for piece in value.split(",")]

    out: List[str] = []
    seen = set()
    for item in raw:
        cleaned = re.sub(r"[^A-Za-z\s'-]", " ", item).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        if not cleaned:
            continue
        normalized = cleaned.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(" ".join(tok.capitalize() for tok in cleaned.split()))
        if len(out) >= 8:
            break
    return out


def _looks_like_noise(scene_name: str) -> bool:
    lowered = scene_name.lower()
    if len(lowered) < 4:
        return True
    if len(lowered.split()) > 10:
        return True
    if not re.match(r"^(in|at|on|near|inside|outside|by)\b", lowered):
        return True
    if re.search(r"\b(he|she|they|his|her|their|i|we|you)\b", lowered):
        return True
    banned = [
        "copyright",
        "all rights reserved",
        "learning together series",
        "published",
        "isbn",
        "page",
        "chapter",
        "table of contents",
        "author",
    ]
    return any(token in lowered for token in banned)


def extract_scene_profiles_with_gemini(
    *,
    story_title: str,
    raw_text: str,
    facts: List[str],
    characters: List[str],
    heuristic_scenes: List[str],
    pdf_base64: str = "",
) -> List[Dict[str, object]]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required for AI scene extraction")

    model = _normalize_model_name(
        os.getenv("STORYBUDDY_SCENE_MODEL", "").strip()
        or os.getenv("STORYBUDDY_CHARACTER_MODEL", "").strip()
        or os.getenv("STORYBUDDY_ANSWER_MODEL", "").strip()
        or "gemini-3.1-flash-lite-preview"
    )
    base_url = os.getenv("STORYBUDDY_GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    endpoint = f"{base_url}/models/{model}:generateContent?key={api_key}"

    facts_blob = "\n".join(f"- {f}" for f in facts[:50])
    chars_blob = ", ".join(characters[:20]) or "none"
    hints_blob = ", ".join(heuristic_scenes[:20]) or "none"
    prompt = (
        "Extract story SETTINGS (locations where events happen) from this children's story and attach participating characters.\n"
        "Return ONLY valid JSON with this exact shape:\n"
        '{"scenes":[{"name":"...","description":"...","characters":["..."]}]}\n'
        "Rules:\n"
        "- Include recurring location settings only (max 12).\n"
        "- Exclude title-page, copyright, and publisher boilerplate.\n"
        "- name must be a setting phrase that starts with a location preposition: In/At/On/Near/Inside/Outside/By.\n"
        "- Example names: In the classroom, In the hallway, At lunch, In speech room.\n"
        "- description: short visual location summary (8-24 words).\n"
        "- characters: subset of known characters present in that scene.\n"
        "- Preserve story wording when possible.\n"
        "- Do not output actions/events as scene names.\n\n"
        f"Story title: {story_title}\n"
        f"Known characters: {chars_blob}\n"
        f"Heuristic scene hints: {hints_blob}\n"
        "Story facts:\n"
        f"{facts_blob}\n\n"
        "Story text excerpt:\n"
        f"{raw_text[:28000]}"
    )

    parts: List[Dict[str, object]] = [{"text": prompt}]
    normalized_pdf = _normalize_pdf_base64(pdf_base64)
    if normalized_pdf:
        parts.append(
            {
                "inline_data": {
                    "mime_type": "application/pdf",
                    "data": normalized_pdf,
                }
            }
        )

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }

    with httpx.Client(timeout=45.0) as client:
        response = client.post(endpoint, json=payload, headers={"Content-Type": "application/json"})

    if response.status_code >= 400:
        raise RuntimeError(f"gemini scene extraction failed ({response.status_code}): {response.text[:400]}")

    raw = response.json()
    raw_text = _parse_response_text(raw)
    parsed = json.loads(_extract_json_text(raw_text))
    items = parsed.get("scenes")
    if not isinstance(items, list):
        raise RuntimeError("gemini scene extraction returned invalid schema")

    out: List[Dict[str, object]] = []
    seen = set()
    known_character_keys = {name.strip().lower(): name.strip() for name in characters if name.strip()}
    for item in items:
        if not isinstance(item, dict):
            continue
        name = _clean_scene_name(str(item.get("name", "")))
        description = _clean_description(str(item.get("description", "")))
        scene_characters = _clean_character_names(item.get("characters", []))
        scene_characters = [known_character_keys.get(name.lower(), name) for name in scene_characters]
        if not name or _looks_like_noise(name):
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        if not description:
            description = "Important scene in the story."
        out.append({"name": name, "description": description, "characters": scene_characters})
        if len(out) >= 12:
            break

    if not out:
        raise RuntimeError("gemini returned no valid scenes")
    return out
