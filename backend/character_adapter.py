from __future__ import annotations

import json
import os
import re
from typing import Dict, List

import httpx


def _normalize_model_name(model: str) -> str:
    cleaned = (model or "").strip()
    if not cleaned:
        return "gemini-2.5-flash"
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
    raise RuntimeError("Gemini character response did not contain JSON")


def _parse_response_text(data: Dict[str, object]) -> str:
    candidates = data.get("candidates") or []
    if not isinstance(candidates, list):
        raise RuntimeError("Gemini character response missing candidates")
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
    raise RuntimeError("Gemini character response had no text")


def _clean_name(name: str) -> str:
    out = re.sub(r"\s+", " ", str(name or "").strip())
    out = re.sub(r"[^A-Za-z\s'-]", "", out).strip()
    if not out:
        return ""
    return " ".join(token.capitalize() for token in out.split())


def _clean_description(text: str) -> str:
    out = re.sub(r"\s+", " ", str(text or "").strip())
    out = re.sub(r"[\x00-\x1F]", "", out)
    out = out.strip(" .,:;")
    words = out.split()
    if len(words) > 24:
        out = " ".join(words[:24])
    return out


def _is_bad_character_name(name: str) -> bool:
    if len(name) < 2:
        return True
    banned = {
        "The", "A", "An", "And", "But", "When", "Then", "After", "Before", "Page", "Copyright",
        "Inc", "Book", "Story", "Tuesday", "Thursday", "Back", "Every", "By", "With",
    }
    if name in banned:
        return True
    if re.fullmatch(r"[A-Z]{2,}", name):
        return True
    return False


def extract_character_profiles_with_gemini(
    *,
    story_title: str,
    raw_text: str,
    facts: List[str],
    heuristic_characters: List[str],
) -> List[Dict[str, str]]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required for AI character extraction")

    model = _normalize_model_name(os.getenv("STORYBUDDY_ANSWER_MODEL", "gemini-2.5-flash"))
    base_url = os.getenv("STORYBUDDY_GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    endpoint = f"{base_url}/models/{model}:generateContent?key={api_key}"

    facts_blob = "\n".join(f"- {f}" for f in facts[:40])
    heur_blob = ", ".join(heuristic_characters[:20]) or "none"
    prompt = (
        "Extract only real character names from this children's story and provide a short visual description for each.\n"
        "Return ONLY valid JSON with this exact shape:\n"
        '{"characters":[{"name":"...","description":"..."}]}\n'
        "Rules:\n"
        "- Include major recurring characters only (max 12).\n"
        "- Exclude titles, page labels, copyright/publisher text, weekdays, and random nouns.\n"
        "- Use concise visual descriptions (6-20 words) helpful for finding that character in book illustrations.\n"
        "- Keep names in title case.\n\n"
        f"Story title: {story_title}\n"
        f"Heuristic names (may include noise): {heur_blob}\n"
        "Story facts:\n"
        f"{facts_blob}\n\n"
        "Story text excerpt:\n"
        f"{raw_text[:28000]}"
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }

    with httpx.Client(timeout=40.0) as client:
        response = client.post(endpoint, json=payload, headers={"Content-Type": "application/json"})

    if response.status_code >= 400:
        raise RuntimeError(f"gemini character extraction failed ({response.status_code}): {response.text[:400]}")

    raw = response.json()
    raw_text = _parse_response_text(raw)
    parsed = json.loads(_extract_json_text(raw_text))
    items = parsed.get("characters")
    if not isinstance(items, list):
        raise RuntimeError("gemini character extraction returned invalid schema")

    out: List[Dict[str, str]] = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        name = _clean_name(str(item.get("name", "")))
        description = _clean_description(str(item.get("description", "")))
        if not name or _is_bad_character_name(name):
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        if not description:
            description = "Main character in the story."
        out.append({"name": name, "description": description})
        if len(out) >= 12:
            break

    if not out:
        raise RuntimeError("gemini returned no valid characters")
    return out
