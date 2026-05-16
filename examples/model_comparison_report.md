# Photo Grouping Model Comparison Report

- sample_count: 2
- recommended_model: qwen2.5:14b

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
