from __future__ import annotations

import os
from typing import Any
from typing import List, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

from .group_photos import (
    DEFAULT_GROUPING_MODEL,
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_OLLAMA_TIMEOUT_SECONDS,
    DEFAULT_TIME_WINDOW_MINUTES,
    GroupingStrategy,
    compare_grouping_models,
    group_photos,
    refine_groups_with_llm,
)


app = FastAPI(
    title="Photo Grouping Agent API",
    version="1.0.0",
    description="사진 정보 목록을 받아 그룹화 결과를 반환하는 에이전트 API",
)


OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL)
OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", str(DEFAULT_OLLAMA_TIMEOUT_SECONDS)))


class GpsPayload(BaseModel):
    lat: Optional[float] = None
    lon: Optional[float] = None


class PhotoPayload(BaseModel):
    photo_id: str
    file_name: str
    captured_at: Optional[str] = None
    has_gps: Optional[bool] = None
    gps: Optional[GpsPayload] = None
    location_hint: Optional[str] = None
    scene_type: Optional[str] = None
    summary: Optional[str] = None
    subjects: List[str] = Field(default_factory=list)


class GroupingRequest(BaseModel):
    project_id: str
    grouping_strategy: GroupingStrategy
    time_window_minutes: int = DEFAULT_TIME_WINDOW_MINUTES
    enable_llm_refinement: bool = False
    grouping_model: str = DEFAULT_GROUPING_MODEL
    compare_models: List[str] = Field(default_factory=list)
    photos: List[PhotoPayload]


@app.get("/health")
def health() -> dict[str, str]:
    """오케스트레이터에서 살아있는지 확인하는 단순 헬스 체크."""

    return {"status": "ok"}


@app.post("/api/v1/photo-groups")
def create_photo_groups(request: GroupingRequest) -> dict[str, Any]:
    """정해진 그룹화 전략으로 사진 그룹을 생성한다."""

    photos = [photo.model_dump() for photo in request.photos]
    base_result = group_photos(
        photos,
        grouping_strategy=request.grouping_strategy,
        time_window_minutes=request.time_window_minutes,
    )
    result: dict[str, Any] = base_result

    if request.enable_llm_refinement:
        result = refine_groups_with_llm(
            photos=photos,
            grouping_result=base_result,
            model_name=request.grouping_model,
            base_url=OLLAMA_BASE_URL,
            timeout_seconds=OLLAMA_TIMEOUT_SECONDS,
        )

    if request.compare_models:
        result["comparison_results"] = compare_grouping_models(
            photos=photos,
            grouping_result=base_result,
            model_names=request.compare_models,
            base_url=OLLAMA_BASE_URL,
            timeout_seconds=OLLAMA_TIMEOUT_SECONDS,
        )

    result["project_id"] = request.project_id
    return result
