# Photo Grouping Agent API Spec

Spring 오케스트레이터가 이 에이전트를 호출할 때 사용할 요청/응답 규약 문서다.

## 개요

- 목적: 사진 정보 목록을 받아 그룹화 결과를 반환한다.
- 제약: `grouping_strategy`는 허용된 enum 값만 받을 수 있다.
- 권장 방식: Spring은 자유 텍스트가 아니라 enum 전략 값만 전달한다.

## 권장 엔드포인트

- `POST /api/v1/photo-groups`

## 요청 본문

```json
{
  "project_id": "trip-001",
  "grouping_strategy": "LOCATION_BASED",
  "time_window_minutes": 90,
  "enable_llm_refinement": true,
  "grouping_model": "qwen2.5:14b",
  "photos": [
    {
      "photo_id": "photo-001",
      "file_name": "IMG_0001.jpg",
      "captured_at": "2026-04-10T09:00:00",
      "has_gps": true,
      "gps": { "lat": 35.681, "lon": 139.767 },
      "location_hint": "도쿄역",
      "scene_type": "city",
      "summary": "도쿄역 앞 거리 풍경",
      "subjects": ["station", "people"]
    }
  ]
}
```

## 요청 필드

- `project_id`: 프로젝트 또는 작업 식별자
- `grouping_strategy`: enum
- `time_window_minutes`: 시간 기반 분리에 쓸 임계값
- `enable_llm_refinement`: 규칙 기반 결과 뒤에 LLM 보정을 수행할지 여부
- `grouping_model`: LLM 보정에 사용할 모델명
- `photos`: 그룹화 대상 사진 목록

## 서버 내부 설정

다음 값은 공개 요청 본문에 포함하지 않고 서버 내부 설정으로 관리한다.

- `OLLAMA_BASE_URL`
- `OLLAMA_TIMEOUT_SECONDS`

예시:

```bash
export OLLAMA_BASE_URL=http://localhost:11434
export OLLAMA_TIMEOUT_SECONDS=180
```

## grouping_strategy enum

- `TIME_BASED`
  촬영 시각 중심 그룹화
- `LOCATION_BASED`
  GPS, location_hint, scene/location 의미 중심 그룹화
- `SCENE_BASED`
  장면 종류와 의미 태그 중심 그룹화
- `FOOD_TYPE_BASED`
  음식 종류 중심 그룹화
- `STORY_FLOW_BASED`
  여행 흐름과 연속성 중심 그룹화

## 응답 본문

```json
{
  "grouping_strategy": "LOCATION_BASED",
  "group_count": 2,
  "groups": [
    {
      "group_id": "group-001",
      "start_time": "2026-04-10T09:00:00",
      "end_time": "2026-04-10T10:00:00",
      "photo_ids": ["photo-001", "photo-002"],
      "location_hint": "도쿄역",
      "group_reason": "initial_group",
      "score": 3.5,
      "score_details": {
        "strategy": "LOCATION_BASED",
        "same_scene": true,
        "same_location": true,
        "shared_tags": ["urban"],
        "conflicting_tags": false,
        "shared_summary_words": ["station"]
      }
    }
  ],
  "grouping_llm": {
    "enabled": true,
    "model": "qwen2.5:14b",
    "status": "ok"
  }
}
```

## 응답 필드

- `grouping_strategy`: 실제 적용한 전략
- `group_count`: 그룹 수
- `groups`: 그룹 목록
- `grouping_llm`: 선택적 보정 결과 메타데이터

## 오류 응답 예시

```json
{
  "error_code": "INVALID_GROUPING_STRATEGY",
  "message": "grouping_strategy must be one of the allowed enum values"
}
```

## Spring 구현 팁

- 요청 DTO에서 `grouping_strategy`는 enum으로 받는다.
- API gateway 또는 controller 레벨에서 enum 검증을 끝낸다.
- 자유 텍스트 그룹 요청은 받지 않고, enum 전략으로만 변환해서 내부 에이전트를 호출한다.
- 결과 JSON은 그대로 저장해 후속 에이전트가 전략 컨텍스트를 재사용하게 한다.
