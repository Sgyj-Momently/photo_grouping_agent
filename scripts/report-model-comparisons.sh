#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
agent_root="$(cd "${script_dir}/.." && pwd)"
default_python="${agent_root}/.venv/bin/python"
if [ ! -x "${default_python}" ]; then
  default_python="python3"
fi
python_bin="${PYTHON:-${default_python}}"

output_json="${agent_root}/examples/model_comparison_report.json"
output_md="${agent_root}/examples/model_comparison_report.md"

inputs=("$@")
if [ "${#inputs[@]}" -eq 0 ]; then
  inputs=("${agent_root}"/examples/grouped_compare_qwen_vs_gemma4*.json)
fi

"${python_bin}" "${agent_root}/src/model_comparison_report.py" \
  "${inputs[@]}" \
  --output-json "${output_json}" \
  --output-md "${output_md}"

echo "Model comparison report written to:"
echo "- ${output_json}"
echo "- ${output_md}"
