#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
agent_root="$(cd "${script_dir}/.." && pwd)"
default_python="${agent_root}/.venv/bin/python"
if [ ! -x "${default_python}" ]; then
  default_python="python3"
fi
python_bin="${PYTHON:-${default_python}}"

input_path="${1:-${agent_root}/examples/adapted_grouping_input.json}"
output_path="${2:-${agent_root}/examples/grouped_compare_qwen_vs_gemma4.json}"
shift $(( $# > 0 ? 1 : 0 ))
shift $(( $# > 0 ? 1 : 0 ))

models=("$@")
if [ "${#models[@]}" -eq 0 ]; then
  models=("qwen2.5:14b" "gemma4:e4b")
fi

"${python_bin}" "${agent_root}/src/group_photos.py" \
  --input "${input_path}" \
  --output "${output_path}" \
  --compare-models "${models[@]}"

echo "Model comparison written to: ${output_path}"
"${python_bin}" - "${output_path}" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
comparison_results = payload.get("comparison_results", {})
recommended_model = comparison_results.get("recommended_model")
summary = comparison_results.get("quality_summary", [])
if recommended_model:
    print(f"Recommended model: {recommended_model}")
if summary:
    print("Quality summary:")
    for item in summary:
        print(
            f"- {item['model']}: {item['status']}, "
            f"repair_count={item['repair_count']}, "
            f"group_delta={item['group_count_delta_from_rules']}"
        )
PY
