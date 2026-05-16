from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Callable
from urllib import request

try:
    from .boundary_evaluators import evaluate_group_boundary
    from .strategy_profiles import GroupingStrategy
except ImportError:  # pragma: no cover - CLI/top-level test import compatibility
    from boundary_evaluators import evaluate_group_boundary
    from strategy_profiles import GroupingStrategy


DEFAULT_TIME_WINDOW_MINUTES = 90
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_GROUPING_MODEL = "qwen2.5:14b"
DEFAULT_COMPARE_MODELS = ["qwen2.5:14b"]
DEFAULT_OLLAMA_TIMEOUT_SECONDS = 60


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

        decision = evaluate_group_boundary(
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

    parsed, dropped_group_fields = _normalize_model_group_schema(parsed)
    if dropped_group_fields:
        result["grouping_llm"]["schema_repair"] = {
            "dropped_group_fields": dropped_group_fields,
            "strategy": "keep_allowed_group_fields",
        }

    parsed, dropped_empty_group_ids = _drop_empty_groups(parsed)
    if dropped_empty_group_ids:
        result["grouping_llm"]["structure_repair"] = {
            "dropped_empty_group_ids": dropped_empty_group_ids,
            "strategy": "drop_empty_groups",
        }

    coverage_errors = _validate_group_coverage(photos, parsed.get("groups", []))
    if _can_repair_missing_photo_ids(coverage_errors):
        parsed = _repair_missing_photo_ids(parsed, grouping_result, coverage_errors["missing_photo_ids"])
        result["grouping_llm"]["coverage_repair"] = {
            "missing_photo_ids": coverage_errors["missing_photo_ids"],
            "strategy": "append_rule_based_repair_groups",
        }
        coverage_errors = _validate_group_coverage(photos, parsed.get("groups", []))

    if coverage_errors:
        result["grouping_llm"]["status"] = "error: invalid_group_coverage"
        result["grouping_llm"]["coverage_errors"] = coverage_errors
        result["grouping_llm"]["raw_response"] = raw_response
        return result

    result["group_count"] = len(parsed.get("groups", []))
    result["groups"] = parsed.get("groups", result.get("groups", []))
    if "coverage_repair" in result["grouping_llm"]:
        result["grouping_llm"]["status"] = "ok_with_coverage_repair"
    elif "structure_repair" in result["grouping_llm"]:
        result["grouping_llm"]["status"] = "ok_with_structure_repair"
    else:
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
                "coverage_errors": refined["grouping_llm"].get("coverage_errors", {}),
                "schema_repair": refined["grouping_llm"].get("schema_repair", {}),
                "coverage_repair": refined["grouping_llm"].get("coverage_repair", {}),
                "structure_repair": refined["grouping_llm"].get("structure_repair", {}),
                "group_count": refined.get("group_count"),
                "groups": refined.get("groups", []),
            }
        )

    quality_summary = _summarize_model_comparison_quality(
        base_group_count=grouping_result.get("group_count", 0),
        comparisons=comparisons,
    )
    return {
        "comparisons": comparisons,
        "quality_summary": quality_summary,
        "recommended_model": quality_summary[0]["model"] if quality_summary else None,
    }


def _summarize_model_comparison_quality(
    base_group_count: int,
    comparisons: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """모델 비교 결과를 사람이 훑기 쉬운 품질 요약으로 변환한다."""

    summary = []
    for comparison in comparisons:
        missing_ids = comparison.get("coverage_repair", {}).get("missing_photo_ids", [])
        coverage_errors = comparison.get("coverage_errors", {})
        dropped_group_fields = comparison.get("schema_repair", {}).get("dropped_group_fields", [])
        dropped_empty_group_ids = comparison.get("structure_repair", {}).get("dropped_empty_group_ids", [])
        group_count = comparison.get("group_count") or 0
        summary.append(
            {
                "model": comparison.get("model"),
                "status": comparison.get("status"),
                "coverage_ok": not coverage_errors,
                "repair_count": len(missing_ids),
                "schema_repair_count": len(dropped_group_fields),
                "structure_repair_count": len(dropped_empty_group_ids),
                "coverage_error_count": sum(len(ids) for ids in coverage_errors.values()),
                "group_count": group_count,
                "group_count_delta_from_rules": group_count - base_group_count,
            }
        )

    return sorted(
        summary,
        key=lambda item: (
            not item["coverage_ok"],
            item["repair_count"],
            item["schema_repair_count"],
            item["structure_repair_count"],
            item["coverage_error_count"],
            abs(item["group_count_delta_from_rules"]),
            item["model"] or "",
        ),
    )


def _normalize_model_group_schema(parsed_result: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """LLM group 객체에서 출력 계약에 없는 필드를 제거한다."""

    allowed_fields = {"group_id", "start_time", "end_time", "photo_ids", "location_hint", "group_reason"}
    groups = parsed_result.get("groups", [])
    dropped_fields: set[str] = set()
    normalized_groups = []
    for group in groups:
        dropped_fields.update(set(group) - allowed_fields)
        normalized_group = {key: group.get(key) for key in allowed_fields if key in group}
        if not isinstance(normalized_group.get("photo_ids", []), list):
            normalized_group["photo_ids"] = []
        normalized_groups.append(normalized_group)

    if not dropped_fields:
        return parsed_result, []

    repaired = copy.deepcopy(parsed_result)
    repaired["groups"] = normalized_groups
    repaired["group_count"] = len(normalized_groups)
    return repaired, sorted(dropped_fields)


def _drop_empty_groups(parsed_result: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """LLM이 만든 빈 그룹을 제거하고 제거한 group_id를 반환한다."""

    groups = parsed_result.get("groups", [])
    dropped_group_ids = [
        str(group.get("group_id") or f"group-{index + 1:03d}")
        for index, group in enumerate(groups)
        if not group.get("photo_ids")
    ]
    if not dropped_group_ids:
        return parsed_result, []

    repaired = copy.deepcopy(parsed_result)
    repaired["groups"] = [group for group in groups if group.get("photo_ids")]
    repaired["group_count"] = len(repaired["groups"])
    return repaired, dropped_group_ids


def _validate_group_coverage(
    photos: list[dict[str, Any]],
    groups: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """LLM 보정 결과가 입력 photo_id를 누락/중복하지 않는지 검증한다."""

    expected_ids = [photo.get("photo_id") for photo in photos if photo.get("photo_id")]
    seen_ids: list[str] = []
    for group in groups:
        seen_ids.extend(photo_id for photo_id in group.get("photo_ids", []) if photo_id)

    expected_set = set(expected_ids)
    seen_set = set(seen_ids)
    duplicate_ids = sorted({photo_id for photo_id in seen_ids if seen_ids.count(photo_id) > 1})
    unexpected_ids = sorted(seen_set - expected_set)
    missing_ids = sorted(expected_set - seen_set)

    errors: dict[str, list[str]] = {}
    if missing_ids:
        errors["missing_photo_ids"] = missing_ids
    if duplicate_ids:
        errors["duplicate_photo_ids"] = duplicate_ids
    if unexpected_ids:
        errors["unexpected_photo_ids"] = unexpected_ids
    return errors


def _can_repair_missing_photo_ids(coverage_errors: dict[str, list[str]]) -> bool:
    """누락만 있는 LLM 결과는 규칙 기반 그룹으로 복구 가능하다."""

    return bool(coverage_errors.get("missing_photo_ids")) and not (
        coverage_errors.get("duplicate_photo_ids") or coverage_errors.get("unexpected_photo_ids")
    )


def _repair_missing_photo_ids(
    parsed_result: dict[str, Any],
    rule_based_result: dict[str, Any],
    missing_photo_ids: list[str],
) -> dict[str, Any]:
    """LLM이 누락한 photo_id를 규칙 기반 그룹 조각으로 끝에 붙인다."""

    repaired = copy.deepcopy(parsed_result)
    groups = list(repaired.get("groups", []))
    missing_set = set(missing_photo_ids)
    repair_index = 1
    for rule_group in rule_based_result.get("groups", []):
        repair_photo_ids = [photo_id for photo_id in rule_group.get("photo_ids", []) if photo_id in missing_set]
        if not repair_photo_ids:
            continue
        groups.append(
            {
                "group_id": f"coverage-repair-{repair_index:03d}",
                "start_time": rule_group.get("start_time"),
                "end_time": rule_group.get("end_time"),
                "photo_ids": repair_photo_ids,
                "location_hint": rule_group.get("location_hint"),
                "group_reason": "coverage_repair",
            }
        )
        repair_index += 1

    repaired["groups"] = groups
    repaired["group_count"] = len(groups)
    return repaired


def _photo_sort_key(photo: dict[str, Any]) -> tuple[int, str]:
    """촬영 시각이 있는 사진을 우선 정렬하고, 없으면 파일명으로 순서를 고정한다."""

    captured_at = photo.get("captured_at")
    if captured_at:
        return (0, captured_at)
    return (1, photo.get("file_name", ""))


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
        "options": {
            "temperature": 0,
        },
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

절대 규칙:
- required_photo_ids의 모든 photo_id를 결과 groups 안에 정확히 한 번씩 포함하라.
- photo_id를 누락하거나 새로 만들거나 중복 배치하지 마라.
- 같은 이벤트를 합치거나 다른 이벤트를 나누더라도 photo_id 전체 목록은 보존하라.

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
        "required_photo_ids": [photo.get("photo_id") for photo in photos if photo.get("photo_id")],
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
        default=None,
        choices=[strategy.value for strategy in GroupingStrategy],
        help="Grouping strategy enum value. Defaults to input grouping_strategy, then LOCATION_BASED.",
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
    grouping_strategy = GroupingStrategy(
        args.grouping_strategy
        or payload.get("grouping_strategy")
        or GroupingStrategy.LOCATION_BASED.value
    )
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
