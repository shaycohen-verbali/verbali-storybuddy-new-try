from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class StyleRef(BaseModel):
    id: str
    name: str
    data_url: str = Field(alias="dataUrl")
    character_hints: List[str] = Field(default_factory=list, alias="characterHints")
    scene_hints: List[str] = Field(default_factory=list, alias="sceneHints")
    source_type: str = Field(default="manual", alias="sourceType")
    page_number: Optional[int] = Field(default=None, alias="pageNumber")
    page_text_snippet: Optional[str] = Field(default=None, alias="pageTextSnippet")

    class Config:
        populate_by_name = True


class CharacterStyleMap(BaseModel):
    character: str
    description: str = ""
    ref_ids: List[str] = Field(default_factory=list)
    confidence: float = 0.0


class SceneStyleMap(BaseModel):
    scene: str
    ref_ids: List[str] = Field(default_factory=list)
    confidence: float = 0.0


class StyleProfile(BaseModel):
    notes: List[str] = Field(default_factory=list)
    dominant_palette: List[str] = Field(default_factory=list)
    texture_keywords: List[str] = Field(default_factory=list)


class CharacterProfile(BaseModel):
    name: str
    description: str = ""


class StoryPackage(BaseModel):
    id: str
    title: str
    raw_text: str
    facts: List[str] = Field(default_factory=list)
    scenes: List[str] = Field(default_factory=list)
    characters: List[str] = Field(default_factory=list)
    character_profiles: List[CharacterProfile] = Field(default_factory=list)
    objects: List[str] = Field(default_factory=list)
    style_refs: List[StyleRef] = Field(default_factory=list)
    style_profile: StyleProfile = Field(default_factory=StyleProfile)
    character_style_map: List[CharacterStyleMap] = Field(default_factory=list)
    scene_style_map: List[SceneStyleMap] = Field(default_factory=list)
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
    package_id: Optional[str] = Field(default=None, alias="packageId")
    package: Optional[StoryPackage] = None
    question: str
    model: Literal["nano-banana-2", "nano-banana", "nano-banana-pro"] = "nano-banana-2"

    @field_validator("model", mode="before")
    @classmethod
    def normalize_model_aliases(cls, value: str) -> str:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower()
        if normalized == "pro":
            return "nano-banana-pro"
        if normalized == "standard":
            return "nano-banana"
        return normalized

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
    style_refs_used: List[Dict[str, object]] = Field(alias="styleRefsUsed")
    image_model: str = Field(alias="modelUsed")
    image_provider: str = Field(alias="imageProvider")
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
