# 오케스트레이션 메모

## 역할 분리

- `photo-info-agent`: 사진 단위 정보 추출
- `photo-grouping-agent`: 사진 묶음 생성
- `hero-photo-agent`: 그룹별 대표 사진 선택
- `outline-agent`: 그룹 기반 개요 생성
- `draft-agent`: 초안 작성
- `style-agent`: 문체 반영
- `review-agent`: 검수
- `Spring orchestrator`: 실행 순서와 상태 보장

## 핵심 원칙

- 에이전트는 자율적으로 다음 단계를 결정하지 않는다.
- 순서와 재시도 정책은 Spring 오케스트레이터가 코드로 보장한다.
- 각 단계는 명확한 입력 JSON과 출력 JSON을 가진다.
- 실패 시 이전 단계 산출물을 유지한 채 재실행 가능해야 한다.

## 권장 상태 흐름

1. `PHOTO_INFO_EXTRACTED`
2. `PHOTO_GROUPED`
3. `HERO_PHOTO_SELECTED`
4. `OUTLINE_CREATED`
5. `DRAFT_CREATED`
6. `STYLE_APPLIED`
7. `REVIEW_COMPLETED`

## 그룹화 에이전트 호출 조건

- `photo-info-agent` 결과가 존재해야 한다.
- 입력 JSON 스키마 검증이 통과해야 한다.
- 동일 프로젝트에 대해 이미 `PHOTO_GROUPED` 상태이면 기본적으로 스킵하거나 강제 재실행 옵션이 필요하다.
