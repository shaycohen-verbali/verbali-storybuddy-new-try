from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import re
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from random import Random
from typing import Dict, List, Tuple

from .answer_adapter import generate_answer_options_with_gemini
from .character_adapter import extract_character_profiles_with_gemini
from .image_adapter import canonicalize_model, generate_image
from .models import (
    AnswerCard,
    AnswerOption,
    AskResponse,
    CardDebug,
    CharacterProfile,
    CharacterStyleMap,
    SceneStyleMap,
    SetupIngestRequest,
    SetupIngestResponse,
    StoryPackage,
    StyleProfile,
    StyleRef,
)

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover - optional dependency in runtime
    PdfReader = None

try:
    from PIL import Image
except Exception:  # pragma: no cover - optional dependency in runtime
    Image = None


STOP_WORDS = {
    "about", "after", "again", "also", "because", "before", "being", "came", "come", "could", "does", "down",
    "each", "even", "from", "have", "into", "just", "like", "many", "more", "most", "much", "only", "other",
    "over", "same", "some", "such", "than", "that", "their", "them", "then", "there", "these", "they", "this",
    "those", "through", "very", "what", "when", "where", "which", "while", "with", "would",
}

logger = logging.getLogger("storybuddy.pipeline")

FEELING_KEYWORDS = {
    "happy",
    "sad",
    "scared",
    "afraid",
    "brave",
    "nervous",
    "angry",
    "excited",
    "calm",
    "embarrassed",
    "proud",
    "frustrated",
    "worried",
    "confident",
    "shy",
    "lonely",
    "tired",
    "upset",
}


class CardImageGenerationError(RuntimeError):
    def __init__(self, *, card_id: str, model: str, detail: str):
        self.card_id = card_id
        self.model = model
        self.detail = detail
        super().__init__(f"{card_id} image generation failed for model {model}: {detail}")


class AnswerGenerationError(RuntimeError):
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ingest_setup(
    req: SetupIngestRequest,
    existing: StoryPackage | None = None,
    *,
    enforce_character_mapping: bool = True,
) -> SetupIngestResponse:
    text = (req.book_text or "").strip()
    if not text and req.pdf_base64:
        text = extract_text_from_pdf_base64(req.pdf_base64)
    if not text:
        raise ValueError("book text is required (bookText or pdfBase64)")

    learned = analyze_book_text(text)
    character_profiles = build_character_profiles(
        story_title=req.story_title.strip(),
        raw_text=text,
        facts=learned["facts"],
        heuristic_characters=learned["characters"],
        pdf_base64=req.pdf_base64 or "",
    )
    if character_profiles:
        learned["characters"] = [str(row.get("name", "")) for row in character_profiles if str(row.get("name", "")).strip()]
    style_refs = normalize_style_refs(req.style_refs or (existing.style_refs if existing else []))
    style_profile = build_style_profile(style_refs, text)
    character_map = build_character_style_map(
        characters=learned["characters"],
        style_refs=style_refs,
        explicit_hints=req.character_image_hints,
        descriptions={str(row.get("name", "")): str(row.get("description", "")) for row in character_profiles},
        species_by_name={str(row.get("name", "")): str(row.get("species", "")) for row in character_profiles},
        traits_by_name={
            str(row.get("name", "")): [str(item) for item in (row.get("appearanceTraits", []) or [])]
            for row in character_profiles
        },
        vibe_by_name={str(row.get("name", "")): str(row.get("visualVibe", "")) for row in character_profiles},
    )
    if enforce_character_mapping:
        missing = [row.character for row in character_map if not row.ref_ids]
        if missing:
            raise ValueError(
                "Each identified character must be mapped to at least one reference image. "
                f"Missing mappings: {', '.join(missing[:10])}"
            )
    scene_map = build_scene_style_map(
        scenes=learned["scenes"],
        style_refs=style_refs,
    )

    now = _utc_now()
    package_id = req.package_id or (existing.id if existing else f"pkg-{uuid.uuid4().hex[:12]}")
    package = StoryPackage(
        id=package_id,
        title=req.story_title.strip(),
        raw_text=text,
        facts=learned["facts"],
        scenes=learned["scenes"],
        characters=learned["characters"],
        character_profiles=[
            CharacterProfile(
                name=str(row.get("name", "")),
                description=str(row.get("description", "")),
                species=str(row.get("species", "")),
                appearance_traits=[str(item) for item in (row.get("appearanceTraits", []) or [])],
                visual_vibe=str(row.get("visualVibe", "")),
            )
            for row in character_profiles
            if str(row.get("name", "")).strip()
        ],
        objects=learned["objects"],
        style_refs=style_refs,
        style_profile=style_profile,
        character_style_map=character_map,
        scene_style_map=scene_map,
        created_at=existing.created_at if existing else now,
        updated_at=now,
    )

    summary = {
        "facts": len(package.facts),
        "characters": len(package.characters),
        "objects": len(package.objects),
        "scenes": len(package.scenes),
        "styleRefs": len(package.style_refs),
        "characterMappings": sum(1 for m in package.character_style_map if m.ref_ids),
        "sceneMappings": sum(1 for m in package.scene_style_map if m.ref_ids),
    }

    return SetupIngestResponse(package=package, learnedSummary=summary)


def build_character_profiles(
    *,
    story_title: str,
    raw_text: str,
    facts: List[str],
    heuristic_characters: List[str],
    pdf_base64: str = "",
) -> List[Dict[str, object]]:
    try:
        rows = extract_character_profiles_with_gemini(
            story_title=story_title,
            raw_text=raw_text,
            facts=facts,
            heuristic_characters=heuristic_characters,
            pdf_base64=pdf_base64,
        )
        logger.info("character extraction provider=gemini count=%s", len(rows))
        return rows
    except Exception as exc:
        allow_fallback = os.getenv("STORYBUDDY_ALLOW_CHARACTER_FALLBACK", "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if allow_fallback:
            logger.warning("character extraction with gemini failed, using fallback: %s", exc)
            return build_fallback_character_profiles(heuristic_characters, facts)
        raise ValueError(f"AI character extraction failed: {exc}") from exc


def build_fallback_character_profiles(characters: List[str], facts: List[str]) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    seen = set()
    for character in characters[:12]:
        name = character.strip()
        if not name:
            continue
        key = normalize(name)
        if key in seen:
            continue
        seen.add(key)
        fact = next((f for f in facts if re.search(rf"\b{re.escape(name)}\b", f, flags=re.I)), "")
        description = "Character mentioned in the story."
        if fact:
            snippet = strip_page_markers(fact)
            description = truncate(snippet, 120)
        out.append(
            {
                "name": name,
                "description": description,
                "species": "Unknown",
                "appearanceTraits": [description],
                "visualVibe": "Friendly storybook character",
            }
        )
    return out


def extract_text_from_pdf_base64(pdf_b64: str) -> str:
    if not PdfReader:
        return ""

    if "," in pdf_b64:
        pdf_b64 = pdf_b64.split(",", 1)[1]

    raw = base64.b64decode(pdf_b64)
    reader = PdfReader(io.BytesIO(raw))
    pages = []
    for idx, page in enumerate(reader.pages, start=1):
        page_text = (page.extract_text() or "").strip()
        if page_text:
            pages.append(f"[Page {idx}] {page_text}")
    return clean_text("\n\n".join(pages))


def analyze_book_text(text: str) -> Dict[str, List[str]]:
    cleaned = clean_text(strip_page_markers(text))
    sentences = split_story_sentences(cleaned)

    facts = [truncate(strip_page_markers(s), 200) for s in sentences[:40]]
    characters = extract_characters(cleaned)[:12]
    objects = extract_objects(cleaned, characters)[:20]
    scenes = extract_scenes(sentences)[:12]

    return {
        "facts": facts,
        "characters": characters,
        "objects": objects,
        "scenes": scenes,
    }


def split_story_sentences(text: str) -> List[str]:
    # Preserve sentence integrity for honorifics like "Mrs. Bloom".
    protected = str(text or "")
    for abbr in ["Mr.", "Mrs.", "Ms.", "Dr.", "Prof.", "St.", "Sr.", "Jr."]:
        protected = re.sub(rf"\b{re.escape(abbr)}", abbr.replace(".", "<prd>"), protected, flags=re.I)
    parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", protected) if part.strip()]
    out = [part.replace("<prd>", ".") for part in parts]
    return out


def build_style_profile(style_refs: List[StyleRef], text: str) -> StyleProfile:
    notes = []
    lowered = text.lower()
    for word in ["warm", "gentle", "storybook", "painted", "whimsical", "soft", "pastel", "bright", "watercolor"]:
        if word in lowered:
            notes.append(word)

    palette = []
    textures = []
    if Image and style_refs:
        for ref in style_refs[:5]:
            try:
                data = data_url_to_bytes(ref.data_url)
                img = Image.open(io.BytesIO(data)).convert("RGB").resize((64, 64))
                channels = list(img.split())
                rgb = tuple(int(sum(ch.getdata()) / len(ch.getdata())) for ch in channels)
                palette.append(f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}")
                textures.append("illustrated" if img.width >= img.height else "portrait")
            except Exception:
                continue

    if not notes:
        notes = ["storybook", "friendly", "clear silhouettes"]
    if not palette:
        palette = ["#f3ce9a", "#8bc7a7", "#e36e4f"]
    if not textures:
        textures = ["painted", "soft lines"]

    return StyleProfile(
        notes=dedupe(notes)[:6],
        dominant_palette=dedupe(palette)[:5],
        texture_keywords=dedupe(textures)[:5],
    )


def normalize_style_refs(style_refs: List[StyleRef]) -> List[StyleRef]:
    rows: List[StyleRef] = []
    for ref in style_refs:
        rows.append(
            StyleRef(
                id=ref.id,
                name=ref.name,
                dataUrl=ref.data_url,
                characterHints=dedupe([capitalize(tok.strip()) for tok in ref.character_hints if tok.strip()])[:8],
                sceneHints=dedupe([capitalize(tok.strip()) for tok in ref.scene_hints if tok.strip()])[:8],
                sourceType=(ref.source_type or "manual").strip().lower() or "manual",
                pageNumber=ref.page_number,
                pageTextSnippet=truncate((ref.page_text_snippet or "").strip(), 160) or None,
            )
        )
    return rows


def _ref_text_blob(ref: StyleRef) -> str:
    parts = [ref.name, *(ref.character_hints or []), *(ref.scene_hints or []), ref.page_text_snippet or ""]
    return " ".join(part for part in parts if part).lower()


def _score_ref_for_character(
    ref: StyleRef,
    character: str,
    *,
    description: str = "",
    species: str = "",
    appearance_traits: List[str] | None = None,
) -> int:
    score = 0
    target = character.strip().lower()
    tokens = tokenize_name(character)
    description = description.strip().lower()
    species = species.strip().lower()
    trait_tokens: List[str] = []
    for trait in (appearance_traits or []):
        trait_tokens.extend(tokenize(trait))
    blob = _ref_text_blob(ref)

    if any(target == hint.lower() for hint in ref.character_hints):
        score += 8
    if target in blob:
        score += 4
    if species and species in blob:
        score += 2
    if description:
        score += sum(1 for tok in tokenize(description) if tok in blob)
    if trait_tokens:
        score += sum(1 for tok in trait_tokens if tok in blob)
    score += sum(2 for tok in tokens if tok in ref.name.lower())
    score += sum(1 for tok in tokens if tok in blob)
    return score


def _score_ref_for_scene(ref: StyleRef, scene: str) -> int:
    score = 0
    target = normalize(scene)
    tokens = [tok for tok in tokenize(scene) if tok not in {"inside", "outside", "near"}]
    blob = _ref_text_blob(ref)

    if any(target == normalize(hint) for hint in ref.scene_hints):
        score += 8
    if target in normalize(blob):
        score += 4
    score += sum(2 for tok in tokens if tok in ref.name.lower())
    score += sum(1 for tok in tokens if tok in blob)
    return score


def build_character_style_map(
    *,
    characters: List[str],
    style_refs: List[StyleRef],
    explicit_hints: Dict[str, List[str]],
    descriptions: Dict[str, str] | None = None,
    species_by_name: Dict[str, str] | None = None,
    traits_by_name: Dict[str, List[str]] | None = None,
    vibe_by_name: Dict[str, str] | None = None,
) -> List[CharacterStyleMap]:
    descriptions = descriptions or {}
    species_by_name = species_by_name or {}
    traits_by_name = traits_by_name or {}
    vibe_by_name = vibe_by_name or {}
    ref_lookup = {r.id: r for r in style_refs}
    rows: List[CharacterStyleMap] = []

    for character in characters:
        matched_ids: List[str] = []

        if character in explicit_hints:
            for ref_id in explicit_hints[character]:
                if ref_id in ref_lookup and ref_id not in matched_ids:
                    matched_ids.append(ref_id)
            if matched_ids:
                rows.append(
                    CharacterStyleMap(
                        character=character,
                        description=descriptions.get(character, ""),
                        species=species_by_name.get(character, ""),
                        appearance_traits=traits_by_name.get(character, [])[:8],
                        visual_vibe=vibe_by_name.get(character, ""),
                        ref_ids=matched_ids[:2],
                        confidence=0.95,
                    )
                )
                continue

        description = descriptions.get(character, "")
        species = species_by_name.get(character, "")
        appearance_traits = traits_by_name.get(character, [])
        scored = sorted(
            (
                (
                    _score_ref_for_character(
                        ref,
                        character,
                        description=description,
                        species=species,
                        appearance_traits=appearance_traits,
                    ),
                    ref.id,
                )
                for ref in style_refs
            ),
            reverse=True,
        )
        for score, ref_id in scored:
            if score <= 0:
                continue
            matched_ids.append(ref_id)
            if len(matched_ids) >= 2:
                break

        if not matched_ids and style_refs:
            matched_ids = [style_refs[0].id]

        confidence = 0.2
        if matched_ids:
            confidence = 0.7
            top_score = scored[0][0] if scored else 0
            if top_score >= 8:
                confidence = 0.9

        rows.append(
            CharacterStyleMap(
                character=character,
                description=descriptions.get(character, ""),
                species=species_by_name.get(character, ""),
                appearance_traits=traits_by_name.get(character, [])[:8],
                visual_vibe=vibe_by_name.get(character, ""),
                ref_ids=matched_ids[:2],
                confidence=confidence,
            )
        )

    return rows


def build_scene_style_map(
    *,
    scenes: List[str],
    style_refs: List[StyleRef],
) -> List[SceneStyleMap]:
    rows: List[SceneStyleMap] = []
    for scene in scenes:
        matched_ids: List[str] = []
        scored = sorted(
            ((_score_ref_for_scene(ref, scene), ref.id) for ref in style_refs),
            reverse=True,
        )
        for score, ref_id in scored:
            if score <= 0:
                continue
            matched_ids.append(ref_id)
            if len(matched_ids) >= 2:
                break

        if not matched_ids and style_refs:
            matched_ids = [style_refs[0].id]

        confidence = 0.2
        if matched_ids:
            confidence = 0.65
            top_score = scored[0][0] if scored else 0
            if top_score >= 8:
                confidence = 0.9

        rows.append(SceneStyleMap(scene=scene, ref_ids=matched_ids[:2], confidence=confidence))
    return rows


async def run_ask_pipeline(package: StoryPackage, question: str, model: str) -> AskResponse:
    t0 = time.perf_counter()
    timeline: List[Dict[str, object]] = []

    def begin(event: str, lane: str, meta: Dict[str, object] | None = None) -> Dict[str, object]:
        item = {
            "event": event,
            "lane": lane,
            "meta": meta or {},
            "startMs": ms_since(t0),
            "endMs": 0,
            "durationMs": 0,
        }
        timeline.append(item)
        return item

    def end(item: Dict[str, object]) -> None:
        item["endMs"] = ms_since(t0)
        item["durationMs"] = int(item["endMs"]) - int(item["startMs"])

    t = begin("transcription", "main")
    transcript = question.strip()
    end(t)
    question_character_matches = resolve_question_characters(package, transcript)

    t = begin("answer_option_generation", "main")
    options, answer_generation = await generate_answer_options(package, transcript)
    t["meta"] = {"provider": answer_generation.get("provider"), "model": answer_generation.get("model")}
    end(t)

    resolved_model = canonicalize_model(model)

    fanout = begin("image_fanout", "main", {"cardCount": 3})
    tasks = [
        _generate_card(
            package=package,
            question=transcript,
            option=option,
            model=resolved_model,
            lane=f"card-{idx}",
            t0=t0,
            timeline=timeline,
            question_characters=question_character_matches,
        )
        for idx, option in enumerate(options, start=1)
    ]
    try:
        cards = await asyncio.gather(*tasks)
    except CardImageGenerationError:
        raise
    except Exception as exc:
        raise CardImageGenerationError(card_id="card-unknown", model=resolved_model, detail=str(exc)) from exc
    end(fanout)

    t = begin("last_image_interactive", "main")
    end(t)

    step_timings = summarize_step_timings(timeline)

    debug_bundle = {
        "request": {
            "storyPackageId": package.id,
            "storyTitle": package.title,
            "model": resolved_model,
            "transcript": transcript,
            "questionCharacterMatches": question_character_matches,
        },
        "answerGeneration": answer_generation,
        "options": [
            {
                "text": option.text,
                "isCorrect": option.is_correct,
                "supportFact": option.support_fact,
            }
            for option in options
        ],
        "cards": [
            {
                "id": f"card-{idx+1}",
                **card.debug.model_dump(by_alias=True),
                "cardTiming": card.card_timing,
            }
            for idx, card in enumerate(cards)
        ],
        "telemetry": {
            "stepTimings": step_timings,
            "timeline": sorted(timeline, key=lambda item: int(item["startMs"])),
            "completedAt": datetime.now(timezone.utc).isoformat(),
        },
    }

    telemetry = {
        "stepTimings": step_timings,
        "timeline": sorted(timeline, key=lambda item: int(item["startMs"])),
    }

    return AskResponse(cards=cards, telemetry=telemetry, debugBundle=debug_bundle)


async def _generate_card(
    *,
    package: StoryPackage,
    question: str,
    option: AnswerOption,
    model: str,
    lane: str,
    t0: float,
    timeline: List[Dict[str, object]],
    question_characters: List[str],
) -> AnswerCard:
    def begin(event: str, meta: Dict[str, object] | None = None) -> Dict[str, object]:
        item = {
            "event": event,
            "lane": lane,
            "meta": meta or {},
            "startMs": ms_since(t0),
            "endMs": 0,
            "durationMs": 0,
        }
        timeline.append(item)
        return item

    def end(item: Dict[str, object]) -> None:
        item["endMs"] = ms_since(t0)
        item["durationMs"] = int(item["endMs"]) - int(item["startMs"])

    e = begin("participant_resolver")
    participants = resolve_participants(package, question, option.text, option.support_fact)
    warnings: List[str] = []
    selected_chars = [str(name) for name in participants.get("characters", [])]
    if question_characters and not set(normalize(name) for name in question_characters).intersection(
        normalize(name) for name in selected_chars
    ):
        warnings.append(
            f"Question mentions {', '.join(question_characters)} but selected participants were {', '.join(selected_chars) or 'none'}."
        )
    if warnings:
        participants["warnings"] = warnings
    end(e)

    e = begin("style_ref_selection")
    style_refs_used = select_style_refs(package, participants)
    end(e)

    e = begin("illustration_plan")
    illustration_prompt = build_illustration_prompt(package, question, option, participants, style_refs_used)
    end(e)

    e = begin("image_generation", {"model": model})
    style_ref_images = select_style_ref_images(package, style_refs_used)
    logger.info(
        "card reference selection lane=%s refs=%s",
        lane,
        ", ".join(
            f"{ref.get('id')}:{ref.get('name')}:{ref.get('reason')}:{ref.get('sourceType')}:{ref.get('pageNumber')}"
            for ref in style_refs_used
        ) or "none",
    )
    try:
        image_data_url = await generate_image(
            prompt=illustration_prompt,
            model=model,
            style_ref_images=style_ref_images,
            style_ref_labels=[str(ref.get("name", "")) for ref in style_refs_used],
            trace_id=lane,
        )
    except Exception as exc:
        raise CardImageGenerationError(card_id=lane, model=model, detail=str(exc)) from exc
    end(e)

    card_timing = {}
    for evt in timeline:
        if evt["lane"] == lane and evt["event"] in {
            "participant_resolver",
            "style_ref_selection",
            "illustration_plan",
            "image_generation",
        }:
            key = {
                "participant_resolver": "participantResolverMs",
                "style_ref_selection": "styleRefSelectionMs",
                "illustration_plan": "illustrationPlanMs",
                "image_generation": "imageGenerationMs",
            }[evt["event"]]
            card_timing[key] = int(evt["durationMs"])

    card_timing["totalMs"] = sum(card_timing.values())

    return AnswerCard(
        text=option.text,
        isCorrect=option.is_correct,
        imageDataUrl=image_data_url,
        cardTiming=card_timing,
        debug=CardDebug(
            prompts={
                "optionPrompt": f"Answer this question from book facts: {question}",
                "illustrationPrompt": illustration_prompt,
            },
            selectedParticipants=participants,
            styleRefsUsed=style_refs_used,
            modelUsed=model,
            imageProvider="replicate",
            generationError=None,
            supportFact=option.support_fact,
        ),
    )


def summarize_step_timings(timeline: List[Dict[str, object]]) -> Dict[str, int]:
    out = {
        "transcriptionMs": 0,
        "answerOptionGenerationMs": 0,
        "imageFanoutMs": 0,
        "totalMs": 0,
    }
    for item in timeline:
        evt = item["event"]
        if evt == "transcription":
            out["transcriptionMs"] = int(item["durationMs"])
        elif evt == "answer_option_generation":
            out["answerOptionGenerationMs"] = int(item["durationMs"])
        elif evt == "image_fanout":
            out["imageFanoutMs"] = int(item["durationMs"])

    if timeline:
        out["totalMs"] = max(int(item["endMs"]) for item in timeline)
    return out


async def generate_answer_options(package: StoryPackage, question: str) -> Tuple[List[AnswerOption], Dict[str, object]]:
    try:
        ai = await generate_answer_options_with_gemini(
            story_title=package.title,
            question=question,
            facts=package.facts,
            characters=package.characters,
            scenes=package.scenes,
        )
        options = [
            AnswerOption(
                text=str(item.get("text", "")),
                isCorrect=bool(item.get("isCorrect")),
                supportFact=str(item.get("supportFact", "")),
            )
            for item in ai.get("options", [])
        ]
        if len(options) == 3 and sum(1 for item in options if item.is_correct) == 1:
            return options, {
                "provider": ai.get("provider", "gemini"),
                "model": ai.get("model", "gemini-2.5-flash"),
                "prompt": ai.get("prompt", ""),
                "mode": "ai",
            }
        raise RuntimeError("invalid option shape from gemini")
    except Exception as exc:
        allow_fallback = os.getenv("STORYBUDDY_ALLOW_RULE_BASED_FALLBACK", "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if allow_fallback:
            logger.warning("gemini answer generation failed, using fallback: %s", exc)
            return generate_answer_options_rule_based(package, question), {
                "provider": "rule_based_fallback",
                "model": "none",
                "prompt": "",
                "error": str(exc),
                "mode": "fallback",
            }
        raise AnswerGenerationError(f"Gemini answer generation failed: {exc}") from exc


def generate_answer_options_rule_based(package: StoryPackage, question: str) -> List[AnswerOption]:
    feeling = generate_feeling_answer_options(package, question)
    if feeling:
        rnd = Random(hash(question) & 0xFFFFFFFF)
        rnd.shuffle(feeling)
        return feeling

    special = generate_special_answer_options(package, question)
    if special:
        rnd = Random(hash(question) & 0xFFFFFFFF)
        rnd.shuffle(special)
        return special

    facts = package.facts[:30]
    scored = sorted(((score_fact_against_question(f, question), f) for f in facts), reverse=True)
    correct_fact = scored[0][1] if scored else (facts[0] if facts else "")
    wrong_facts = [fact for _score, fact in scored[1:]]

    correct_answer = answer_from_fact(question, correct_fact, package)
    distractors: List[str] = []
    for fact in wrong_facts:
        candidate = answer_from_fact(question, fact, package)
        if candidate and normalize(candidate) != normalize(correct_answer) and normalize(candidate) not in {normalize(d) for d in distractors}:
            distractors.append(candidate)
        if len(distractors) == 2:
            break

    while len(distractors) < 2:
        distractors.append(build_synthetic_distractor(package, correct_answer, distractors))

    options = [
        AnswerOption(text=correct_answer, isCorrect=True, supportFact=correct_fact),
        AnswerOption(text=distractors[0], isCorrect=False, supportFact=wrong_facts[0] if wrong_facts else "Synthetic distractor"),
        AnswerOption(text=distractors[1], isCorrect=False, supportFact=wrong_facts[1] if len(wrong_facts) > 1 else "Synthetic distractor"),
    ]

    rnd = Random(hash(question) & 0xFFFFFFFF)
    rnd.shuffle(options)
    return options


def resolve_participants(package: StoryPackage, question: str, option_text: str, support_fact: str) -> Dict[str, object]:
    alias_index = build_character_alias_index(package)
    text = f"{question} {option_text} {support_fact}"
    text_lower = text.lower()
    chars = match_character_names(text, alias_index)[:3]
    if not chars:
        chars = find_characters_from_related_facts(package, support_fact, alias_index)[:3]
    objects = [obj for obj in package.objects if obj.lower() in text_lower][:3]
    scene = next(
        (scene for scene in package.scenes if normalize(scene) in normalize(text)),
        package.scenes[0] if package.scenes else "Main story setting",
    )

    if not chars and package.characters:
        chars = package.characters[:1]

    return {
        "scene": scene,
        "characters": chars,
        "objects": objects,
    }


def resolve_question_characters(package: StoryPackage, question: str) -> List[str]:
    return match_character_names(question, build_character_alias_index(package))


def build_character_alias_index(package: StoryPackage) -> Dict[str, str]:
    canonical = [name for name in package.characters if name.strip()]
    if not canonical and package.character_profiles:
        canonical = [row.name for row in package.character_profiles if row.name.strip()]
    if not canonical and package.character_style_map:
        canonical = [row.character for row in package.character_style_map if row.character.strip()]
    canonical_norm = {normalize(name) for name in canonical}
    for row in package.character_style_map:
        if row.character.strip() and normalize(row.character) not in canonical_norm:
            canonical.append(row.character.strip())
            canonical_norm.add(normalize(row.character))

    index: Dict[str, str] = {}
    for name in canonical:
        aliases = aliases_for_character_name(name)
        for alias in aliases:
            if alias and alias not in index:
                index[alias] = name
    return index


def aliases_for_character_name(name: str) -> List[str]:
    cleaned = normalize_character_alias(name)
    if not cleaned:
        return []
    parts = cleaned.split()
    aliases = {cleaned}
    if len(parts) >= 2:
        last = parts[-1]
        aliases.add(last)
        if parts[0] in {"mr", "mrs", "ms", "miss", "dr"}:
            base = " ".join(parts[1:])
            aliases.add(base)
            aliases.add(f"ms {base}")
            aliases.add(f"mrs {base}")
            aliases.add(f"miss {base}")
    return sorted(aliases, key=len, reverse=True)


def normalize_character_alias(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", " ", str(value or "").lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def match_character_names(text: str, alias_index: Dict[str, str]) -> List[str]:
    source = f" {normalize_character_alias(text)} "
    matched: List[str] = []
    seen = set()
    for alias, canonical in sorted(alias_index.items(), key=lambda item: len(item[0]), reverse=True):
        if not alias:
            continue
        if f" {alias} " in source:
            key = normalize(canonical)
            if key not in seen:
                seen.add(key)
                matched.append(canonical)
    return matched


def find_characters_from_related_facts(package: StoryPackage, support_fact: str, alias_index: Dict[str, str]) -> List[str]:
    support_tokens = set(tokenize(support_fact))
    if not support_tokens:
        return []
    out: List[str] = []
    seen = set()
    for fact in package.facts[:40]:
        fact_tokens = set(tokenize(fact))
        if not fact_tokens:
            continue
        overlap = support_tokens & fact_tokens
        if not overlap:
            continue
        for name in match_character_names(fact, alias_index):
            key = normalize(name)
            if key not in seen:
                seen.add(key)
                out.append(name)
    return out


def generate_special_answer_options(package: StoryPackage, question: str) -> List[AnswerOption] | None:
    q = question.lower()
    asks_main_characters = ("character" in q or "characters" in q) and ("main" in q or "who" in q or "what" in q)
    asks_three = bool(re.search(r"\b(three|3)\b", q))
    if not asks_main_characters:
        return None

    characters = [name for name in package.characters if name and len(name) > 1][:6]
    if len(characters) < 2:
        return None

    target_count = 3 if asks_three else min(3, len(characters))
    correct_names = characters[:target_count]
    correct_text = format_name_list(correct_names)

    supporting = []
    for name in correct_names:
        fact = next((f for f in package.facts if re.search(rf"\b{re.escape(name)}\b", f, flags=re.I)), "")
        if fact:
            supporting.append(strip_page_markers(fact))
    support_fact = " | ".join(supporting[:3]) or f"Main characters in the story include: {correct_text}."

    pool = dedupe(characters[target_count:] + [capitalize(obj) for obj in package.objects[:6]] + ["Someone else", "Another character"])
    if not pool:
        pool = ["Someone else", "Another character", "A classmate"]

    def make_wrong(seed: int) -> str:
        picks: List[str] = []
        for idx in range(target_count):
            candidate = pool[(seed + idx) % len(pool)]
            if candidate not in picks:
                picks.append(candidate)
        while len(picks) < target_count:
            picks.append(f"Extra {len(picks)+1}")
        return format_name_list(picks[:target_count])

    wrong1 = make_wrong(0)
    wrong2 = make_wrong(2 if len(pool) > 2 else 1)
    if normalize(wrong1) == normalize(correct_text):
        wrong1 = make_wrong(1)
    if normalize(wrong2) in {normalize(correct_text), normalize(wrong1)}:
        wrong2 = make_wrong(3)

    return [
        AnswerOption(text=correct_text, isCorrect=True, supportFact=support_fact),
        AnswerOption(text=wrong1, isCorrect=False, supportFact="Distractor option"),
        AnswerOption(text=wrong2, isCorrect=False, supportFact="Distractor option"),
    ]


def generate_feeling_answer_options(package: StoryPackage, question: str) -> List[AnswerOption] | None:
    q = question.lower()
    asks_feeling = ("feel" in q or "feeling" in q or "emotion" in q) and ("how" in q or "what" in q)
    if not asks_feeling:
        return None

    candidates: List[tuple[str, str, int]] = []
    for fact in package.facts:
        for feeling in extract_feelings_from_fact(fact):
            score = score_fact_against_question(fact, question)
            if "not " in feeling:
                score += 1
            if any(name.lower() in fact.lower() for name in package.characters[:3]):
                score += 1
            candidates.append((feeling, strip_page_markers(fact), score))

    if not candidates:
        return None

    candidates.sort(key=lambda row: row[2], reverse=True)
    correct_feeling, support_fact, _ = candidates[0]

    distractor_pool = dedupe([row[0] for row in candidates[1:]] + [name for name in FEELING_KEYWORDS if name != correct_feeling])
    distractors: List[str] = []
    for candidate in distractor_pool:
        if normalize(candidate) == normalize(correct_feeling):
            continue
        if normalize(candidate) in {normalize(d) for d in distractors}:
            continue
        distractors.append(candidate)
        if len(distractors) == 2:
            break

    while len(distractors) < 2:
        fallback = ["happy", "nervous", "sad", "calm"][len(distractors) % 4]
        if normalize(fallback) != normalize(correct_feeling):
            distractors.append(fallback)

    return [
        AnswerOption(text=capitalize(correct_feeling), isCorrect=True, supportFact=support_fact),
        AnswerOption(text=capitalize(distractors[0]), isCorrect=False, supportFact="Distractor option"),
        AnswerOption(text=capitalize(distractors[1]), isCorrect=False, supportFact="Distractor option"),
    ]


def select_style_refs(package: StoryPackage, participants: Dict[str, object]) -> List[Dict[str, object]]:
    chars = [str(x) for x in participants.get("characters", [])]
    scene = str(participants.get("scene", ""))
    chosen_ids: List[str] = []
    out: List[Dict[str, object]] = []
    ref_lookup = {r.id: r for r in package.style_refs}

    def add_ref(ref_id: str, *, reason: str, entity: str) -> None:
        if ref_id in chosen_ids:
            return
        if ref_id not in ref_lookup:
            return
        row = ref_lookup[ref_id]
        chosen_ids.append(ref_id)
        out.append(
            {
                "id": ref_id,
                "name": row.name,
                "sourceType": row.source_type,
                "pageNumber": row.page_number,
                "reason": reason,
                "entity": entity,
            }
        )

    for ch in chars:
        row = next((m for m in package.character_style_map if normalize(m.character) == normalize(ch)), None)
        if row:
            for rid in row.ref_ids:
                add_ref(rid, reason="character_map", entity=ch)

    scene_row = next((m for m in package.scene_style_map if normalize(m.scene) == normalize(scene)), None)
    if scene_row:
        for rid in scene_row.ref_ids:
            add_ref(rid, reason="scene_map", entity=scene)

    for ref in package.style_refs:
        if len(chosen_ids) >= 3:
            break
        add_ref(ref.id, reason="fallback", entity="story_style")

    return out[:3]


def select_style_ref_images(package: StoryPackage, refs_used: List[Dict[str, object]]) -> List[str]:
    ref_lookup = {r.id: r for r in package.style_refs}
    out: List[str] = []
    for ref in refs_used[:3]:
        ref_id = ref.get("id")
        if not isinstance(ref_id, str):
            continue
        row = ref_lookup.get(ref_id)
        if row and row.data_url:
            out.append(row.data_url)
    return out


def build_illustration_prompt(
    package: StoryPackage,
    question: str,
    option: AnswerOption,
    participants: Dict[str, object],
    style_refs_used: List[Dict[str, object]],
) -> str:
    refs = ", ".join(str(ref.get("name", "")).strip() for ref in style_refs_used if str(ref.get("name", "")).strip()) or "book refs"
    style_notes = ", ".join(package.style_profile.notes[:3]) or "storybook"
    palette = ", ".join(package.style_profile.dominant_palette[:3]) or "book palette"
    scene = str(participants.get("scene", "main story setting"))
    chars = ", ".join(participants.get("characters", []) or []) or "main cast"

    return (
        "Illustrate a child-friendly answer card in the exact style of the provided reference images. "
        f"Answer concept: {option.text}. Scene: {scene}. Characters: {chars}. "
        f"Book style cues: {style_notes}. Palette: {palette}. "
        f"Reference images: {refs}. "
        "Keep character appearance and scene look consistent with the book. "
        "Use clear, simple composition for non-verbal child recognition."
    )


def extract_characters(text: str) -> List[str]:
    banned = {
        "The", "A", "An", "And", "But", "Then", "When", "After", "Before", "In", "On", "At", "He", "She", "They",
        "It", "We", "I", "Page", "Back", "Every", "Tuesday", "Thursday",
    }
    matches = re.findall(r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\b", text)
    counts = Counter(tok for tok in matches if tok not in banned)
    return [name for name, _count in counts.most_common()]


def extract_objects(text: str, characters: List[str]) -> List[str]:
    stop = set(STOP_WORDS)
    for c in characters:
        stop.add(c.lower())

    counts: Counter[str] = Counter()
    for word in re.findall(r"[a-z]{4,}", text.lower()):
        if word in stop:
            continue
        counts[word] += 1

    return [word for word, count in counts.most_common() if count > 1]


def extract_scenes(sentences: List[str]) -> List[str]:
    out = []
    for sentence in sentences:
        m = re.search(r"\b(in|at|on|near|inside|outside|by)\b([^.!?,;]+)", sentence, flags=re.I)
        if m:
            out.append(capitalize(f"{m.group(1)} {m.group(2).strip()}"))

    if not out:
        out = ["Main story setting"]

    return dedupe(out)


def score_fact_against_question(fact: str, question: str) -> int:
    q_tokens = tokenize(question)
    f_tokens = set(tokenize(fact))
    score = sum(2 for token in q_tokens if token in f_tokens)

    q = question.lower()
    if "who" in q and re.search(r"\b([A-Z][a-z]+|he|she|they)\b", fact):
        score += 1
    if "where" in q and re.search(r"\b(in|at|on|near|inside|outside|by)\b", fact, flags=re.I):
        score += 1
    return score


def answer_from_fact(question: str, fact: str, package: StoryPackage) -> str:
    q = question.lower()
    fact_clean = strip_page_markers(fact)

    if "feel" in q or "feeling" in q or "emotion" in q:
        feelings = extract_feelings_from_fact(fact_clean)
        if feelings:
            return capitalize(feelings[0])

    if "who" in q:
        for name in package.characters:
            if name.lower() in fact_clean.lower():
                return name

    if "where" in q:
        m = re.search(r"\b(in|at|on|near|inside|outside|by)\b([^.!?,;]+)", fact_clean, flags=re.I)
        if m:
            return capitalize(f"{m.group(1)} {m.group(2).strip()}")

    if "what" in q:
        for obj in package.objects:
            if obj.lower() in fact_clean.lower():
                return capitalize(obj)

    words = re.sub(r"[^a-zA-Z0-9\s]", "", fact_clean).split()
    return capitalize(" ".join(words[:8])) if words else "From the story"


def build_synthetic_distractor(package: StoryPackage, correct: str, current: List[str]) -> str:
    candidates = package.characters + [capitalize(x) for x in package.objects] + ["Another place", "Someone else"]
    used = {normalize(correct), *(normalize(v) for v in current)}
    for candidate in candidates:
        if normalize(candidate) not in used and len(candidate) > 2:
            return candidate
    return f"Not {correct}"


def data_url_to_bytes(data_url: str) -> bytes:
    if "," not in data_url:
        raise ValueError("invalid data URL")
    return base64.b64decode(data_url.split(",", 1)[1])


def tokenize_name(name: str) -> List[str]:
    return [tok.lower() for tok in re.findall(r"[a-zA-Z]+", name) if len(tok) > 1]


def tokenize(text: str) -> List[str]:
    return [word for word in re.findall(r"[a-z]{3,}", text.lower()) if word not in STOP_WORDS]


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def strip_page_markers(text: str) -> str:
    return re.sub(r"\[Page\s+\d+\]", " ", text, flags=re.I)


def extract_feelings_from_fact(fact: str) -> List[str]:
    lowered = strip_page_markers(fact).lower()
    out: List[str] = []

    for keyword in FEELING_KEYWORDS:
        if re.search(rf"\b{re.escape(keyword)}\b", lowered):
            out.append(keyword)

    for match in re.finditer(r"\b(?:did not|didn't)\s+feel\s+([a-z][a-z\- ]{2,30})", lowered):
        token = match.group(1).strip().split(" and ")[0].split(",")[0].strip()
        if token:
            out.append(f"not {token}")

    for match in re.finditer(r"\b(?:felt|feel|feels|feeling)\s+([a-z][a-z\- ]{2,30})", lowered):
        token = match.group(1).strip().split(" and ")[0].split(",")[0].strip()
        token = re.sub(r"[^a-z\- ]", "", token).strip()
        if token and len(token) <= 20:
            out.append(token)

    for match in re.finditer(r"\b(?:was|were|is|are)\s+([a-z][a-z\- ]{2,20})", lowered):
        token = match.group(1).strip().split(" and ")[0].split(",")[0].strip()
        if token in FEELING_KEYWORDS:
            out.append(token)

    cleaned: List[str] = []
    for entry in out:
        entry = re.sub(r"\s+", " ", entry).strip()
        if not entry:
            continue
        if entry in {"the", "very", "really"}:
            continue
        cleaned.append(entry)
    return dedupe(cleaned)[:4]


def format_name_list(values: List[str]) -> str:
    items = [v.strip() for v in values if v and v.strip()]
    if not items:
        return "No characters found"
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"


def truncate(text: str, max_len: int) -> str:
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def dedupe(values: List[str]) -> List[str]:
    seen = set()
    out = []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def capitalize(text: str) -> str:
    return text[:1].upper() + text[1:]


def ms_since(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)
