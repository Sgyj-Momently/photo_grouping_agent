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

```bash
python3 src/group_photos.py \
  --input ./examples/photo_info_input.json \
  --output ./examples/grouped_output.json \
  --enable-llm-refinement \
  --grouping-model qwen2.5:14b
```

```bash
python3 src/group_photos.py \
  --input ./examples/photo_info_input.json \
  --output ./examples/grouped_output.compare.json \
  --compare-models qwen2.5:14b gemma4:e4b gemma4:26b
```

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

## 입력 개요

입력은 사진 정보 추출 에이전트의 결과 목록을 사용한다.

```json
{
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
  "group_count": 1,
  "groups": [
    {
      "group_id": "group-001",
      "start_time": "2026-04-10T09:00:00",
      "end_time": "2026-04-10T09:20:00",
      "photo_ids": ["p1", "p2"],
      "location_hint": "도쿄역",
      "group_reason": "time_window"
    }
  ]
}
```

## 모델 추천

- 기본 기준선: `qwen2.5:14b`
- 가벼운 비교 실험: `gemma4:e4b`
- 품질 우선 비교 실험: `gemma4:26b`

## 운용 팁

- 먼저 규칙 기반 결과를 만들고, 그다음 LLM 보정은 선택적으로 켠다.
- 모델 비교는 동일 입력에 대해 `comparison_results`를 남겨 품질 차이를 확인하는 용도로 사용한다.
- 전체 순서 제어는 여전히 Spring 오케스트레이터가 맡고, 이 모듈은 그룹화 결과 생성만 담당한다.
