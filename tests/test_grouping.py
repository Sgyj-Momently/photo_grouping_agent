import sys
import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from group_photos import GroupingStrategy, compare_grouping_models, group_photos, refine_groups_with_llm


class GroupPhotosTest(unittest.TestCase):
    def test_그룹화_전략_enum은_허용된_값만_가진다(self) -> None:
        self.assertEqual(GroupingStrategy.LOCATION_BASED.value, "LOCATION_BASED")
        self.assertEqual(GroupingStrategy.FOOD_TYPE_BASED.value, "FOOD_TYPE_BASED")

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

        result = group_photos(
            photos,
            grouping_strategy=GroupingStrategy.TIME_BASED,
            time_window_minutes=90,
        )

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

        result = group_photos(photos, grouping_strategy=GroupingStrategy.LOCATION_BASED)

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

        result = group_photos(photos, grouping_strategy=GroupingStrategy.TIME_BASED)

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

        result = group_photos(photos, grouping_strategy=GroupingStrategy.LOCATION_BASED)

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

        result = group_photos(photos, grouping_strategy=GroupingStrategy.LOCATION_BASED)

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

        result = group_photos(photos, grouping_strategy=GroupingStrategy.LOCATION_BASED)

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

        result = group_photos(photos, grouping_strategy=GroupingStrategy.LOCATION_BASED)
        self.assertEqual(result["group_count"], 2)
        self.assertEqual(result["groups"][0]["photo_ids"], ["p1", "p2"])
        self.assertEqual(result["groups"][1]["photo_ids"], ["p3"])

    def test_음식전략은_음식_키워드가_같으면_같은_그룹으로_묶는다(self) -> None:
        photos = [
            {
                "photo_id": "p1",
                "file_name": "IMG_0001.jpg",
                "captured_at": None,
                "summary": "ramen bowl on the table",
                "scene_type": "food",
                "location_hint": "tokyo",
            },
            {
                "photo_id": "p2",
                "file_name": "IMG_0002.jpg",
                "captured_at": None,
                "summary": "close-up of ramen with egg",
                "scene_type": "food",
                "location_hint": "osaka",
            },
            {
                "photo_id": "p3",
                "file_name": "IMG_0003.jpg",
                "captured_at": None,
                "summary": "strawberry cake dessert on a plate",
                "scene_type": "food",
                "location_hint": "osaka",
            },
        ]

        result = group_photos(photos, grouping_strategy=GroupingStrategy.FOOD_TYPE_BASED)

        self.assertEqual(result["group_count"], 2)
        self.assertEqual(result["groups"][0]["photo_ids"], ["p1", "p2"])
        self.assertEqual(result["groups"][1]["photo_ids"], ["p3"])

        self.assertEqual(result["group_count"], 2)
        self.assertEqual(result["groups"][0]["photo_ids"], ["p1", "p2"])
        self.assertEqual(result["groups"][1]["photo_ids"], ["p3"])

    def test_단서가_전혀_없으면_누락_메타데이터_fallback으로_유지한다(self) -> None:
        photos = [
            {"photo_id": "p1", "file_name": "IMG_0001.jpg", "captured_at": None},
            {"photo_id": "p2", "file_name": "IMG_0002.jpg", "captured_at": None},
        ]

        result = group_photos(photos, grouping_strategy=GroupingStrategy.SCENE_BASED)

        self.assertEqual(result["group_count"], 1)
        self.assertEqual(result["groups"][0]["group_reason"], "initial_group")

    def test_위치전략은_시간_간격이_크면_먼저_분리한다(self) -> None:
        photos = [
            {
                "photo_id": "p1",
                "file_name": "IMG_0001.jpg",
                "captured_at": "2026-04-10T09:00:00",
                "summary": "ramen bowl",
            },
            {
                "photo_id": "p2",
                "file_name": "IMG_0002.jpg",
                "captured_at": "2026-04-10T13:00:00",
                "summary": "ramen bowl",
            },
        ]

        result = group_photos(
            photos,
            grouping_strategy=GroupingStrategy.LOCATION_BASED,
            time_window_minutes=90,
        )

        self.assertEqual(result["group_count"], 2)
        self.assertEqual(result["groups"][1]["group_reason"], "time_gap")


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

    def test_llm_호출_실패는_기존_그룹을_유지한다(self) -> None:
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
            analyzer=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("ollama down")),
            model_name="gemma4:e4b",
        )

        self.assertTrue(result["grouping_llm"]["status"].startswith("error: ollama_request_failed"))
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
    def test_llm_프롬프트는_그룹_후보_중심으로_입력을_축약한다(self) -> None:
        from group_photos import _build_grouping_prompt

        long_summary = "beach sunset " * 80
        prompt = _build_grouping_prompt(
            photos=[
                {
                    "photo_id": "p1",
                    "file_name": "IMG_0001.jpg",
                    "captured_at": "2026-04-10T09:00:00",
                    "location_hint": "Busan beach",
                    "scene_type": "beach",
                    "summary": long_summary,
                    "subjects": ["person", "sea", "sand", "sun", "sky", "extra"],
                    "source_path": "/very/long/local/path/that/should/not/be/sent.jpg",
                    "raw_ocr": "private text that is irrelevant for grouping",
                }
            ],
            grouping_result={
                "grouping_strategy": "LOCATION_BASED",
                "group_count": 1,
                "groups": [
                    {
                        "group_id": "group-001",
                        "start_time": "2026-04-10T09:00:00",
                        "end_time": "2026-04-10T09:00:00",
                        "photo_ids": ["p1"],
                        "location_hint": "Busan beach",
                        "group_reason": "initial_group",
                    }
                ],
            },
        )

        self.assertIn("그룹 후보", prompt)
        self.assertIn('"photo_id": "p1"', prompt)
        self.assertIn('"summary": "beach sunset', prompt)
        self.assertIn("...", prompt)
        self.assertNotIn("source_path", prompt)
        self.assertNotIn("raw_ocr", prompt)
        self.assertNotIn("irrelevant for grouping", prompt)
        self.assertNotIn("extra", prompt)

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

    def test_cli는_bundle을_grouping_input_json으로_저장한다(self) -> None:
        import adapt_photo_info

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "bundle.json"
            output_path = root / "grouping-input.json"
            input_path.write_text(
                json.dumps(
                    {
                        "photos": [
                            {
                                "file_name": "IMG_0001.jpg",
                                "captured_at": None,
                                "has_gps": False,
                                "gps": None,
                                "photo_summary": {
                                    "summary": "케이크",
                                    "subjects": ["cake"],
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(
                sys,
                "argv",
                [
                    "adapt_photo_info.py",
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                    "--grouping-strategy",
                    "SCENE_BASED",
                ],
            ):
                adapt_photo_info.main()

            saved = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(saved["grouping_strategy"], "SCENE_BASED")
        self.assertEqual(saved["photos"][0]["summary"], "케이크")


class GroupPhotosCliTest(unittest.TestCase):
    def test_cli는_grouping_result_json을_저장한다(self) -> None:
        import group_photos as group_photos_module

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "grouping-input.json"
            output_path = root / "grouping-output.json"
            input_path.write_text(
                json.dumps(
                    {
                        "photos": [
                            {
                                "photo_id": "p1",
                                "file_name": "IMG_0001.jpg",
                                "captured_at": "2026-04-10T09:00:00",
                            },
                            {
                                "photo_id": "p2",
                                "file_name": "IMG_0002.jpg",
                                "captured_at": "2026-04-10T12:00:00",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(
                sys,
                "argv",
                [
                    "group_photos.py",
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                    "--grouping-strategy",
                    "TIME_BASED",
                    "--time-window-minutes",
                    "90",
                ],
            ):
                group_photos_module.main()

            saved = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(saved["grouping_strategy"], "TIME_BASED")
        self.assertEqual(saved["group_count"], 2)


class ApiServerTest(unittest.TestCase):
    def test_health_endpoint(self) -> None:
        from fastapi.testclient import TestClient
        from src.api_server import app

        client = TestClient(app)

        response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_photo_groups_endpoint_returns_project_and_groups(self) -> None:
        from fastapi.testclient import TestClient
        from src.api_server import app

        client = TestClient(app)

        response = client.post(
            "/api/v1/photo-groups",
            json={
                "project_id": "project-001",
                "grouping_strategy": "TIME_BASED",
                "photos": [
                    {
                        "photo_id": "p1",
                        "file_name": "IMG_0001.jpg",
                        "captured_at": "2026-04-10T09:00:00",
                    },
                    {
                        "photo_id": "p2",
                        "file_name": "IMG_0002.jpg",
                        "captured_at": "2026-04-10T12:00:00",
                    },
                ],
            },
        )

        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["project_id"], "project-001")
        self.assertEqual(body["group_count"], 2)

    def test_photo_groups_endpoint_can_call_llm_refinement_and_model_compare(self) -> None:
        from fastapi.testclient import TestClient
        import src.api_server as api_server

        client = TestClient(api_server.app)

        with patch.object(
            api_server,
            "refine_groups_with_llm",
            return_value={
                "grouping_strategy": "LOCATION_BASED",
                "group_count": 1,
                "groups": [{"group_id": "group-001", "photo_ids": ["p1"], "group_reason": "llm"}],
                "grouping_llm": {"status": "ok", "model": "qwen2.5:14b"},
            },
        ) as refine_mock, patch.object(
            api_server,
            "compare_grouping_models",
            return_value={"comparisons": [{"model": "gemma4:e4b"}]},
        ) as compare_mock:
            response = client.post(
                "/api/v1/photo-groups",
                json={
                    "project_id": "project-001",
                    "grouping_strategy": "LOCATION_BASED",
                    "enable_llm_refinement": True,
                    "grouping_model": "qwen2.5:14b",
                    "compare_models": ["gemma4:e4b"],
                    "photos": [
                        {
                            "photo_id": "p1",
                            "file_name": "IMG_0001.jpg",
                            "summary": "케이크",
                        }
                    ],
                },
            )

        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["project_id"], "project-001")
        self.assertEqual(body["grouping_llm"]["status"], "ok")
        self.assertEqual(body["comparison_results"]["comparisons"][0]["model"], "gemma4:e4b")
        refine_mock.assert_called_once()
        compare_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
