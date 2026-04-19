#!/usr/bin/env bash
set -uo pipefail

REPO_ROOT="$1"
REPORT_DIR="$2"
QUICK_MODE="${3:-false}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$SCRIPT_DIR/config"
VENV_DIR="$SCRIPT_DIR/.venv"

PATHS_JSON="$CONFIG_DIR/paths.json"
WHITELIST="$CONFIG_DIR/vulture-whitelist.py"

if [[ -d "$VENV_DIR" ]]; then
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
fi

run_ruff() {
  local pkg_name="$1" pkg_path="$2"
  local full_path="$REPO_ROOT/$pkg_path"
  local out_file="$REPORT_DIR/raw/${pkg_name}-ruff.json"

  if [[ ! -d "$full_path" ]]; then
    echo "[ruff] Skipping $pkg_name — directory not found: $full_path"
    return 0
  fi

  echo "[ruff] Scanning $pkg_name ($pkg_path)..."
  ruff check \
    --select F401,F811,F841 \
    --output-format json \
    --quiet \
    --extend-exclude ".venv,venv,node_modules" \
    "$full_path" > "$out_file" 2>/dev/null || true

  local count
  count=$(python3 -c "import json,sys; d=json.load(open('$out_file')); print(len(d))" 2>/dev/null || echo "?")
  echo "[ruff] $pkg_name: $count findings"
}

run_vulture() {
  local pkg_name="$1" pkg_path="$2"
  local full_path="$REPO_ROOT/$pkg_path"
  local out_file="$REPORT_DIR/raw/${pkg_name}-vulture.json"

  if [[ ! -d "$full_path" ]]; then
    return 0
  fi

  echo "[vulture] Scanning $pkg_name ($pkg_path)..."

  local whitelist_arg=""
  if [[ -f "$WHITELIST" ]]; then
    whitelist_arg="$WHITELIST"
  fi

  local raw_output
  raw_output=$(find "$full_path" -name "*.py" \
    -not -path "*/.venv/*" \
    -not -path "*/venv/*" \
    -not -path "*/node_modules/*" \
    -not -path "*/__pycache__/*" \
    -not -path "*/.git/*" \
    -not -path "*/.eggs/*" \
    -not -path "*/dist/*" \
    -not -path "*/build/*" \
    2>/dev/null | sort | xargs vulture --min-confidence 60 $whitelist_arg 2>/dev/null || true)

  python3 -c "
import json, re, sys

results = []
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    m = re.match(r'^(.+?):(\d+): (.+?) [\x27\x22](.+?)[\x27\x22] \((\d+)% confidence\)', line)
    if m:
        results.append({
            'file': m.group(1),
            'line': int(m.group(2)),
            'message': m.group(3),
            'name': m.group(4),
            'confidence': int(m.group(5)),
            'size_lines': 1
        })
    else:
        m2 = re.match(r'^(.+?):(\d+): (.+?) \((\d+)% confidence\)', line)
        if m2:
            results.append({
                'file': m2.group(1),
                'line': int(m2.group(2)),
                'message': m2.group(3),
                'name': '',
                'confidence': int(m2.group(4)),
                'size_lines': 1
            })

json.dump(results, open('$out_file', 'w'), indent=2)
print(f'[vulture] $pkg_name: {len(results)} findings')
" <<< "$raw_output"
}

run_grimp() {
  local pkg_name="$1" pkg_path="$2" src_path="$3"
  local full_src="$REPO_ROOT/$src_path"
  local out_file="$REPORT_DIR/raw/${pkg_name}-grimp.json"

  if [[ ! -d "$full_src" ]]; then
    return 0
  fi

  echo "[grimp] Analyzing imports for $pkg_name ($src_path)..."

  python3 -c "
import json, os, sys

sys.path.insert(0, '$REPO_ROOT/$pkg_path')
sys.path.insert(0, '$full_src')

try:
    import grimp
except ImportError:
    json.dump({'error': 'grimp not installed'}, open('$out_file', 'w'))
    sys.exit(0)

src = '$full_src'
if not os.path.isdir(src):
    json.dump({'error': 'src dir not found'}, open('$out_file', 'w'))
    sys.exit(0)

top_packages = []
for item in os.listdir(src):
    item_path = os.path.join(src, item)
    if os.path.isdir(item_path) and os.path.exists(os.path.join(item_path, '__init__.py')):
        top_packages.append(item)
    elif item.endswith('.py') and item != '__init__.py':
        top_packages.append(item[:-3])

results = {'packages_found': top_packages, 'orphan_modules': [], 'import_errors': []}

for pkg in top_packages:
    try:
        graph = grimp.build_graph(pkg)
        all_modules = set(graph.modules)
        has_importer = set()
        for mod in all_modules:
            importers = graph.find_modules_directly_imported_by(mod)
            has_importer.update(importers)

        orphans = []
        for mod in all_modules:
            if mod not in has_importer and mod != pkg:
                downstream = graph.find_modules_that_directly_import(mod)
                if not downstream:
                    orphans.append(mod)

        results['orphan_modules'].extend([{'package': pkg, 'module': m} for m in sorted(orphans)])
    except Exception as e:
        results['import_errors'].append({'package': pkg, 'error': str(e)})

json.dump(results, open('$out_file', 'w'), indent=2)
print(f'[grimp] $pkg_name: {len(results[\"orphan_modules\"])} orphan modules found')
" 2>&1 || echo "[grimp] $pkg_name: analysis failed (non-fatal)"
}

PACKAGES=$(python3 -c "
import json
data = json.load(open('$PATHS_JSON'))
for p in data['python_packages']:
    print(p['name'], p['path'], p.get('src', p['path']))
")

while IFS=' ' read -r name path src; do
  run_ruff "$name" "$path"
done <<< "$PACKAGES"

if ! $QUICK_MODE; then
  while IFS=' ' read -r name path src; do
    run_vulture "$name" "$path"
  done <<< "$PACKAGES"

  while IFS=' ' read -r name path src; do
    run_grimp "$name" "$path" "$src"
  done <<< "$PACKAGES"
fi

echo "[python] All Python scans complete."
