# Photo Grouping Model Comparison Report

- sample_count: 2
- recommended_model: qwen2.5:14b
- confidence_level: low
- warning: Need at least 5 comparison samples before treating the recommended model as stable.
- warning: Need samples from at least 3 grouping strategies to reduce strategy bias.

## Strategy Coverage

| strategy | samples | coverage_ok | recommended_models |
| --- | ---: | ---: | --- |
| LOCATION_BASED | 2 | 4/4 | qwen2.5:14b: 2 |

## Model Summary

| model | recommendations | coverage_ok | repairs | schema_repairs | structure_repairs | coverage_errors | avg_abs_group_delta |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| qwen2.5:14b | 2 | 2/2 | 0 | 0 | 0 | 0 | 2.0 |
| gemma4:e4b | 0 | 2/2 | 0 | 0 | 0 | 0 | 3.0 |

## Samples

| sample | strategy | recommended_model | base_group_count |
| --- | --- | --- | ---: |
| real-bundle-v1 | LOCATION_BASED | qwen2.5:14b | 6 |
| real-bundle-v2 | LOCATION_BASED | qwen2.5:14b | 6 |
