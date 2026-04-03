import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from group_photos import compare_grouping_models, group_photos, refine_groups_with_llm


class GroupPhotosTest(unittest.TestCase):
    def test_시간_차이가_크면_새_그룹으로_분리한다(self) -> None:
        photos = [
            {
                "photo_id": "p1",
                "file_name": "IMG_0001.jpg",
                "captured_at": "2026-04-10T09:00:00",
            },
            {
                "photo_id": "p2",
                "file_name": "IMG_0002.jpg",
                "captured_at": "2026-04-10T09:30:00",
            },
            {
                "photo_id": "p3",
                "file_name": "IMG_0003.jpg",
                "captured_at": "2026-04-10T12:10:00",
            },
        ]

        result = group_photos(photos, time_window_minutes=90)

        self.assertEqual(result["group_count"], 2)
        self.assertEqual(result["groups"][0]["photo_ids"], ["p1", "p2"])
        self.assertEqual(result["groups"][1]["photo_ids"], ["p3"])
        self.assertEqual(result["groups"][1]["group_reason"], "time_gap")

    def test_위치힌트가_있으면_그룹_메타에_대표값으로_남긴다(self) -> None:
        photos = [
            {
                "photo_id": "p1",
                "file_name": "IMG_0001.jpg",
                "captured_at": "2026-04-10T09:00:00",
                "location_hint": "도쿄역",
            },
            {
                "photo_id": "p2",
                "file_name": "IMG_0002.jpg",
                "captured_at": "2026-04-10T09:10:00",
            },
        ]

        result = group_photos(photos)

        self.assertEqual(result["groups"][0]["location_hint"], "도쿄역")

    def test_촬영시각이_없는_사진도_하나의_그룹으로_묶을_수_있다(self) -> None:
        photos = [
            {
                "photo_id": "p1",
                "file_name": "IMG_0001.jpg",
                "captured_at": None,
            },
            {
                "photo_id": "p2",
                "file_name": "IMG_0002.jpg",
                "captured_at": None,
            },
        ]

        result = group_photos(photos)

        self.assertEqual(result["group_count"], 1)
        self.assertEqual(result["groups"][0]["photo_ids"], ["p1", "p2"])

    def test_촬영시각이_없어도_장면과_위치가_크게_다르면_분리한다(self) -> None:
        photos = [
            {
                "photo_id": "p1",
                "file_name": "IMG_0001.jpg",
                "captured_at": None,
                "location_hint": "beach",
                "scene_type": "beach",
                "summary": "people on the beach at sunset",
            },
            {
                "photo_id": "p2",
                "file_name": "IMG_0002.jpg",
                "captured_at": None,
                "location_hint": "city street",
                "scene_type": "urban",
                "summary": "night walk in a city street",
            },
        ]

        result = group_photos(photos)

        self.assertEqual(result["group_count"], 2)
        self.assertEqual(result["groups"][0]["photo_ids"], ["p1"])
        self.assertEqual(result["groups"][1]["photo_ids"], ["p2"])
        self.assertEqual(result["groups"][1]["group_reason"], "semantic_split")

    def test_시간이_없어도_의미가_유사하면_같은_그룹으로_유지한다(self) -> None:
        photos = [
            {
                "photo_id": "p1",
                "file_name": "IMG_0001.jpg",
                "captured_at": None,
                "location_hint": "beach",
                "scene_type": "beach",
                "summary": "people walking on a beach",
            },
            {
                "photo_id": "p2",
                "file_name": "IMG_0002.jpg",
                "captured_at": None,
                "location_hint": "seaside",
                "scene_type": "beach",
                "summary": "sunset near the beach with people",
            },
        ]

        result = group_photos(photos)

        self.assertEqual(result["group_count"], 1)
        self.assertEqual(result["groups"][0]["photo_ids"], ["p1", "p2"])
        self.assertEqual(result["groups"][0]["group_reason"], "initial_group")

    def test_그룹에는_형성_근거_점수가_남는다(self) -> None:
        photos = [
            {
                "photo_id": "p1",
                "file_name": "IMG_0001.jpg",
                "captured_at": None,
                "location_hint": "beach",
                "scene_type": "beach",
                "summary": "people walking on a beach",
            },
            {
                "photo_id": "p2",
                "file_name": "IMG_0002.jpg",
                "captured_at": None,
                "location_hint": "city street",
                "scene_type": "urban",
                "summary": "night walk in a city street",
            },
        ]

        result = group_photos(photos)

        self.assertIn("score", result["groups"][0])
        self.assertIn("score_details", result["groups"][0])

    def test_beach와_urban은_같은_그룹으로_묶이지_않는다(self) -> None:
        photos = [
            {
                "photo_id": "p1",
                "file_name": "IMG_0001.jpg",
                "captured_at": None,
                "location_hint": "beach or seaside location",
                "scene_type": "beach",
                "summary": "Two people standing at the edge of a sandy beach near the water.",
            },
            {
                "photo_id": "p2",
                "file_name": "IMG_0002.jpg",
                "captured_at": None,
                "location_hint": "Coastal area",
                "scene_type": "Outdoor",
                "summary": "A person standing in front of a tall palm tree against a clear blue sky.",
            },
            {
                "photo_id": "p3",
                "file_name": "IMG_0003.jpg",
                "captured_at": None,
                "location_hint": "City street",
                "scene_type": "Street",
                "summary": "An Asian woman standing on an urban street at night.",
            },
        ]

        result = group_photos(photos)

        self.assertEqual(result["group_count"], 2)
        self.assertEqual(result["groups"][0]["photo_ids"], ["p1", "p2"])
        self.assertEqual(result["groups"][1]["photo_ids"], ["p3"])


class RefineGroupsWithLlmTest(unittest.TestCase):
    def test_llm_보정이_성공하면_모델_결과를_반영한다(self) -> None:
        base_result = {
            "group_count": 1,
            "groups": [
                {
                    "group_id": "group-001",
                    "photo_ids": ["p1", "p2"],
                    "group_reason": "time_window",
                }
            ],
        }

        result = refine_groups_with_llm(
            photos=[{"photo_id": "p1"}, {"photo_id": "p2"}],
            grouping_result=base_result,
            analyzer=lambda *_args, **_kwargs: """
            {
              "group_count": 1,
              "groups": [
                {
                  "group_id": "group-001",
                  "photo_ids": ["p1", "p2"],
                  "group_reason": "llm_refined"
                }
              ]
            }
            """,
            model_name="qwen2.5:14b",
        )

        self.assertEqual(result["grouping_llm"]["status"], "ok")
        self.assertEqual(result["grouping_llm"]["model"], "qwen2.5:14b")
        self.assertEqual(result["groups"][0]["group_reason"], "llm_refined")

    def test_llm_응답이_json이_아니면_기존_그룹을_유지한다(self) -> None:
        base_result = {
            "group_count": 1,
            "groups": [
                {
                    "group_id": "group-001",
                    "photo_ids": ["p1"],
                    "group_reason": "time_window",
                }
            ],
        }

        result = refine_groups_with_llm(
            photos=[{"photo_id": "p1"}],
            grouping_result=base_result,
            analyzer=lambda *_args, **_kwargs: "일반 텍스트 응답",
            model_name="gemma4:e4b",
        )

        self.assertEqual(result["grouping_llm"]["status"], "error: invalid_model_json")
        self.assertEqual(result["groups"][0]["group_reason"], "time_window")


class CompareGroupingModelsTest(unittest.TestCase):
    def test_여러_모델의_비교_결과를_한곳에_모은다(self) -> None:
        base_result = {
            "group_count": 1,
            "groups": [
                {
                    "group_id": "group-001",
                    "photo_ids": ["p1", "p2"],
                    "group_reason": "time_window",
                }
            ],
        }

        def fake_analyzer(*_args, **kwargs):
            model_name = kwargs["model_name"]
            return f"""
            {{
              "group_count": 1,
              "groups": [
                {{
                  "group_id": "group-001",
                  "photo_ids": ["p1", "p2"],
                  "group_reason": "{model_name}"
                }}
              ]
            }}
            """

        result = compare_grouping_models(
            photos=[{"photo_id": "p1"}, {"photo_id": "p2"}],
            grouping_result=base_result,
            model_names=["qwen2.5:14b", "gemma4:e4b"],
            analyzer=fake_analyzer,
        )

        self.assertEqual(len(result["comparisons"]), 2)
        self.assertEqual(result["comparisons"][0]["model"], "qwen2.5:14b")
        self.assertEqual(result["comparisons"][1]["model"], "gemma4:e4b")


class OllamaGroupingClientTest(unittest.TestCase):
    def test_그룹화_요청은_선택한_모델명으로_전송한다(self) -> None:
        from group_photos import _call_ollama_grouping_model

        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return b'{"response": "{\\"group_count\\": 1, \\"groups\\": []}"}'

        def fake_urlopen(request, timeout):
            import json

            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            response = _call_ollama_grouping_model(
                photos=[{"photo_id": "p1", "file_name": "IMG_0001.jpg"}],
                grouping_result={"group_count": 1, "groups": []},
                model_name="gemma4:26b",
                base_url="http://localhost:11434",
                timeout_seconds=25,
            )

        self.assertEqual(response, '{"group_count": 1, "groups": []}')
        self.assertEqual(captured["url"], "http://localhost:11434/api/generate")
        self.assertEqual(captured["timeout"], 25)
        self.assertEqual(captured["body"]["model"], "gemma4:26b")
        self.assertIn("group_count", captured["body"]["prompt"])


class BundleAdapterTest(unittest.TestCase):
    def test_bundle_json을_grouping_input_형식으로_변환한다(self) -> None:
        from adapt_photo_info import adapt_bundle_to_grouping_input

        bundle = {
            "photos": [
                {
                    "file_name": "IMG_0001.jpg",
                    "captured_at": "2026-04-10T09:00:00",
                    "has_gps": True,
                    "gps": {"lat": 35.1, "lon": 129.1},
                    "photo_summary": {
                        "location_hint": "부산 해변",
                        "scene_type": "beach",
                        "summary": "해변을 걷는 장면",
                        "subjects": ["2 people"],
                    },
                }
            ]
        }

        result = adapt_bundle_to_grouping_input(bundle)

        self.assertEqual(len(result["photos"]), 1)
        self.assertEqual(result["photos"][0]["photo_id"], "photo-001")
        self.assertEqual(result["photos"][0]["file_name"], "IMG_0001.jpg")
        self.assertEqual(result["photos"][0]["location_hint"], "부산 해변")
        self.assertEqual(result["photos"][0]["scene_type"], "beach")
        self.assertEqual(result["photos"][0]["summary"], "해변을 걷는 장면")

    def test_summary가_없어도_exif_기반_필드는_유지한다(self) -> None:
        from adapt_photo_info import adapt_bundle_to_grouping_input

        bundle = {
            "photos": [
                {
                    "file_name": "IMG_0002.jpg",
                    "captured_at": None,
                    "has_gps": False,
                    "gps": None,
                    "photo_summary": {},
                }
            ]
        }

        result = adapt_bundle_to_grouping_input(bundle)

        self.assertEqual(result["photos"][0]["photo_id"], "photo-001")
        self.assertIsNone(result["photos"][0]["captured_at"])
        self.assertFalse(result["photos"][0]["has_gps"])
        self.assertIsNone(result["photos"][0]["gps"])


if __name__ == "__main__":
    unittest.main()
