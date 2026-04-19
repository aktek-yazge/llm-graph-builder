#!/usr/bin/env bash
set -uo pipefail

REPO_ROOT="$1"
REPORT_DIR="$2"
QUICK_MODE="${3:-false}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$SCRIPT_DIR/config"
PATHS_JSON="$CONFIG_DIR/paths.json"

ensure_tools() {
  if ! command -v npx >/dev/null 2>&1; then
    echo "[frontend] npx not found — skipping frontend scans"
    return 1
  fi

  for pkg_dir in $(python3 -c "
import json
data = json.load(open('$PATHS_JSON'))
for p in data['frontend_packages']:
    print(p['path'])
"); do
    local full="$REPO_ROOT/$pkg_dir"
    if [[ -d "$full" && ! -d "$full/node_modules" ]]; then
      echo "[frontend] Installing node_modules for $pkg_dir..."
      (cd "$full" && npm install --silent 2>/dev/null) || true
    fi
  done

  return 0
}

run_knip() {
  local pkg_name="$1" pkg_path="$2"
  local full_path="$REPO_ROOT/$pkg_path"
  local out_file="$REPORT_DIR/raw/${pkg_name}-knip.json"

  if [[ ! -d "$full_path" ]]; then
    echo "[knip] Skipping $pkg_name — directory not found"
    return 0
  fi

  if [[ ! -d "$full_path/node_modules" ]]; then
    echo "[knip] Skipping $pkg_name — node_modules not found"
    return 0
  fi

  echo "[knip] Scanning $pkg_name ($pkg_path)..."

  local knip_config="$CONFIG_DIR/knip-${pkg_name}.json"
  local knip_args=("--reporter" "json")

  if [[ -f "$knip_config" ]]; then
    knip_args+=("--config" "$knip_config")
  fi

  (cd "$full_path" && npx knip "${knip_args[@]}" 2>/dev/null) > "$out_file" || true

  if [[ -s "$out_file" ]]; then
    python3 -c "
import json, sys
try:
    data = json.load(open('$out_file'))
    counts = {}
    for key in ['files', 'dependencies', 'devDependencies', 'unlisted', 'exports', 'types', 'duplicates']:
        if key in data and isinstance(data[key], list):
            counts[key] = len(data[key])
    print(f'[knip] $pkg_name: {counts}')
except:
    print('[knip] $pkg_name: output exists but could not parse')
"
  else
    echo "{}" > "$out_file"
    echo "[knip] $pkg_name: no findings"
  fi
}

run_madge() {
  local pkg_name="$1" pkg_path="$2" src_path="$3"
  local full_src="$REPO_ROOT/$src_path"
  local out_circular="$REPORT_DIR/raw/${pkg_name}-madge-circular.json"
  local out_orphans="$REPORT_DIR/raw/${pkg_name}-madge-orphans.json"

  if [[ ! -d "$full_src" ]]; then
    return 0
  fi

  echo "[madge] Analyzing $pkg_name ($src_path)..."

  local ts_config="$REPO_ROOT/$pkg_path/tsconfig.json"
  local madge_args=()
  if [[ -f "$ts_config" ]]; then
    madge_args+=("--ts-config" "$ts_config")
  fi

  npx madge --circular --json "${madge_args[@]}" "$full_src" > "$out_circular" 2>/dev/null || echo "[]" > "$out_circular"

  npx madge --orphans --json "${madge_args[@]}" "$full_src" > "$out_orphans" 2>/dev/null || echo "[]" > "$out_orphans"

  local circ_count orphan_count
  circ_count=$(python3 -c "import json; print(len(json.load(open('$out_circular'))))" 2>/dev/null || echo "?")
  orphan_count=$(python3 -c "import json; print(len(json.load(open('$out_orphans'))))" 2>/dev/null || echo "?")
  echo "[madge] $pkg_name: $circ_count circular deps, $orphan_count orphan files"
}

ensure_tools || exit 0

PACKAGES=$(python3 -c "
import json
data = json.load(open('$PATHS_JSON'))
for p in data['frontend_packages']:
    print(p['name'], p['path'], p.get('src', p['path']))
")

while IFS=' ' read -r name path src; do
  run_knip "$name" "$path"
done <<< "$PACKAGES"

if ! $QUICK_MODE; then
  while IFS=' ' read -r name path src; do
    run_madge "$name" "$path" "$src"
  done <<< "$PACKAGES"
fi

echo "[frontend] All frontend scans complete."
