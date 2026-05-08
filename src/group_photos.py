from __future__ import annotations

import argparse
import copy
import json
import re
from enum import Enum
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib import request


DEFAULT_TIME_WINDOW_MINUTES = 90
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_GROUPING_MODEL = "qwen2.5:14b"
DEFAULT_COMPARE_MODELS = ["qwen2.5:14b"]
DEFAULT_OLLAMA_TIMEOUT_SECONDS = 60


class GroupingStrategy(str, Enum):
    """허용된 그룹화 전략 집합."""

    TIME_BASED = "TIME_BASED"
    LOCATION_BASED = "LOCATION_BASED"
    SCENE_BASED = "SCENE_BASED"
    FOOD_TYPE_BASED = "FOOD_TYPE_BASED"
    STORY_FLOW_BASED = "STORY_FLOW_BASED"


def group_photos(
    photos: list[dict[str, Any]],
    grouping_strategy: GroupingStrategy = GroupingStrategy.LOCATION_BASED,
    time_window_minutes: int = DEFAULT_TIME_WINDOW_MINUTES,
) -> dict[str, Any]:
    """사진 목록을 시간 기준으로 그룹화해 후속 에이전트가 쓰기 쉬운 구조로 변환한다."""

    sorted_photos = sorted(photos, key=_photo_sort_key)
    groups: list[dict[str, Any]] = []

    current_group: list[dict[str, Any]] = []
    current_group_reason = "initial_group"
    current_group_score = 0.0
    current_group_score_details: dict[str, Any] = {}
    for photo in sorted_photos:
        if not current_group:
            current_group = [photo]
            continue

        decision = _evaluate_group_boundary(
            current_group[-1],
            photo,
            grouping_strategy=grouping_strategy,
            time_window_minutes=time_window_minutes,
        )
        if decision["should_split"]:
            groups.append(
                _build_group(
                    group_index=len(groups),
                    photos=current_group,
                    group_reason=current_group_reason,
                    score=current_group_score,
                    score_details=current_group_score_details,
                )
            )
            current_group = [photo]
            current_group_reason = decision["reason"]
            current_group_score = decision["score"]
            current_group_score_details = decision["score_details"]
            continue

        current_group.append(photo)

    if current_group:
        groups.append(
            _build_group(
                group_index=len(groups),
                photos=current_group,
                group_reason=current_group_reason,
                score=current_group_score,
                score_details=current_group_score_details,
            )
        )

    return {
        "grouping_strategy": grouping_strategy.value,
        "group_count": len(groups),
        "groups": groups,
    }


def refine_groups_with_llm(
    photos: list[dict[str, Any]],
    grouping_result: dict[str, Any],
    analyzer: Callable[..., str] | None = None,
    model_name: str = DEFAULT_GROUPING_MODEL,
    base_url: str = DEFAULT_OLLAMA_BASE_URL,
    timeout_seconds: int = DEFAULT_OLLAMA_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """규칙 기반 그룹 결과를 LLM으로 한 번 더 보정한다."""

    analyzer_fn = analyzer or _call_ollama_grouping_model
    result = copy.deepcopy(grouping_result)
    result["grouping_llm"] = {
        "enabled": True,
        "model": model_name,
        "status": "pending",
    }

    try:
        raw_response = analyzer_fn(
            photos=photos,
            grouping_result=grouping_result,
            model_name=model_name,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        result["grouping_llm"]["status"] = f"error: ollama_request_failed ({exc})"
        return result

    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError:
        result["grouping_llm"]["status"] = "error: invalid_model_json"
        result["grouping_llm"]["raw_response"] = raw_response
        return result

    result["group_count"] = parsed.get("group_count", result.get("group_count", 0))
    result["groups"] = parsed.get("groups", result.get("groups", []))
    result["grouping_llm"]["status"] = "ok"
    return result


def compare_grouping_models(
    photos: list[dict[str, Any]],
    grouping_result: dict[str, Any],
    model_names: list[str],
    analyzer: Callable[..., str] | None = None,
    base_url: str = DEFAULT_OLLAMA_BASE_URL,
    timeout_seconds: int = DEFAULT_OLLAMA_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """여러 모델의 그룹 보정 결과를 나란히 저장해 비교 실험을 쉽게 만든다."""

    comparisons = []
    for model_name in model_names:
        refined = refine_groups_with_llm(
            photos=photos,
            grouping_result=grouping_result,
            analyzer=analyzer,
            model_name=model_name,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )
        comparisons.append(
            {
                "model": model_name,
                "status": refined["grouping_llm"]["status"],
                "group_count": refined.get("group_count"),
                "groups": refined.get("groups", []),
            }
        )

    return {"comparisons": comparisons}


def _photo_sort_key(photo: dict[str, Any]) -> tuple[int, str]:
    """촬영 시각이 있는 사진을 우선 정렬하고, 없으면 파일명으로 순서를 고정한다."""

    captured_at = photo.get("captured_at")
    if captured_at:
        return (0, captured_at)
    return (1, photo.get("file_name", ""))


def _evaluate_group_boundary(
    previous_photo: dict[str, Any],
    current_photo: dict[str, Any],
    grouping_strategy: GroupingStrategy,
    time_window_minutes: int,
) -> dict[str, Any]:
    """시간 차이와 의미 차이를 함께 보고 새 그룹 시작 여부와 근거를 반환한다."""

    if grouping_strategy == GroupingStrategy.TIME_BASED:
        return _evaluate_time_priority_boundary(previous_photo, current_photo, time_window_minutes)

    if grouping_strategy == GroupingStrategy.FOOD_TYPE_BASED:
        return _evaluate_food_type_boundary(previous_photo, current_photo)

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

    # 촬영 시각이 없거나 부족할 때는 장면/위치/요약 유사도로만 분리 여부를 본다.
    return _evaluate_semantic_distance(previous_photo, current_photo, grouping_strategy)


def _evaluate_time_priority_boundary(
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


def _parse_datetime(raw_value: str | None) -> datetime | None:
    """ISO 8601 형태의 촬영 시각 문자열을 datetime으로 변환한다."""

    if not raw_value:
        return None
    return datetime.fromisoformat(raw_value)


def _build_group(
    group_index: int,
    photos: list[dict[str, Any]],
    group_reason: str,
    score: float,
    score_details: dict[str, Any],
) -> dict[str, Any]:
    """한 그룹의 대표 메타데이터와 형성 근거를 계산한다."""

    start_time = next((photo.get("captured_at") for photo in photos if photo.get("captured_at")), None)
    end_time = next(
        (photo.get("captured_at") for photo in reversed(photos) if photo.get("captured_at")),
        None,
    )
    location_hint = next(
        (photo.get("location_hint") for photo in photos if photo.get("location_hint")),
        None,
    )

    return {
        "group_id": f"group-{group_index + 1:03d}",
        "start_time": start_time,
        "end_time": end_time,
        "photo_ids": [photo["photo_id"] for photo in photos],
        "location_hint": location_hint,
        "group_reason": group_reason,
        "score": score,
        "score_details": score_details,
    }


def _evaluate_semantic_distance(
    previous_photo: dict[str, Any],
    current_photo: dict[str, Any],
    grouping_strategy: GroupingStrategy,
) -> dict[str, Any]:
    """시간 정보가 약할 때는 위치/장면/요약 유사도로 서로 다른 이벤트인지 추정한다."""

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
    previous_tags = _derive_semantic_tags(previous_photo, grouping_strategy)
    current_tags = _derive_semantic_tags(current_photo, grouping_strategy)
    shared_tags = previous_tags & current_tags
    conflicting_tags = _has_conflicting_tags(previous_tags, current_tags)

    score = 0.0
    if same_scene:
        score += 2.0
    if same_location:
        score += 2.0
    if len(shared_tags) >= 1:
        score += 1.5
    if len(shared_summary_words) >= 2:
        score += 1.5
    elif len(shared_summary_words) == 1:
        score += 0.5
    if conflicting_tags:
        score -= 2.5

    # 핵심 단서가 모두 다르면 다른 이벤트일 가능성이 크다고 보고 분리한다.
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

    if score > 0:
        return {
            "should_split": False,
            "reason": "initial_group",
            "score": score,
            "score_details": {
                "same_scene": same_scene,
                "same_location": same_location,
                "strategy": grouping_strategy.value,
                "shared_tags": sorted(shared_tags),
                "conflicting_tags": conflicting_tags,
                "shared_summary_words": sorted(shared_summary_words),
            },
        }

    if has_any_signal:
        return {
            "should_split": True,
            "reason": "semantic_split",
            "score": 0.0,
            "score_details": {
                "same_scene": same_scene,
                "same_location": same_location,
                "strategy": grouping_strategy.value,
                "shared_tags": sorted(shared_tags),
                "conflicting_tags": conflicting_tags,
                "shared_summary_words": sorted(shared_summary_words),
            },
        }

    return {
        "should_split": False,
        "reason": "fallback_missing_metadata",
        "score": 0.0,
        "score_details": {
            "same_scene": same_scene,
            "same_location": same_location,
            "strategy": grouping_strategy.value,
            "shared_tags": sorted(shared_tags),
            "conflicting_tags": conflicting_tags,
            "shared_summary_words": sorted(shared_summary_words),
        },
    }


def _evaluate_food_type_boundary(
    previous_photo: dict[str, Any],
    current_photo: dict[str, Any],
) -> dict[str, Any]:
    """음식 후기 전략에서는 음식 종류 태그가 가장 중요한 기준이 된다."""

    previous_tags = _derive_semantic_tags(previous_photo, GroupingStrategy.FOOD_TYPE_BASED)
    current_tags = _derive_semantic_tags(current_photo, GroupingStrategy.FOOD_TYPE_BASED)
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

    return _evaluate_semantic_distance(
        previous_photo,
        current_photo,
        GroupingStrategy.FOOD_TYPE_BASED,
    )


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
    words = {
        word
        for word in re.findall(r"[a-zA-Z]+", raw_value.lower())
        if len(word) >= 4 and word not in stopwords
    }
    return words


def _derive_semantic_tags(photo: dict[str, Any], grouping_strategy: GroupingStrategy) -> set[str]:
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

    tag_patterns = {
        "beach": ["beach", "seaside", "coastal", "sand", "ocean", "sea", "sunset"],
        "urban": ["urban", "city", "street", "building", "night", "cityscape"],
        "portrait": ["person", "woman", "man", "people", "adult", "female"],
        "nature": ["sky", "cloud", "sunset", "water", "outdoor"],
        "ramen": ["ramen", "noodle", "broth"],
        "dessert": ["dessert", "cake", "cookie", "icecream", "ice", "sweet"],
        "coffee": ["coffee", "latte", "espresso", "cafe"],
        "meat": ["steak", "bbq", "barbecue", "grill", "meat"],
    }

    tags = set()
    for tag, keywords in tag_patterns.items():
        if any(keyword in text for keyword in keywords):
            tags.add(tag)

    if grouping_strategy == GroupingStrategy.FOOD_TYPE_BASED:
        return {
            tag
            for tag in tags
            if tag in {"ramen", "dessert", "coffee", "meat"}
        }

    if grouping_strategy == GroupingStrategy.SCENE_BASED:
        return {
            tag
            for tag in tags
            if tag in {"beach", "urban", "portrait", "nature"}
        }

    return tags


def _has_conflicting_tags(previous_tags: set[str], current_tags: set[str]) -> bool:
    """서로 다른 이벤트일 가능성이 높은 의미 태그 조합인지 판정한다."""

    conflicting_pairs = [
        ({"beach"}, {"urban"}),
        ({"nature"}, {"urban"}),
    ]
    for left, right in conflicting_pairs:
        if (previous_tags & left and current_tags & right) or (previous_tags & right and current_tags & left):
            return True
    return False


def _call_ollama_grouping_model(
    photos: list[dict[str, Any]],
    grouping_result: dict[str, Any],
    model_name: str,
    base_url: str,
    timeout_seconds: int,
) -> str:
    """Ollama 텍스트 모델에 그룹 후보와 사진 정보를 보내 보정 결과를 받는다."""

    prompt = _build_grouping_prompt(photos=photos, grouping_result=grouping_result)
    body = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "format": "json",
    }

    http_request = request.Request(
        url=f"{base_url.rstrip('/')}/api/generate",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with request.urlopen(http_request, timeout=timeout_seconds) as response:
        response_payload = json.loads(response.read().decode("utf-8"))
    return response_payload["response"]


def _build_grouping_prompt(photos: list[dict[str, Any]], grouping_result: dict[str, Any]) -> str:
    """LLM이 규칙 기반 그룹을 merge/split 보정하도록 입력 프롬프트를 만든다."""

    context = _build_grouping_llm_context(photos=photos, grouping_result=grouping_result)
    context_json = json.dumps(context, ensure_ascii=False, indent=2)
    return f"""
당신은 여행 사진 그룹화 전문가다.
입력으로 규칙 기반 1차 그룹 후보와 후보 안의 사진 요약이 주어진다.
해야 할 일은 1차 그룹화를 검토하고, 같은 이벤트는 합치고 다른 이벤트는 나누는 것이다.

판단 기준:
- 촬영 시각의 연속성
- GPS 또는 location_hint의 일관성
- scene_type과 요약의 유사성
- 여행 일정상 자연스러운 흐름

그룹 후보:
{context_json}

반드시 아래 JSON 객체만 반환하라. 설명, 코드블록, 마크다운 없이 JSON만 출력하라.
키는 정확히 다음만 사용하라.
- group_count: integer
- groups: array

각 group 객체 키:
- group_id: string
- start_time: string | null
- end_time: string | null
- photo_ids: string[]
- location_hint: string | null
- group_reason: string
""".strip()


def _build_grouping_llm_context(photos: list[dict[str, Any]], grouping_result: dict[str, Any]) -> dict[str, Any]:
    """LLM 보정에 필요한 그룹 후보 중심의 축약 입력을 만든다."""

    photo_by_id = {photo.get("photo_id"): photo for photo in photos}
    groups = []
    for group in grouping_result.get("groups", []):
        photo_ids = group.get("photo_ids", [])
        groups.append(
            {
                "group_id": group.get("group_id"),
                "start_time": group.get("start_time"),
                "end_time": group.get("end_time"),
                "photo_ids": photo_ids,
                "location_hint": group.get("location_hint"),
                "group_reason": group.get("group_reason"),
                "score": group.get("score"),
                "score_details": group.get("score_details", {}),
                "photos": [
                    _compact_photo_for_llm(photo_by_id[photo_id])
                    for photo_id in photo_ids
                    if photo_id in photo_by_id
                ],
            }
        )

    return {
        "grouping_strategy": grouping_result.get("grouping_strategy"),
        "group_count": grouping_result.get("group_count", len(groups)),
        "groups": groups,
    }


def _compact_photo_for_llm(photo: dict[str, Any]) -> dict[str, Any]:
    """사진 원본 계약에서 그룹 보정에 직접 필요한 필드만 남긴다."""

    return {
        "photo_id": photo.get("photo_id"),
        "captured_at": photo.get("captured_at"),
        "location_hint": photo.get("location_hint"),
        "scene_type": photo.get("scene_type"),
        "summary": _truncate_text(photo.get("summary"), 240),
        "subjects": list(photo.get("subjects", []))[:5],
    }


def _truncate_text(value: str | None, limit: int) -> str | None:
    """LLM 입력 폭주를 막기 위해 긴 요약을 고정 길이로 자른다."""

    if value is None or len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def main() -> None:
    parser = argparse.ArgumentParser(description="Group extracted photo information into event groups.")
    parser.add_argument("--input", required=True, help="Input JSON path")
    parser.add_argument("--output", required=True, help="Output JSON path")
    parser.add_argument(
        "--grouping-strategy",
        default=GroupingStrategy.LOCATION_BASED.value,
        choices=[strategy.value for strategy in GroupingStrategy],
        help="Grouping strategy enum value",
    )
    parser.add_argument(
        "--time-window-minutes",
        type=int,
        default=DEFAULT_TIME_WINDOW_MINUTES,
        help="Maximum time gap allowed inside one group",
    )
    parser.add_argument(
        "--enable-llm-refinement",
        action="store_true",
        help="Refine rule-based grouping with an Ollama text model",
    )
    parser.add_argument(
        "--grouping-model",
        default=DEFAULT_GROUPING_MODEL,
        help="Model name used for grouping refinement",
    )
    parser.add_argument(
        "--compare-models",
        nargs="*",
        default=[],
        help="Optional list of model names to compare side by side",
    )
    parser.add_argument(
        "--ollama-base-url",
        default=DEFAULT_OLLAMA_BASE_URL,
        help="Ollama server base URL",
    )
    parser.add_argument(
        "--ollama-timeout-seconds",
        type=int,
        default=DEFAULT_OLLAMA_TIMEOUT_SECONDS,
        help="Ollama request timeout in seconds",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    grouping_strategy = GroupingStrategy(args.grouping_strategy)
    base_result = group_photos(
        payload["photos"],
        grouping_strategy=grouping_strategy,
        time_window_minutes=args.time_window_minutes,
    )
    result = base_result

    if args.enable_llm_refinement:
        result = refine_groups_with_llm(
            photos=payload["photos"],
            grouping_result=base_result,
            model_name=args.grouping_model,
            base_url=args.ollama_base_url,
            timeout_seconds=args.ollama_timeout_seconds,
        )

    if args.compare_models:
        result["comparison_results"] = compare_grouping_models(
            photos=payload["photos"],
            grouping_result=base_result,
            model_names=args.compare_models,
            base_url=args.ollama_base_url,
            timeout_seconds=args.ollama_timeout_seconds,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
