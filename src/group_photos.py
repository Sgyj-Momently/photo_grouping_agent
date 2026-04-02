from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib import request


DEFAULT_TIME_WINDOW_MINUTES = 90
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_GROUPING_MODEL = "qwen2.5:14b"
DEFAULT_COMPARE_MODELS = ["qwen2.5:14b", "gemma4:e4b"]
DEFAULT_OLLAMA_TIMEOUT_SECONDS = 60


def group_photos(
    photos: list[dict[str, Any]],
    time_window_minutes: int = DEFAULT_TIME_WINDOW_MINUTES,
) -> dict[str, Any]:
    """사진 목록을 시간 기준으로 그룹화해 후속 에이전트가 쓰기 쉬운 구조로 변환한다."""

    sorted_photos = sorted(photos, key=_photo_sort_key)
    groups: list[dict[str, Any]] = []

    current_group: list[dict[str, Any]] = []
    for photo in sorted_photos:
        if not current_group:
            current_group = [photo]
            continue

        if _should_start_new_group(current_group[-1], photo, time_window_minutes):
            groups.append(_build_group(group_index=len(groups), photos=current_group))
            current_group = [photo]
            continue

        current_group.append(photo)

    if current_group:
        groups.append(_build_group(group_index=len(groups), photos=current_group))

    return {
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


def _should_start_new_group(
    previous_photo: dict[str, Any],
    current_photo: dict[str, Any],
    time_window_minutes: int,
) -> bool:
    """기본 버전에서는 시간 차이가 임계값보다 크면 새 그룹을 시작한다."""

    previous_time = _parse_datetime(previous_photo.get("captured_at"))
    current_time = _parse_datetime(current_photo.get("captured_at"))

    if previous_time is None or current_time is None:
        return False

    minutes_diff = (current_time - previous_time).total_seconds() / 60
    return minutes_diff > time_window_minutes


def _parse_datetime(raw_value: str | None) -> datetime | None:
    """ISO 8601 형태의 촬영 시각 문자열을 datetime으로 변환한다."""

    if not raw_value:
        return None
    return datetime.fromisoformat(raw_value)


def _build_group(group_index: int, photos: list[dict[str, Any]]) -> dict[str, Any]:
    """한 그룹의 대표 메타데이터를 계산한다."""

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
        "group_reason": "time_window",
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

    photos_json = json.dumps(photos, ensure_ascii=False, indent=2)
    grouping_json = json.dumps(grouping_result, ensure_ascii=False, indent=2)
    return f"""
당신은 여행 사진 그룹화 전문가다.
입력으로 사진 목록과 규칙 기반 1차 그룹화 결과가 주어진다.
해야 할 일은 1차 그룹화를 검토하고, 같은 이벤트는 합치고 다른 이벤트는 나누는 것이다.

판단 기준:
- 촬영 시각의 연속성
- GPS 또는 location_hint의 일관성
- scene_type과 요약의 유사성
- 여행 일정상 자연스러운 흐름

사진 목록:
{photos_json}

규칙 기반 그룹화 결과:
{grouping_json}

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


def main() -> None:
    parser = argparse.ArgumentParser(description="Group extracted photo information into event groups.")
    parser.add_argument("--input", required=True, help="Input JSON path")
    parser.add_argument("--output", required=True, help="Output JSON path")
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
    base_result = group_photos(payload["photos"], time_window_minutes=args.time_window_minutes)
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
