# Photo Grouping Agent

사진 정보 추출 결과를 입력받아 시간·위치·장면 단서를 기준으로 사진을 이벤트 단위 그룹으로 묶는 에이전트입니다.

## 역할

파이프라인에서 `photo_exif_llm_pipeline` 다음, `hero_photo_agent` 앞에 위치한다. 사진 단위 메타데이터를 이벤트 단위 그룹으로 재구성해 대표 사진 선택·개요 생성·초안 작성 단계의 입력을 안정적으로 만든다. 상태 관리·재시도·산출물 저장은 `spring_orchestrator`가 담당한다.

## API

### `GET /health`

```json
{ "status": "ok", "service": "photo_grouping_agent" }
```

### `POST /api/v1/photo-groups`

**Request**

```json
{
  "project_id": "project-001",
  "grouping_strategy": "LOCATION_BASED",
  "time_window_minutes": 90,
  "enable_llm_refinement": false,
  "grouping_model": "qwen2.5:14b",
  "compare_models": [],
  "photos": [
    {
      "photo_id": "p1",
      "file_name": "IMG_0001.jpg",
      "captured_at": "2026-04-10T09:00:00",
      "has_gps": true,
      "gps": { "lat": 35.71, "lon": 139.77 },
      "location_hint": "도쿄역",
      "scene_type": "city",
      "summary": "도쿄역 앞 광장",
      "subjects": ["people", "building"]
    }
  ]
}
```

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `project_id` | string | — | 프로젝트 식별자 |
| `grouping_strategy` | enum | — | 그룹화 전략 (아래 표 참고) |
| `time_window_minutes` | int | 90 | 같은 그룹으로 허용하는 최대 시간 간격(분) |
| `enable_llm_refinement` | bool | false | Ollama 텍스트 모델로 규칙 기반 결과를 보정할지 여부 |
| `grouping_model` | string | `qwen2.5:14b` | LLM 보정에 쓸 모델 이름 |
| `compare_models` | string[] | `[]` | 나란히 비교할 모델 이름 목록 |
| `photos` | object[] | — | 사진 메타데이터 목록 |

**그룹화 전략**

| 값 | 기준 |
|----|------|
| `TIME_BASED` | 촬영 시각 간격 중심 |
| `LOCATION_BASED` | GPS·`location_hint`·장소 의미 중심 |
| `SCENE_BASED` | 장면 분류와 요약 의미 중심 |
| `FOOD_TYPE_BASED` | 음식 종류 태그 중심 |
| `STORY_FLOW_BASED` | 여행 흐름·요약 연속성 중심 |

**Response**

```json
{
  "project_id": "project-001",
  "grouping_strategy": "LOCATION_BASED",
  "group_count": 1,
  "groups": [
    {
      "group_id": "group-001",
      "start_time": "2026-04-10T09:00:00",
      "end_time": "2026-04-10T09:20:00",
      "photo_ids": ["p1", "p2"],
      "location_hint": "도쿄역",
      "group_reason": "initial_group",
      "score": 3.5,
      "score_details": { "strategy": "LOCATION_BASED" }
    }
  ]
}
```

`enable_llm_refinement: true`이면 응답에 `grouping_llm` 객체가 추가된다. `compare_models`가 지정되면 `comparison_results` 객체가 추가된다.

## 실행

### 로컬 (FastAPI)

```bash
pip install -r requirements.txt
uvicorn src.api_server:app --reload
```

- Swagger UI: `http://127.0.0.1:8000/docs`
- Health: `http://127.0.0.1:8000/health`

### CLI (단독 실행)

```bash
python3 src/group_photos.py \
  --input ./examples/photo_info_input.json \
  --output ./examples/grouped_output.json \
  --grouping-strategy LOCATION_BASED
```

LLM 보정을 켜거나 모델을 비교하려면:

```bash
python3 src/group_photos.py \
  --input ./examples/photo_info_input.json \
  --output ./examples/grouped_output.json \
  --enable-llm-refinement \
  --grouping-model qwen2.5:14b

python3 src/group_photos.py \
  --input ./examples/photo_info_input.json \
  --output ./examples/grouped_output.compare.json \
  --compare-models qwen2.5:14b gemma4:e4b
```

### 앞 단계 결과 연결 (bundle.json → 그룹화 입력)

```bash
python3 src/adapt_photo_info.py \
  --input ../photo_exif_llm_pipeline/output/bundles/bundle.json \
  --output ./examples/adapted_grouping_input.json
```

## 설정

| 환경 변수 | 기본값 | 설명 |
|-----------|--------|------|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama 서버 주소 |
| `OLLAMA_TIMEOUT_SECONDS` | `60` | Ollama 요청 타임아웃(초) |

## 테스트

```bash
# 단위 테스트
PYTHONPYCACHEPREFIX=.pycache python3 -m unittest discover -s tests

# coverage gate 포함 전체 검증 (운영 전 필수)
scripts/verify.sh
```

`verify.sh`는 `.venv/bin/python`을 기본으로 사용한다. 다른 인터프리터를 쓰려면 `PYTHON` 환경 변수를 지정한다.

```bash
PYTHON=python3 scripts/verify.sh
```

수동 커버리지 확인:

```bash
python3 -m coverage run --source=src -m unittest discover -s tests
python3 -m coverage report -m --fail-under=85
```

## 모델 비교 스크립트

| 스크립트 | 역할 |
|----------|------|
| `scripts/compare-models.sh` | 두 모델의 그룹화 결과를 나란히 비교 |
| `scripts/compare-sample-suite.sh` | `examples/model_comparison_samples.json`의 전체 샘플로 비교 후 리포트 갱신 |
| `scripts/report-model-comparisons.sh` | 기존 비교 결과 파일을 집계해 리포트만 재생성 |

```bash
scripts/compare-models.sh \
  ./examples/adapted_grouping_input.json \
  ./examples/grouped_compare.json \
  qwen2.5:14b gemma4:e4b
```

권장 모델: `qwen2.5:14b` (기준선), `gemma4:e4b` (빠른 비교), `gemma4:26b` (품질 우선).

## 구조

```
photo_grouping_agent/
├── src/
│   ├── api_server.py           # FastAPI 앱, 요청/응답 모델, 라우트
│   ├── group_photos.py         # 규칙 기반 그룹화 + LLM 보정 + CLI 진입점
│   ├── boundary_evaluators.py  # 전략별 그룹 경계 판단 로직
│   ├── strategy_profiles.py    # GroupingStrategy enum + 전략별 가중치 프로필
│   ├── adapt_photo_info.py     # bundle.json → 그룹화 입력 변환기 + CLI
│   └── model_comparison_report.py  # 비교 결과 집계 리포트 생성
├── tests/
│   └── test_grouping.py
├── scripts/
│   ├── verify.sh
│   ├── compare-models.sh
│   ├── compare-sample-suite.sh
│   └── report-model-comparisons.sh
├── examples/                   # 입력·출력 예시 및 비교 결과
├── schemas/                    # JSON Schema (입력·출력 계약)
└── docs/                       # API 명세 및 오케스트레이션 문서
```

스키마 파일: [`schemas/grouping-input.schema.json`](./schemas/grouping-input.schema.json), [`schemas/grouping-output.schema.json`](./schemas/grouping-output.schema.json)
