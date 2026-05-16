# Photo Grouping Agent

사진 정보 추출 결과를 입력받아 시간, 위치, 장면 힌트를 기준으로 사진 묶음(group)을 만드는 에이전트입니다.

## 목적

- 사진 단위 정보를 이벤트 단위 그룹으로 재구성한다.
- 이후 대표 사진 선택, 개요 생성, 초안 작성 단계의 입력을 안정적으로 만든다.
- 초기 버전은 규칙 기반으로 동작하고, 이후 LLM 보정 단계를 덧붙일 수 있게 설계한다.

## 현재 범위

- 시간 차이 기준 1차 그룹화
- GPS/위치 힌트 비교를 위한 확장 지점 제공
- 그룹별 시작 시각, 종료 시각, 사진 목록 생성
- enum 기반 그룹화 전략 선택
- 전략별 의미 태그 필터, 의미 점수 가중치, boundary evaluator 분리
- 메타데이터가 부족한 사진의 파일명 숫자 순서 gap fallback
- 선택한 Ollama 텍스트 모델로 그룹 보정
- `qwen2.5`와 `gemma4` 비교 실험 결과 저장

## 권장 아키텍처

- 이 모듈은 상위 워크스페이스 안의 독립 에이전트로 유지한다.
- 전체 순서 제어는 Spring 오케스트레이터가 담당한다.
- 이 에이전트는 `입력 JSON -> 출력 JSON` 변환 책임만 가진다.

## 실행 예시

```bash
python3 src/group_photos.py --input ./examples/photo_info_input.json --output ./examples/grouped_output.json
```

## FastAPI 실행

```bash
pip install -r requirements.txt
uvicorn src.api_server:app --reload
```

실행 후 아래 주소에서 확인할 수 있다.

- Swagger UI: `http://127.0.0.1:8000/docs`
- OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`
- Health: `http://127.0.0.1:8000/health`

```bash
python3 src/group_photos.py \
  --input ./examples/photo_info_input.json \
  --output ./examples/grouped_output.json \
  --grouping-strategy LOCATION_BASED \
  --enable-llm-refinement \
  --grouping-model qwen2.5:14b
```

```bash
python3 src/group_photos.py \
  --input ./examples/photo_info_input.json \
  --output ./examples/grouped_output.compare.json \
  --compare-models qwen2.5:14b gemma4:e4b gemma4:26b
```

반복 비교 실험은 스크립트로도 실행할 수 있다.

```bash
scripts/compare-models.sh \
  ./examples/adapted_grouping_input.json \
  ./examples/grouped_compare_qwen_vs_gemma4.json \
  qwen2.5:14b gemma4:e4b
```

여러 비교 결과를 집계하려면:

```bash
scripts/report-model-comparisons.sh
```

현재 예제 입력 묶음 전체를 비교하고 리포트까지 갱신하려면:

```bash
scripts/compare-sample-suite.sh qwen2.5:14b gemma4:e4b
```

샘플 묶음은 `examples/model_comparison_samples.json`에서 관리한다.

기본 출력:

- `examples/model_comparison_report.json`
- `examples/model_comparison_report.md`

## 1번 결과 연결

`photo_exif_llm_pipeline`의 `bundle.json`을 이 에이전트 입력 형식으로 바꿀 수 있다.

```bash
python3 src/adapt_photo_info.py \
  --input ../photo_exif_llm_pipeline/output/bundles/bundle.json \
  --output ./examples/adapted_grouping_input.json
```

## 테스트

```bash
PYTHONPYCACHEPREFIX=.pycache python3 -m unittest discover -s tests
```

운영 전 표준 검증은 coverage gate까지 포함한 아래 명령을 사용한다.

```bash
scripts/verify.sh
```

수동으로 커버리지를 확인하려면:

```bash
python3 -m coverage run --source=src -m unittest discover -s tests
python3 -m coverage report -m --fail-under=85
```

## 입력 개요

입력은 사진 정보 추출 에이전트의 결과 목록을 사용한다.

```json
{
  "grouping_strategy": "LOCATION_BASED",
  "photos": [
    {
      "photo_id": "p1",
      "file_name": "IMG_0001.jpg",
      "captured_at": "2026-04-10T09:00:00",
      "has_gps": true,
      "gps": { "lat": 35.71, "lon": 139.77 },
      "location_hint": "도쿄역",
      "scene_type": "city"
    }
  ]
}
```

## 출력 개요

```json
{
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
      "score_details": {
        "strategy": "LOCATION_BASED"
      }
    }
  ]
}
```

## 그룹화 전략

- `TIME_BASED`: 촬영 시각 중심
- `LOCATION_BASED`: GPS, location_hint, 장소 의미 중심
- `SCENE_BASED`: 장면 분류와 요약 의미 중심
- `FOOD_TYPE_BASED`: 음식 종류 중심
- `STORY_FLOW_BASED`: 여행 흐름 중심

## API 명세

- 사람 친화 문서: [docs/api-spec.md](./docs/api-spec.md)
- OpenAPI 초안: [docs/openapi.yaml](./docs/openapi.yaml)
- FastAPI 런타임 문서: `/docs`

## 모델 추천

- 기본 기준선: `qwen2.5:14b`
- 가벼운 비교 실험: `gemma4:e4b`
- 품질 우선 비교 실험: `gemma4:26b`

## 운용 팁

- 먼저 규칙 기반 결과를 만들고, 그다음 LLM 보정은 선택적으로 켠다.
- 모델 비교는 동일 입력에 대해 `comparison_results`를 남겨 품질 차이를 확인하는 용도로 사용한다.
- LLM 보정 결과가 입력 `photo_id`를 누락하면 규칙 기반 그룹 조각으로 `coverage_repair`를 붙인다.
- LLM 보정 결과가 `photo_id`를 중복하거나 새로 만들면 `invalid_group_coverage`로 처리하고 규칙 기반 결과를 유지한다.
- LLM 보정 결과 group 객체에 계약 외 필드가 있으면 `schema_repair`로 허용 필드만 남긴다.
- 모델 비교 결과의 `quality_summary`는 커버리지, repair 수, 규칙 기반 그룹 수와의 차이를 요약한다.
- 모델 비교 결과의 `recommended_model`은 `quality_summary` 정렬 기준상 가장 먼저 검토할 모델을 가리킨다.
- 여러 비교 파일의 집계 리포트는 샘플별 추천 횟수, repair 수, 평균 그룹 수 차이를 합산한다.
- `compare-models.sh`는 입력 JSON의 `grouping_strategy`를 기본값으로 사용하고, 없으면 `LOCATION_BASED`를 사용한다.
- Ollama 비교 호출은 재현성을 위해 `temperature: 0`으로 실행한다.
- 전체 순서 제어는 여전히 Spring 오케스트레이터가 맡고, 이 모듈은 그룹화 결과 생성만 담당한다.
