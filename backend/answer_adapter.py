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


def _strip_page_markers(text: str) -> str:
    return re.sub(r"\[Page\s+\d+\]", " ", text, flags=re.I).strip()


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
    raise RuntimeError("Gemini response did not contain JSON")


def _parse_response_text(data: Dict[str, object]) -> str:
    candidates = data.get("candidates") or []
    if not isinstance(candidates, list):
        raise RuntimeError("Gemini response missing candidates")
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
    raise RuntimeError("Gemini response had no text parts")


def _normalize_option_text(text: str) -> str:
    cleaned = _strip_page_markers(text)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"[\"`]+", "", cleaned).strip(" .,:;")
    words = cleaned.split()
    if len(words) > 12:
        cleaned = " ".join(words[:12])
    return cleaned


def _fact_match(candidate: str, facts: List[str]) -> str:
    norm_candidate = re.sub(r"\s+", " ", _strip_page_markers(candidate)).lower()
    for fact in facts:
        norm_fact = re.sub(r"\s+", " ", _strip_page_markers(fact)).lower()
        if norm_candidate == norm_fact:
            return _strip_page_markers(fact)
    return ""


async def generate_answer_options_with_gemini(
    *,
    story_title: str,
    question: str,
    facts: List[str],
    characters: List[str],
    scenes: List[str],
) -> Dict[str, object]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required for AI answer generation")

    model = _normalize_model_name(os.getenv("STORYBUDDY_ANSWER_MODEL", "gemini-2.5-flash"))
    base_url = os.getenv("STORYBUDDY_GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    endpoint = f"{base_url}/models/{model}:generateContent?key={api_key}"

    fact_lines = [f"{idx + 1}. {_strip_page_markers(f)}" for idx, f in enumerate(facts[:40]) if _strip_page_markers(f)]
    character_line = ", ".join(characters[:8]) or "unknown"
    scene_line = ", ".join(scenes[:6]) or "unknown"
    fact_blob = "\n".join(fact_lines) if fact_lines else "No facts available."

    prompt = (
        "You generate answer options for a children's reading-comprehension app.\n"
        "Return ONLY valid JSON with this exact shape:\n"
        '{"options":[{"text":"...","isCorrect":true,"supportFact":"..."},{"text":"...","isCorrect":false,"supportFact":"..."},{"text":"...","isCorrect":false,"supportFact":"..."}]}\n'
        "Rules:\n"
        "- Exactly 3 options.\n"
        "- Exactly one option must have isCorrect=true.\n"
        "- Correct answer must be directly supported by supportFact from the provided facts.\n"
        "- supportFact must be copied from provided facts.\n"
        "- Use short child-friendly option text (max 12 words).\n"
        "- For emotion questions (feel/feeling/emotion), answer with emotions (e.g. Brave, Nervous, Happy).\n"
        "- Do not use page markers, copyright text, or title-page text.\n\n"
        f"Story title: {story_title}\n"
        f"Known characters: {character_line}\n"
        f"Known scenes: {scene_line}\n"
        f"Question: {question}\n"
        "Facts:\n"
        f"{fact_blob}"
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }

    async with httpx.AsyncClient(timeout=35.0) as client:
        response = await client.post(endpoint, json=payload, headers={"Content-Type": "application/json"})

    if response.status_code >= 400:
        raise RuntimeError(f"gemini answer generation failed ({response.status_code}): {response.text[:400]}")

    raw = response.json()
    raw_text = _parse_response_text(raw)
    json_text = _extract_json_text(raw_text)
    parsed = json.loads(json_text)
    options_raw = parsed.get("options")
    if not isinstance(options_raw, list) or len(options_raw) < 3:
        raise RuntimeError("gemini response options missing/invalid")

    options: List[Dict[str, object]] = []
    for item in options_raw[:3]:
        if not isinstance(item, dict):
            continue
        text = _normalize_option_text(str(item.get("text", "")))
        support = _strip_page_markers(str(item.get("supportFact", "")))
        is_correct = bool(item.get("isCorrect"))
        if not text:
            continue
        options.append({"text": text, "isCorrect": is_correct, "supportFact": support})

    if len(options) != 3:
        raise RuntimeError("gemini response did not produce exactly 3 valid options")

    truthy = [idx for idx, option in enumerate(options) if option["isCorrect"]]
    if len(truthy) != 1:
        raise RuntimeError("gemini response must include exactly one correct option")

    correct_idx = truthy[0]
    matched = _fact_match(str(options[correct_idx].get("supportFact", "")), facts[:40])
    if not matched:
        raise RuntimeError("gemini correct supportFact is not grounded in provided facts")
    options[correct_idx]["supportFact"] = matched

    for idx, option in enumerate(options):
        if idx == correct_idx:
            continue
        if not option.get("supportFact"):
            option["supportFact"] = "Distractor option"

    normalized_texts = [re.sub(r"\s+", " ", str(opt["text"]).lower()) for opt in options]
    if len(set(normalized_texts)) != 3:
        raise RuntimeError("gemini returned duplicate option texts")

    return {
        "provider": "gemini",
        "model": model,
        "prompt": prompt,
        "options": options,
    }
