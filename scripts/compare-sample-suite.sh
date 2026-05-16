#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
agent_root="$(cd "${script_dir}/.." && pwd)"
default_python="${agent_root}/.venv/bin/python"
if [ ! -x "${default_python}" ]; then
  default_python="python3"
fi
python_bin="${PYTHON:-${default_python}}"
manifest_path="${MODEL_COMPARISON_MANIFEST:-${agent_root}/examples/model_comparison_samples.json}"

models=("$@")
if [ "${#models[@]}" -eq 0 ]; then
  models=("qwen2.5:14b" "gemma4:e4b")
fi

outputs=()

while IFS=$'\t' read -r sample_name input_name output_name; do
  input_path="${agent_root}/examples/${input_name}"
  output_path="${agent_root}/examples/${output_name}"
  "${agent_root}/scripts/compare-models.sh" "${input_path}" "${output_path}" "${models[@]}"
  "${python_bin}" - "${output_path}" "${sample_name}" <<'PY'
import json
import sys
from pathlib import Path

output_path = Path(sys.argv[1])
payload = json.loads(output_path.read_text(encoding="utf-8"))
payload["sample_name"] = sys.argv[2]
output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
PY
  outputs+=("${output_path}")
done < <("${python_bin}" - "${manifest_path}" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for sample in manifest.get("samples", []):
    print(f"{sample['name']}\t{sample['input']}\t{sample['output']}")
PY
)

"${agent_root}/scripts/report-model-comparisons.sh" "${outputs[@]}"
