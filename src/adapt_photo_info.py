from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def adapt_bundle_to_grouping_input(bundle: dict[str, Any]) -> dict[str, Any]:
    """사진 정보 추출 결과 bundle을 그룹화 에이전트 입력 형식으로 변환한다."""

    photos = []
    for index, photo in enumerate(bundle.get("photos", []), start=1):
        summary = photo.get("photo_summary", {})
        photos.append(
            {
                "photo_id": f"photo-{index:03d}",
                "file_name": photo.get("file_name"),
                "captured_at": photo.get("captured_at"),
                "has_gps": photo.get("has_gps"),
                "gps": photo.get("gps"),
                "location_hint": summary.get("location_hint"),
                "scene_type": summary.get("scene_type"),
                "summary": summary.get("summary"),
                "subjects": summary.get("subjects", []),
                "source_path": summary.get("file_path"),
            }
        )

    return {"photos": photos}


def main() -> None:
    parser = argparse.ArgumentParser(description="Adapt photo-info bundle.json to photo-grouping-agent input.")
    parser.add_argument("--input", required=True, help="Path to bundle.json from photo-info agent")
    parser.add_argument("--output", required=True, help="Path to grouping input JSON")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    bundle = json.loads(input_path.read_text(encoding="utf-8"))
    adapted = adapt_bundle_to_grouping_input(bundle)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(adapted, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
