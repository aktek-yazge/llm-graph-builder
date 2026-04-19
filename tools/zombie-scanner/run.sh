#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
REPORT_DIR="$REPO_ROOT/.zombie-reports/$TIMESTAMP"
VENV_DIR="$SCRIPT_DIR/.venv"

RUN_PYTHON=true
RUN_FRONTEND=true
QUICK_MODE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --python)   RUN_FRONTEND=false; shift ;;
    --frontend) RUN_PYTHON=false; shift ;;
    --quick)    QUICK_MODE=true; shift ;;
    --help|-h)
      echo "Usage: $0 [--python|--frontend|--quick]"
      echo "  --python    Only scan Python packages"
      echo "  --frontend  Only scan frontend packages"
      echo "  --quick     Quick mode: ruff + knip only (high confidence)"
      exit 0
      ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

bootstrap_venv() {
  if [[ ! -d "$VENV_DIR" ]]; then
    echo "[setup] Creating scanner venv at $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
  fi
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"

  local needed=()
  command -v ruff    >/dev/null 2>&1 || needed+=(ruff)
  command -v vulture >/dev/null 2>&1 || needed+=(vulture)
  if ! $QUICK_MODE; then
    python3 -c "import grimp" 2>/dev/null || needed+=(grimp)
  fi

  if [[ ${#needed[@]} -gt 0 ]]; then
    echo "[setup] Installing: ${needed[*]}"
    pip install --quiet "${needed[@]}"
  fi
}

bootstrap_venv

mkdir -p "$REPORT_DIR/raw"

echo "=== Zombie Code Scanner ==="
echo "Report dir: $REPORT_DIR"
echo "Quick mode: $QUICK_MODE"
echo ""

PIDS=()

if $RUN_PYTHON; then
  echo "[*] Starting Python scan..."
  bash "$SCRIPT_DIR/scan_python.sh" "$REPO_ROOT" "$REPORT_DIR" "$QUICK_MODE" &
  PIDS+=($!)
fi

if $RUN_FRONTEND; then
  echo "[*] Starting Frontend scan..."
  bash "$SCRIPT_DIR/scan_frontend.sh" "$REPO_ROOT" "$REPORT_DIR" "$QUICK_MODE" &
  PIDS+=($!)
fi

FAIL=0
for pid in "${PIDS[@]}"; do
  if ! wait "$pid"; then
    FAIL=1
    echo "[!] Scanner process $pid exited with error (continuing)"
  fi
done

echo ""
echo "[*] Aggregating results..."
python3 "$SCRIPT_DIR/aggregate.py" "$REPORT_DIR"

echo ""
echo "=== Done ==="
echo "JSON report: $REPORT_DIR/zombie-report.json"
echo "Markdown report: $REPORT_DIR/zombie-report.md"

ln -sfn "$REPORT_DIR" "$REPO_ROOT/.zombie-reports/latest"
echo "Symlink: .zombie-reports/latest -> $TIMESTAMP"

exit $FAIL
