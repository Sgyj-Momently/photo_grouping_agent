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

    def test_전략_프로필은_전략별_의미_태그를_분리한다(self) -> None:
        from strategy_profiles import strategy_profile_for

        all_tags = {"beach", "urban", "ramen", "coffee"}

        scene_profile = strategy_profile_for(GroupingStrategy.SCENE_BASED)
        food_profile = strategy_profile_for(GroupingStrategy.FOOD_TYPE_BASED)
        location_profile = strategy_profile_for(GroupingStrategy.LOCATION_BASED)
        story_profile = strategy_profile_for(GroupingStrategy.STORY_FLOW_BASED)

        self.assertEqual(scene_profile.filter_tags(all_tags), {"beach", "urban"})
        self.assertEqual(food_profile.filter_tags(all_tags), {"ramen", "coffee"})
        self.assertEqual(location_profile.filter_tags(all_tags), all_tags)
        self.assertGreater(scene_profile.semantic_score_weights.same_scene, location_profile.semantic_score_weights.same_scene)
        self.assertGreater(
            story_profile.semantic_score_weights.two_or_more_shared_summary_words,
            location_profile.semantic_score_weights.two_or_more_shared_summary_words,
        )

    def test_의미_거리_점수는_전략별_가중치를_근거에_남긴다(self) -> None:
        from boundary_evaluators import evaluate_semantic_distance

        previous_photo = {
            "photo_id": "p1",
            "scene_type": "beach",
            "location_hint": "busan beach",
            "summary": "people walking on a beach",
        }
        current_photo = {
            "photo_id": "p2",
            "scene_type": "beach",
            "location_hint": "jeju beach",
            "summary": "people sitting near the beach",
        }

        scene_decision = evaluate_semantic_distance(
            previous_photo,
            current_photo,
            GroupingStrategy.SCENE_BASED,
        )
        location_decision = evaluate_semantic_distance(
            previous_photo,
            current_photo,
            GroupingStrategy.LOCATION_BASED,
        )

        self.assertFalse(scene_decision["should_split"])
        self.assertGreater(scene_decision["score"], location_decision["score"])
        self.assertEqual(
            scene_decision["score_details"]["semantic_score_weights"]["same_scene"],
            2.5,
        )

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

    def test_메타가_없어도_파일명_순서가_크게_끊기면_분리한다(self) -> None:
        photos = [
            {"photo_id": "p1", "file_name": "IMG_0001.jpg", "captured_at": None},
            {"photo_id": "p2", "file_name": "IMG_0120.jpg", "captured_at": None},
        ]

        result = group_photos(photos, grouping_strategy=GroupingStrategy.LOCATION_BASED)

        self.assertEqual(result["group_count"], 2)
        self.assertEqual(result["groups"][0]["photo_ids"], ["p1"])
        self.assertEqual(result["groups"][1]["photo_ids"], ["p2"])
        self.assertEqual(result["groups"][1]["group_reason"], "filename_sequence_gap")
        self.assertEqual(result["groups"][1]["score_details"]["filename_sequence_gap"], 119)

    def test_긴_타임스탬프_파일명은_카메라_순번_gap으로_오해하지_않는다(self) -> None:
        photos = [
            {
                "photo_id": "p1",
                "file_name": "3472487552658267452_20221213175124591.jpg",
                "captured_at": None,
            },
            {
                "photo_id": "p2",
                "file_name": "3472487577508871484_20220930113224152.jpg",
                "captured_at": None,
            },
        ]

        result = group_photos(photos, grouping_strategy=GroupingStrategy.LOCATION_BASED)

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

    def test_llm_응답이_photo_id를_누락하면_규칙_기반_그룹으로_복구한다(self) -> None:
        base_result = {
            "group_count": 2,
            "groups": [
                {
                    "group_id": "group-001",
                    "photo_ids": ["p1"],
                    "group_reason": "rule_based",
                },
                {
                    "group_id": "group-002",
                    "photo_ids": ["p2"],
                    "location_hint": "beach",
                    "group_reason": "rule_based",
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
                  "photo_ids": ["p1"],
                  "group_reason": "llm_dropped_photo"
                }
              ]
            }
            """,
            model_name="gemma4:e4b",
        )

        self.assertEqual(result["grouping_llm"]["status"], "ok_with_coverage_repair")
        self.assertEqual(result["grouping_llm"]["coverage_repair"]["missing_photo_ids"], ["p2"])
        self.assertEqual(result["group_count"], 2)
        self.assertEqual(result["groups"][1]["photo_ids"], ["p2"])
        self.assertEqual(result["groups"][1]["group_reason"], "coverage_repair")

    def test_llm_응답이_photo_id를_중복하면_기존_그룹을_유지한다(self) -> None:
        base_result = {
            "group_count": 1,
            "groups": [
                {
                    "group_id": "group-001",
                    "photo_ids": ["p1", "p2"],
                    "group_reason": "rule_based",
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
                  "photo_ids": ["p1", "p1", "p2"],
                  "group_reason": "duplicate"
                }
              ]
            }
            """,
            model_name="gemma4:e4b",
        )

        self.assertEqual(result["grouping_llm"]["status"], "error: invalid_group_coverage")
        self.assertEqual(result["grouping_llm"]["coverage_errors"]["duplicate_photo_ids"], ["p1"])
        self.assertEqual(result["groups"][0]["group_reason"], "rule_based")

    def test_llm_응답의_빈_그룹은_제거한다(self) -> None:
        base_result = {
            "group_count": 1,
            "groups": [
                {
                    "group_id": "group-001",
                    "photo_ids": ["p1"],
                    "group_reason": "rule_based",
                }
            ],
        }

        result = refine_groups_with_llm(
            photos=[{"photo_id": "p1"}],
            grouping_result=base_result,
            analyzer=lambda *_args, **_kwargs: """
            {
              "group_count": 2,
              "groups": [
                {
                  "group_id": "group-001",
                  "photo_ids": ["p1"],
                  "group_reason": "valid"
                },
                {
                  "group_id": "empty-group",
                  "photo_ids": [],
                  "group_reason": "empty"
                }
              ]
            }
            """,
            model_name="gemma4:e4b",
        )

        self.assertEqual(result["grouping_llm"]["status"], "ok_with_structure_repair")
        self.assertEqual(result["grouping_llm"]["structure_repair"]["dropped_empty_group_ids"], ["empty-group"])
        self.assertEqual(result["group_count"], 1)
        self.assertEqual(result["groups"][0]["photo_ids"], ["p1"])

    def test_llm_그룹의_계약_외_필드는_제거한다(self) -> None:
        base_result = {
            "group_count": 1,
            "groups": [
                {
                    "group_id": "group-001",
                    "photo_ids": ["p1"],
                    "group_reason": "rule_based",
                }
            ],
        }

        result = refine_groups_with_llm(
            photos=[{"photo_id": "p1"}],
            grouping_result=base_result,
            analyzer=lambda *_args, **_kwargs: """
            {
              "group_count": 1,
              "groups": [
                {
                  "group_id": "group-001",
                  "photo_ids": ["p1"],
                  "location_hint": null,
                  "group_reason": "valid",
                  "groups": [{"photo_ids": ["p1"]}],
                  "extra_notes": "not part of the contract"
                }
              ]
            }
            """,
            model_name="gemma4:e4b",
        )

        self.assertEqual(result["grouping_llm"]["schema_repair"]["dropped_group_fields"], ["extra_notes", "groups"])
        self.assertNotIn("groups", result["groups"][0])
        self.assertNotIn("extra_notes", result["groups"][0])

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
        self.assertEqual(result["quality_summary"][0]["model"], "gemma4:e4b")
        self.assertTrue(result["quality_summary"][0]["coverage_ok"])
        self.assertEqual(result["recommended_model"], "gemma4:e4b")

    def test_모델_비교_결과에는_커버리지_복구를_포함한다(self) -> None:
        base_result = {
            "group_count": 1,
            "groups": [
                {
                    "group_id": "group-001",
                    "photo_ids": ["p1", "p2"],
                    "group_reason": "rule_based",
                }
            ],
        }

        result = compare_grouping_models(
            photos=[{"photo_id": "p1"}, {"photo_id": "p2"}],
            grouping_result=base_result,
            model_names=["gemma4:e4b"],
            analyzer=lambda *_args, **_kwargs: """
            {
              "group_count": 1,
              "groups": [
                {
                  "group_id": "group-001",
                  "photo_ids": ["p1"],
                  "group_reason": "invalid"
                }
              ]
            }
            """,
        )

        comparison = result["comparisons"][0]
        self.assertEqual(comparison["status"], "ok_with_coverage_repair")
        self.assertEqual(comparison["coverage_repair"]["missing_photo_ids"], ["p2"])
        self.assertEqual(comparison["groups"][1]["group_reason"], "coverage_repair")
        self.assertEqual(result["quality_summary"][0]["repair_count"], 1)
        self.assertEqual(result["quality_summary"][0]["schema_repair_count"], 0)
        self.assertEqual(result["quality_summary"][0]["structure_repair_count"], 0)
        self.assertEqual(result["quality_summary"][0]["coverage_error_count"], 0)


class ModelComparisonReportTest(unittest.TestCase):
    def test_여러_비교_결과를_모델별_리포트로_집계한다(self) -> None:
        from model_comparison_report import build_model_comparison_report, render_markdown_report

        report = build_model_comparison_report(
            [
                {
                    "sample_name": "sample-a",
                    "grouping_strategy": "LOCATION_BASED",
                    "group_count": 6,
                    "comparison_results": {
                        "recommended_model": "gemma4:e4b",
                        "quality_summary": [
                            {
                                "model": "gemma4:e4b",
                                "coverage_ok": True,
                                "repair_count": 0,
                                "schema_repair_count": 0,
                                "structure_repair_count": 0,
                                "coverage_error_count": 0,
                                "group_count_delta_from_rules": -2,
                            },
                            {
                                "model": "qwen2.5:14b",
                                "coverage_ok": True,
                                "repair_count": 2,
                                "schema_repair_count": 0,
                                "structure_repair_count": 0,
                                "coverage_error_count": 0,
                                "group_count_delta_from_rules": -1,
                            },
                        ],
                    },
                },
                {
                    "sample_name": "sample-b",
                    "grouping_strategy": "SCENE_BASED",
                    "group_count": 6,
                    "comparison_results": {
                        "recommended_model": "qwen2.5:14b",
                        "quality_summary": [
                            {
                                "model": "qwen2.5:14b",
                                "coverage_ok": True,
                                "repair_count": 0,
                                "schema_repair_count": 0,
                                "structure_repair_count": 0,
                                "coverage_error_count": 0,
                                "group_count_delta_from_rules": -2,
                            },
                            {
                                "model": "gemma4:e4b",
                                "coverage_ok": True,
                                "repair_count": 0,
                                "schema_repair_count": 1,
                                "structure_repair_count": 0,
                                "coverage_error_count": 0,
                                "group_count_delta_from_rules": -3,
                            },
                        ],
                    },
                },
            ]
        )

        self.assertEqual(report["sample_count"], 2)
        self.assertEqual(report["recommended_model"], "gemma4:e4b")
        self.assertEqual(report["confidence"]["level"], "low")
        self.assertIn("Need at least 5 comparison samples", report["confidence"]["warnings"][0])
        gemma_summary = next(item for item in report["model_summaries"] if item["model"] == "gemma4:e4b")
        qwen_summary = next(item for item in report["model_summaries"] if item["model"] == "qwen2.5:14b")
        scene_summary = next(item for item in report["strategy_summaries"] if item["grouping_strategy"] == "SCENE_BASED")
        self.assertEqual(gemma_summary["recommendation_count"], 1)
        self.assertEqual(gemma_summary["schema_repair_count_total"], 1)
        self.assertEqual(qwen_summary["repair_count_total"], 2)
        self.assertEqual(scene_summary["recommended_models"], {"qwen2.5:14b": 1})

        markdown = render_markdown_report(report)
        self.assertIn("- confidence_level: low", markdown)
        self.assertIn("## Strategy Coverage", markdown)
        self.assertIn("| SCENE_BASED | 1 | 2/2 | qwen2.5:14b: 1 |", markdown)
        self.assertIn("| gemma4:e4b |", markdown)
        self.assertIn("| sample-a | LOCATION_BASED | gemma4:e4b | 6 |", markdown)


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
        self.assertEqual(captured["body"]["options"]["temperature"], 0)
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

    def test_cli는_입력_payload의_grouping_strategy를_기본값으로_쓴다(self) -> None:
        import group_photos as group_photos_module

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "grouping-input.json"
            output_path = root / "grouping-output.json"
            input_path.write_text(
                json.dumps(
                    {
                        "grouping_strategy": "SCENE_BASED",
                        "photos": [
                            {
                                "photo_id": "p1",
                                "file_name": "IMG_0001.jpg",
                                "captured_at": None,
                            }
                        ],
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
                ],
            ):
                group_photos_module.main()

            saved = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(saved["grouping_strategy"], "SCENE_BASED")

    def test_cli는_모델_비교_결과를_저장한다(self) -> None:
        import group_photos as group_photos_module

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "grouping-input.json"
            output_path = root / "grouping-output.compare.json"
            input_path.write_text(
                json.dumps(
                    {
                        "photos": [
                            {
                                "photo_id": "p1",
                                "file_name": "IMG_0001.jpg",
                                "captured_at": None,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(
                group_photos_module,
                "compare_grouping_models",
                return_value={
                    "comparisons": [
                        {"model": "qwen2.5:14b", "status": "ok", "group_count": 1, "groups": []},
                        {"model": "gemma4:e4b", "status": "ok", "group_count": 1, "groups": []},
                    ]
                },
            ) as compare_mock, patch.object(
                sys,
                "argv",
                [
                    "group_photos.py",
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                    "--grouping-strategy",
                    "LOCATION_BASED",
                    "--compare-models",
                    "qwen2.5:14b",
                    "gemma4:e4b",
                    "--ollama-timeout-seconds",
                    "20",
                ],
            ):
                group_photos_module.main()

            saved = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(saved["comparison_results"]["comparisons"][0]["model"], "qwen2.5:14b")
        self.assertEqual(saved["comparison_results"]["comparisons"][1]["model"], "gemma4:e4b")
        compare_mock.assert_called_once()
        self.assertEqual(compare_mock.call_args.kwargs["model_names"], ["qwen2.5:14b", "gemma4:e4b"])
        self.assertEqual(compare_mock.call_args.kwargs["timeout_seconds"], 20)


class ApiServerTest(unittest.TestCase):
    def test_health_endpoint(self) -> None:
        from fastapi.testclient import TestClient
        from src.api_server import app

        client = TestClient(app)

        response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "service": "photo_grouping_agent"})

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
