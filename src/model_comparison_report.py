from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def build_model_comparison_report(comparison_payloads: list[dict[str, Any]]) -> dict[str, Any]:
    """여러 모델 비교 결과 파일을 모델별 요약 리포트로 집계한다."""

    samples = []
    model_totals: dict[str, dict[str, Any]] = {}
    strategy_totals: dict[str, dict[str, Any]] = {}

    for index, payload in enumerate(comparison_payloads, start=1):
        comparison_results = payload.get("comparison_results", {})
        sample_name = payload.get("sample_name") or f"sample-{index:03d}"
        recommended_model = comparison_results.get("recommended_model")
        grouping_strategy = payload.get("grouping_strategy") or "UNKNOWN"
        samples.append(
            {
                "sample_name": sample_name,
                "recommended_model": recommended_model,
                "grouping_strategy": grouping_strategy,
                "base_group_count": payload.get("group_count", 0),
            }
        )
        strategy = _strategy_totals(strategy_totals, grouping_strategy)
        strategy["sample_count"] += 1
        if recommended_model:
            strategy["recommended_models"][recommended_model] = (
                strategy["recommended_models"].get(recommended_model, 0) + 1
            )

        if recommended_model:
            totals = _model_totals(model_totals, recommended_model)
            totals["recommendation_count"] += 1

        for item in comparison_results.get("quality_summary", []):
            model = item.get("model")
            if not model:
                continue
            totals = _model_totals(model_totals, model)
            totals["sample_count"] += 1
            totals["repair_count_total"] += item.get("repair_count", 0)
            totals["schema_repair_count_total"] += item.get("schema_repair_count", 0)
            totals["structure_repair_count_total"] += item.get("structure_repair_count", 0)
            totals["coverage_error_count_total"] += item.get("coverage_error_count", 0)
            totals["group_count_delta_abs_total"] += abs(item.get("group_count_delta_from_rules", 0))
            if item.get("coverage_ok"):
                totals["coverage_ok_count"] += 1
                strategy["coverage_ok_count"] += 1
            strategy["model_sample_count"] += 1

    model_summaries = []
    for model, totals in model_totals.items():
        sample_count = totals["sample_count"]
        model_summaries.append(
            {
                "model": model,
                "sample_count": sample_count,
                "recommendation_count": totals["recommendation_count"],
                "coverage_ok_count": totals["coverage_ok_count"],
                "repair_count_total": totals["repair_count_total"],
                "schema_repair_count_total": totals["schema_repair_count_total"],
                "structure_repair_count_total": totals["structure_repair_count_total"],
                "coverage_error_count_total": totals["coverage_error_count_total"],
                "average_abs_group_delta": (
                    round(totals["group_count_delta_abs_total"] / sample_count, 2)
                    if sample_count
                    else 0.0
                ),
            }
        )

    model_summaries.sort(
        key=lambda item: (
            -item["recommendation_count"],
            item["repair_count_total"],
            item["schema_repair_count_total"],
            item["structure_repair_count_total"],
            item["coverage_error_count_total"],
            item["average_abs_group_delta"],
            item["model"],
        )
    )
    strategy_summaries = _summarize_strategies(strategy_totals)
    confidence = _build_confidence_summary(
        sample_count=len(comparison_payloads),
        strategy_count=len(strategy_summaries),
    )

    return {
        "sample_count": len(comparison_payloads),
        "samples": samples,
        "model_summaries": model_summaries,
        "strategy_summaries": strategy_summaries,
        "confidence": confidence,
        "recommended_model": model_summaries[0]["model"] if model_summaries else None,
    }


def render_markdown_report(report: dict[str, Any]) -> str:
    """집계 리포트를 Markdown 표로 렌더링한다."""

    lines = [
        "# Photo Grouping Model Comparison Report",
        "",
        f"- sample_count: {report.get('sample_count', 0)}",
        f"- recommended_model: {report.get('recommended_model')}",
        f"- confidence_level: {report.get('confidence', {}).get('level')}",
    ]
    for warning in report.get("confidence", {}).get("warnings", []):
        lines.append(f"- warning: {warning}")

    lines.extend(
        [
            "",
            "## Strategy Coverage",
            "",
            "| strategy | samples | coverage_ok | recommended_models |",
            "| --- | ---: | ---: | --- |",
        ]
    )
    for item in report.get("strategy_summaries", []):
        recommended_models = ", ".join(
            f"{model}: {count}"
            for model, count in item.get("recommended_models", {}).items()
        )
        row = dict(item)
        row["recommended_models"] = recommended_models or "-"
        lines.append(
            "| {grouping_strategy} | {sample_count} | {coverage_ok_count}/{model_sample_count} | {recommended_models} |".format(
                **row,
            )
        )

    lines.extend(
        [
        "",
        "## Model Summary",
        "",
        "| model | recommendations | coverage_ok | repairs | schema_repairs | structure_repairs | coverage_errors | avg_abs_group_delta |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in report.get("model_summaries", []):
        lines.append(
            "| {model} | {recommendation_count} | {coverage_ok_count}/{sample_count} | "
            "{repair_count_total} | {schema_repair_count_total} | {structure_repair_count_total} | "
            "{coverage_error_count_total} | {average_abs_group_delta} |".format(**item)
        )

    lines.extend(
        [
            "",
            "## Samples",
            "",
            "| sample | strategy | recommended_model | base_group_count |",
            "| --- | --- | --- | ---: |",
        ]
    )
    for sample in report.get("samples", []):
        lines.append(
            "| {sample_name} | {grouping_strategy} | {recommended_model} | {base_group_count} |".format(**sample)
        )

    return "\n".join(lines) + "\n"


def _model_totals(model_totals: dict[str, dict[str, Any]], model: str) -> dict[str, Any]:
    if model not in model_totals:
        model_totals[model] = {
            "sample_count": 0,
            "recommendation_count": 0,
            "coverage_ok_count": 0,
            "repair_count_total": 0,
            "schema_repair_count_total": 0,
            "structure_repair_count_total": 0,
            "coverage_error_count_total": 0,
            "group_count_delta_abs_total": 0,
        }
    return model_totals[model]


def _strategy_totals(strategy_totals: dict[str, dict[str, Any]], strategy: str) -> dict[str, Any]:
    if strategy not in strategy_totals:
        strategy_totals[strategy] = {
            "sample_count": 0,
            "model_sample_count": 0,
            "coverage_ok_count": 0,
            "recommended_models": {},
        }
    return strategy_totals[strategy]


def _summarize_strategies(strategy_totals: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    summaries = []
    for strategy, totals in strategy_totals.items():
        model_sample_count = totals["model_sample_count"]
        summaries.append(
            {
                "grouping_strategy": strategy,
                "sample_count": totals["sample_count"],
                "model_sample_count": model_sample_count,
                "coverage_ok_count": totals["coverage_ok_count"],
                "coverage_ok_ratio": (
                    round(totals["coverage_ok_count"] / model_sample_count, 3)
                    if model_sample_count
                    else 0.0
                ),
                "recommended_models": dict(sorted(totals["recommended_models"].items())),
            }
        )
    return sorted(summaries, key=lambda item: item["grouping_strategy"])


def _build_confidence_summary(sample_count: int, strategy_count: int) -> dict[str, Any]:
    warnings = []
    if sample_count < 5:
        warnings.append("Need at least 5 comparison samples before treating the recommended model as stable.")
    if strategy_count < 3:
        warnings.append("Need samples from at least 3 grouping strategies to reduce strategy bias.")
    level = "high" if not warnings else "medium" if sample_count >= 3 and strategy_count >= 2 else "low"
    return {
        "level": level,
        "sample_count": sample_count,
        "strategy_count": strategy_count,
        "warnings": warnings,
    }


def _load_payload(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.setdefault("sample_name", path.stem)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate photo grouping model comparison results.")
    parser.add_argument("inputs", nargs="+", help="Comparison result JSON files")
    parser.add_argument("--output-json", help="Optional report JSON path")
    parser.add_argument("--output-md", help="Optional report Markdown path")
    args = parser.parse_args()

    payloads = [_load_payload(Path(input_path)) for input_path in args.inputs]
    report = build_model_comparison_report(payloads)

    if args.output_json:
        output_json = Path(args.output_json)
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    markdown = render_markdown_report(report)
    if args.output_md:
        output_md = Path(args.output_md)
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text(markdown, encoding="utf-8")
    else:
        print(markdown, end="")


if __name__ == "__main__":
    main()
