from __future__ import annotations

import asyncio
import base64
import io
import logging
import re
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from random import Random
from typing import Dict, List, Tuple

from .image_adapter import canonicalize_model, generate_image
from .models import (
    AnswerCard,
    AnswerOption,
    AskResponse,
    CardDebug,
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


class CardImageGenerationError(RuntimeError):
    def __init__(self, *, card_id: str, model: str, detail: str):
        self.card_id = card_id
        self.model = model
        self.detail = detail
        super().__init__(f"{card_id} image generation failed for model {model}: {detail}")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ingest_setup(req: SetupIngestRequest, existing: StoryPackage | None = None) -> SetupIngestResponse:
    text = (req.book_text or "").strip()
    if not text and req.pdf_base64:
        text = extract_text_from_pdf_base64(req.pdf_base64)
    if not text:
        raise ValueError("book text is required (bookText or pdfBase64)")

    learned = analyze_book_text(text)
    style_refs = normalize_style_refs(req.style_refs or (existing.style_refs if existing else []))
    style_profile = build_style_profile(style_refs, text)
    character_map = build_character_style_map(
        characters=learned["characters"],
        style_refs=style_refs,
        explicit_hints=req.character_image_hints,
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
    cleaned = clean_text(text)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if s.strip()]

    facts = [truncate(s, 200) for s in sentences[:40]]
    characters = extract_characters(cleaned)[:12]
    objects = extract_objects(cleaned, characters)[:20]
    scenes = extract_scenes(sentences)[:12]

    return {
        "facts": facts,
        "characters": characters,
        "objects": objects,
        "scenes": scenes,
    }


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


def _score_ref_for_character(ref: StyleRef, character: str) -> int:
    score = 0
    target = character.strip().lower()
    tokens = tokenize_name(character)
    blob = _ref_text_blob(ref)

    if any(target == hint.lower() for hint in ref.character_hints):
        score += 8
    if target in blob:
        score += 4
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
) -> List[CharacterStyleMap]:
    ref_lookup = {r.id: r for r in style_refs}
    rows: List[CharacterStyleMap] = []

    for character in characters:
        matched_ids: List[str] = []

        if character in explicit_hints:
            for ref_id in explicit_hints[character]:
                if ref_id in ref_lookup and ref_id not in matched_ids:
                    matched_ids.append(ref_id)
            if matched_ids:
                rows.append(CharacterStyleMap(character=character, ref_ids=matched_ids[:2], confidence=0.95))
                continue

        scored = sorted(
            ((_score_ref_for_character(ref, character), ref.id) for ref in style_refs),
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

        rows.append(CharacterStyleMap(character=character, ref_ids=matched_ids[:2], confidence=confidence))

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

    t = begin("answer_option_generation", "main")
    options = generate_answer_options(package, transcript)
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
        },
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
    participants = resolve_participants(package, option.text, option.support_fact)
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


def generate_answer_options(package: StoryPackage, question: str) -> List[AnswerOption]:
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


def resolve_participants(package: StoryPackage, option_text: str, support_fact: str) -> Dict[str, object]:
    text = f"{option_text} {support_fact}".lower()
    chars = [name for name in package.characters if name.lower() in text][:3]
    objects = [obj for obj in package.objects if obj.lower() in text][:3]
    scene = next((scene for scene in package.scenes if scene.lower() in text), package.scenes[0] if package.scenes else "Main story setting")

    if not chars and package.characters:
        chars = package.characters[:1]

    return {
        "scene": scene,
        "characters": chars,
        "objects": objects,
    }


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
        row = next((m for m in package.character_style_map if m.character == ch), None)
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
    ref_name_by_id = {ref.id: ref.name for ref in package.style_refs}
    char_map_rows = []
    for character in participants["characters"]:
        row = next((m for m in package.character_style_map if m.character == character), None)
        if not row:
            continue
        refs = ", ".join(ref_name_by_id.get(ref_id, ref_id) for ref_id in row.ref_ids) if row.ref_ids else "none"
        char_map_rows.append(f"{character}->{refs}")

    scene_row = next(
        (m for m in package.scene_style_map if normalize(m.scene) == normalize(str(participants["scene"]))),
        None,
    )
    scene_map_note = "use default scene framing"
    if scene_row:
        scene_map_note = ", ".join(ref_name_by_id.get(ref_id, ref_id) for ref_id in scene_row.ref_ids) or scene_map_note

    refs = ", ".join(
        f"{ref.get('name')} (reason={ref.get('reason')}, source={ref.get('sourceType')}, page={ref.get('pageNumber')})"
        for ref in style_refs_used
    ) or "package style profile"
    mapping_note = "; ".join(char_map_rows) or "use canonical character appearance"
    palette = ", ".join(package.style_profile.dominant_palette)

    return (
        f"Create a child-friendly answer card illustration. "
        f"Book title: {package.title}. Question: {question}. Answer text: {option.text}. "
        f"Scene: {participants['scene']}. Characters: {', '.join(participants['characters']) or 'main cast'}. "
        f"Objects: {', '.join(participants['objects']) or 'storybook props'}. "
        f"Style notes: {', '.join(package.style_profile.notes)}. Palette: {palette}. "
        f"Style refs: {refs}. Character-to-ref mapping: {mapping_note}. Scene-to-ref mapping: {scene_map_note}. "
        "Use the provided reference images (image_input) to match line style, palette, character appearance, and scene composition. "
        "Keep composition simple and highly recognizable for non-verbal child selection."
    )


def extract_characters(text: str) -> List[str]:
    banned = {
        "The", "A", "An", "And", "But", "Then", "When", "After", "Before", "In", "On", "At", "He", "She", "They",
        "It", "We", "I",
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

    if "who" in q:
        for name in package.characters:
            if name.lower() in fact.lower():
                return name

    if "where" in q:
        m = re.search(r"\b(in|at|on|near|inside|outside|by)\b([^.!?,;]+)", fact, flags=re.I)
        if m:
            return capitalize(f"{m.group(1)} {m.group(2).strip()}")

    if "what" in q:
        for obj in package.objects:
            if obj.lower() in fact.lower():
                return capitalize(obj)

    words = re.sub(r"[^a-zA-Z0-9\s]", "", fact).split()
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
