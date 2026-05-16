from __future__ import annotations

import re
from datetime import datetime
from typing import Any

FILENAME_SEQUENCE_SPLIT_GAP = 25

try:
    from .strategy_profiles import CONFLICTING_TAG_PAIRS
    from .strategy_profiles import TAG_PATTERNS
    from .strategy_profiles import GroupingStrategy
    from .strategy_profiles import strategy_profile_for
except ImportError:  # pragma: no cover - CLI/top-level test import compatibility
    from strategy_profiles import CONFLICTING_TAG_PAIRS
    from strategy_profiles import TAG_PATTERNS
    from strategy_profiles import GroupingStrategy
    from strategy_profiles import strategy_profile_for


def evaluate_group_boundary(
    previous_photo: dict[str, Any],
    current_photo: dict[str, Any],
    grouping_strategy: GroupingStrategy,
    time_window_minutes: int,
) -> dict[str, Any]:
    """전략에 맞는 boundary evaluator로 새 그룹 시작 여부와 근거를 반환한다."""

    if grouping_strategy == GroupingStrategy.TIME_BASED:
        return evaluate_time_priority_boundary(previous_photo, current_photo, time_window_minutes)

    if grouping_strategy == GroupingStrategy.FOOD_TYPE_BASED:
        return evaluate_food_type_boundary(previous_photo, current_photo)

    return evaluate_semantic_boundary(
        previous_photo,
        current_photo,
        grouping_strategy=grouping_strategy,
        time_window_minutes=time_window_minutes,
    )


def evaluate_semantic_boundary(
    previous_photo: dict[str, Any],
    current_photo: dict[str, Any],
    grouping_strategy: GroupingStrategy,
    time_window_minutes: int,
) -> dict[str, Any]:
    """시간 간격을 먼저 보고, 부족하면 의미 거리로 분리 여부를 판단한다."""

    previous_time = _parse_datetime(previous_photo.get("captured_at"))
    current_time = _parse_datetime(current_photo.get("captured_at"))

    if previous_time is not None and current_time is not None:
        minutes_diff = (current_time - previous_time).total_seconds() / 60
        if minutes_diff > time_window_minutes:
            return {
                "should_split": True,
                "reason": "time_gap",
                "score": float(minutes_diff),
                "score_details": {
                    "minutes_diff": round(minutes_diff, 2),
                    "time_window_minutes": time_window_minutes,
                },
            }

    return evaluate_semantic_distance(previous_photo, current_photo, grouping_strategy)


def evaluate_time_priority_boundary(
    previous_photo: dict[str, Any],
    current_photo: dict[str, Any],
    time_window_minutes: int,
) -> dict[str, Any]:
    """시간 중심 전략에서는 시간 단서를 가장 우선한다."""

    previous_time = _parse_datetime(previous_photo.get("captured_at"))
    current_time = _parse_datetime(current_photo.get("captured_at"))

    if previous_time is None or current_time is None:
        return {
            "should_split": False,
            "reason": "fallback_missing_metadata",
            "score": 0.0,
            "score_details": {"strategy": GroupingStrategy.TIME_BASED.value},
        }

    minutes_diff = (current_time - previous_time).total_seconds() / 60
    if minutes_diff > time_window_minutes:
        return {
            "should_split": True,
            "reason": "time_gap",
            "score": float(minutes_diff),
            "score_details": {
                "strategy": GroupingStrategy.TIME_BASED.value,
                "minutes_diff": round(minutes_diff, 2),
                "time_window_minutes": time_window_minutes,
            },
        }

    return {
        "should_split": False,
        "reason": "initial_group",
        "score": max(0.0, float(time_window_minutes - minutes_diff)),
        "score_details": {
            "strategy": GroupingStrategy.TIME_BASED.value,
            "minutes_diff": round(minutes_diff, 2),
            "time_window_minutes": time_window_minutes,
        },
    }


def evaluate_semantic_distance(
    previous_photo: dict[str, Any],
    current_photo: dict[str, Any],
    grouping_strategy: GroupingStrategy,
) -> dict[str, Any]:
    """위치/장면/요약 유사도로 서로 다른 이벤트인지 추정한다."""

    profile = strategy_profile_for(grouping_strategy)
    weights = profile.semantic_score_weights
    previous_scene = _normalize_text(previous_photo.get("scene_type"))
    current_scene = _normalize_text(current_photo.get("scene_type"))
    previous_location = _normalize_text(previous_photo.get("location_hint"))
    current_location = _normalize_text(current_photo.get("location_hint"))

    same_scene = bool(previous_scene and current_scene and previous_scene == current_scene)
    same_location = bool(
        previous_location
        and current_location
        and (
            previous_location == current_location
            or previous_location in current_location
            or current_location in previous_location
        )
    )

    previous_summary_words = _extract_keywords(previous_photo.get("summary"))
    current_summary_words = _extract_keywords(current_photo.get("summary"))
    shared_summary_words = previous_summary_words & current_summary_words
    previous_tags = derive_semantic_tags(previous_photo, grouping_strategy)
    current_tags = derive_semantic_tags(current_photo, grouping_strategy)
    shared_tags = previous_tags & current_tags
    conflicting_tags = has_conflicting_tags(previous_tags, current_tags)

    score = 0.0
    if same_scene:
        score += weights.same_scene
    if same_location:
        score += weights.same_location
    if len(shared_tags) >= 1:
        score += weights.shared_tag
    if len(shared_summary_words) >= 2:
        score += weights.two_or_more_shared_summary_words
    elif len(shared_summary_words) == 1:
        score += weights.one_shared_summary_word
    if conflicting_tags:
        score += weights.conflicting_tags

    has_any_signal = any(
        [
            previous_scene,
            current_scene,
            previous_location,
            current_location,
            previous_summary_words,
            current_summary_words,
            previous_tags,
            current_tags,
        ]
    )
    score_details = {
        "same_scene": same_scene,
        "same_location": same_location,
        "strategy": grouping_strategy.value,
        "shared_tags": sorted(shared_tags),
        "conflicting_tags": conflicting_tags,
        "shared_summary_words": sorted(shared_summary_words),
        "semantic_score_weights": weights.as_dict(),
    }

    if score > 0:
        return {
            "should_split": False,
            "reason": "initial_group",
            "score": score,
            "score_details": score_details,
        }

    if has_any_signal:
        return {
            "should_split": True,
            "reason": "semantic_split",
            "score": 0.0,
            "score_details": score_details,
        }

    filename_gap = _filename_sequence_gap(previous_photo, current_photo)
    if filename_gap is not None and filename_gap > FILENAME_SEQUENCE_SPLIT_GAP:
        return {
            "should_split": True,
            "reason": "filename_sequence_gap",
            "score": float(filename_gap),
            "score_details": {
                **score_details,
                "filename_sequence_gap": filename_gap,
                "filename_sequence_split_gap": FILENAME_SEQUENCE_SPLIT_GAP,
            },
        }

    return {
        "should_split": False,
        "reason": "fallback_missing_metadata",
        "score": 0.0,
        "score_details": {
            **score_details,
            "filename_sequence_gap": filename_gap,
            "filename_sequence_split_gap": FILENAME_SEQUENCE_SPLIT_GAP,
        },
    }


def evaluate_food_type_boundary(
    previous_photo: dict[str, Any],
    current_photo: dict[str, Any],
) -> dict[str, Any]:
    """음식 후기 전략에서는 음식 종류 태그가 가장 중요한 기준이 된다."""

    previous_tags = derive_semantic_tags(previous_photo, GroupingStrategy.FOOD_TYPE_BASED)
    current_tags = derive_semantic_tags(current_photo, GroupingStrategy.FOOD_TYPE_BASED)
    shared_food_tags = previous_tags & current_tags

    if shared_food_tags:
        return {
            "should_split": False,
            "reason": "initial_group",
            "score": 3.0,
            "score_details": {
                "strategy": GroupingStrategy.FOOD_TYPE_BASED.value,
                "shared_food_tags": sorted(shared_food_tags),
            },
        }

    has_food_signal = bool(previous_tags or current_tags)
    if has_food_signal:
        return {
            "should_split": True,
            "reason": "food_type_split",
            "score": 0.0,
            "score_details": {
                "strategy": GroupingStrategy.FOOD_TYPE_BASED.value,
                "previous_food_tags": sorted(previous_tags),
                "current_food_tags": sorted(current_tags),
            },
        }

    return evaluate_semantic_distance(
        previous_photo,
        current_photo,
        GroupingStrategy.FOOD_TYPE_BASED,
    )


def derive_semantic_tags(photo: dict[str, Any], grouping_strategy: GroupingStrategy) -> set[str]:
    """scene_type, location_hint, summary에서 그룹화용 의미 태그를 추출한다."""

    text = " ".join(
        filter(
            None,
            [
                _normalize_text(photo.get("scene_type")),
                _normalize_text(photo.get("location_hint")),
                _normalize_text(photo.get("summary")),
            ],
        )
    )

    tags = set()
    for tag, keywords in TAG_PATTERNS.items():
        if any(keyword in text for keyword in keywords):
            tags.add(tag)

    return strategy_profile_for(grouping_strategy).filter_tags(tags)


def has_conflicting_tags(previous_tags: set[str], current_tags: set[str]) -> bool:
    """서로 다른 이벤트일 가능성이 높은 의미 태그 조합인지 판정한다."""

    for left, right in CONFLICTING_TAG_PAIRS:
        if (previous_tags & left and current_tags & right) or (previous_tags & right and current_tags & left):
            return True
    return False


def _parse_datetime(raw_value: str | None) -> datetime | None:
    """ISO 8601 형태의 촬영 시각 문자열을 datetime으로 변환한다."""

    if not raw_value:
        return None
    return datetime.fromisoformat(raw_value)


def _normalize_text(raw_value: str | None) -> str:
    """비교에 필요한 텍스트를 소문자 기준으로 단순 정규화한다."""

    if not raw_value:
        return ""
    return " ".join(raw_value.lower().split())


def _extract_keywords(raw_value: str | None) -> set[str]:
    """요약문에서 그룹화 비교에 쓸 핵심 단어 집합을 뽑는다."""

    if not raw_value:
        return set()

    stopwords = {
        "the",
        "a",
        "an",
        "in",
        "on",
        "at",
        "with",
        "and",
        "of",
        "to",
        "near",
        "while",
        "under",
        "this",
        "that",
        "is",
        "are",
    }
    return {
        word
        for word in re.findall(r"[a-zA-Z]+", raw_value.lower())
        if len(word) >= 4 and word not in stopwords
    }


def _filename_sequence_gap(
    previous_photo: dict[str, Any],
    current_photo: dict[str, Any],
) -> int | None:
    """파일명 숫자 순서가 크게 끊기는지 판단하기 위한 차이를 계산한다."""

    previous_sequence = _last_number(previous_photo.get("file_name"))
    current_sequence = _last_number(current_photo.get("file_name"))
    if previous_sequence is None or current_sequence is None:
        return None
    return abs(current_sequence - previous_sequence)


def _last_number(value: str | None) -> int | None:
    """카메라 연속 촬영 파일명에서 sequence로 볼 수 있는 숫자 토큰을 반환한다."""

    if not value:
        return None
    normalized = value.upper()
    match = re.search(r"(?:^|[_-])(IMG|DSC|DSCF|PXL)[_-]?(\d{3,6})(?=\D*$)", normalized)
    if match:
        return int(match.group(2))
    return None
