"""Public API shapes for the IROS 2026 Atlas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EvidenceReference(BaseModel):
    source: str
    url: str | None = None
    observed_at: str | None = None
    status: str = "available"


class ScoreBreakdown(BaseModel):
    score: float
    confidence: float
    eligible: bool
    rank: int | None = None
    topic_ranks: dict[str, int] = Field(default_factory=dict)
    components: dict[str, float] = Field(default_factory=dict)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    version: str
    computed_at: str


class PaperSummary(BaseModel):
    paper_number: str
    title: str
    authors: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    session_type: str | None = None
    day: str | None = None
    pdf_url: str | None = None
    public_page_url: str | None = None
    score: ScoreBreakdown | None = None


class PaperDetail(PaperSummary):
    abstract: str | None = None
    affiliations: list[str] = Field(default_factory=list)
    time: str | None = None
    room: str | None = None
    session_name: str | None = None
    official_record_url: str | None = None
    doi: str | None = None
    related_papers: list[dict[str, Any]] = Field(default_factory=list)


class KeywordNode(BaseModel):
    id: str
    label: str
    count: int
    topic: str


class KeywordEdge(BaseModel):
    source: str
    target: str
    count: int


class InstitutionProfile(BaseModel):
    id: str
    name: str
    paper_count: int
    score: float
    topic_breadth: int
    top_topics: list[str] = Field(default_factory=list)


class ResearcherProfile(BaseModel):
    id: int
    name: str
    paper_count: int
    score: float
    topic_breadth: int
    identity_confidence: str = "source_name_only"
