from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class StyleRef(BaseModel):
    id: str
    name: str
    data_url: str = Field(alias="dataUrl")

    class Config:
        populate_by_name = True


class CharacterStyleMap(BaseModel):
    character: str
    ref_ids: List[str] = Field(default_factory=list)
    confidence: float = 0.0


class StyleProfile(BaseModel):
    notes: List[str] = Field(default_factory=list)
    dominant_palette: List[str] = Field(default_factory=list)
    texture_keywords: List[str] = Field(default_factory=list)


class StoryPackage(BaseModel):
    id: str
    title: str
    raw_text: str
    facts: List[str] = Field(default_factory=list)
    scenes: List[str] = Field(default_factory=list)
    characters: List[str] = Field(default_factory=list)
    objects: List[str] = Field(default_factory=list)
    style_refs: List[StyleRef] = Field(default_factory=list)
    style_profile: StyleProfile = Field(default_factory=StyleProfile)
    character_style_map: List[CharacterStyleMap] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class SetupIngestRequest(BaseModel):
    package_id: Optional[str] = Field(default=None, alias="packageId")
    story_title: str = Field(alias="storyTitle")
    book_text: Optional[str] = Field(default=None, alias="bookText")
    pdf_base64: Optional[str] = Field(default=None, alias="pdfBase64")
    style_refs: List[StyleRef] = Field(default_factory=list, alias="styleRefs")
    character_image_hints: Dict[str, List[str]] = Field(default_factory=dict, alias="characterImageHints")

    class Config:
        populate_by_name = True


class SetupIngestResponse(BaseModel):
    package: StoryPackage
    learned_summary: Dict[str, int] = Field(alias="learnedSummary")

    class Config:
        populate_by_name = True


class AskRequest(BaseModel):
    package_id: str = Field(alias="packageId")
    question: str
    model: Literal["nano-banana-2", "pro", "standard"] = "nano-banana-2"

    class Config:
        populate_by_name = True


class AnswerOption(BaseModel):
    text: str
    is_correct: bool = Field(alias="isCorrect")
    support_fact: str = Field(alias="supportFact")

    class Config:
        populate_by_name = True


class CardDebug(BaseModel):
    prompts: Dict[str, str]
    selected_participants: Dict[str, object] = Field(alias="selectedParticipants")
    style_refs_used: List[Dict[str, str]] = Field(alias="styleRefsUsed")
    image_model: str = Field(alias="modelUsed")
    generation_error: Optional[str] = Field(alias="generationError", default=None)
    support_fact: str = Field(alias="supportFact")

    class Config:
        populate_by_name = True


class AnswerCard(BaseModel):
    text: str
    is_correct: bool = Field(alias="isCorrect")
    image_data_url: str = Field(alias="imageDataUrl")
    card_timing: Dict[str, int] = Field(alias="cardTiming")
    debug: CardDebug

    class Config:
        populate_by_name = True


class AskResponse(BaseModel):
    cards: List[AnswerCard]
    telemetry: Dict[str, object]
    debug_bundle: Dict[str, object] = Field(alias="debugBundle")

    class Config:
        populate_by_name = True
