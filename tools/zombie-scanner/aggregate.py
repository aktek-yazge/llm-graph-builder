#!/usr/bin/env python3
"""
Aggregate raw scanner outputs into a unified zombie-report.json and zombie-report.md.

Usage: python3 aggregate.py <report_dir>
  where <report_dir> contains a raw/ subdirectory with per-tool JSON files.
"""

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def load_json(path: str):
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return None


def _detect_repo_root(report_dir: str) -> str:
    """Walk up from report_dir to find the repo root (.zombie-reports is at root level)."""
    p = Path(report_dir).resolve()
    while p != p.parent:
        if (p / ".zombie-reports").exists() or (p / ".git").exists():
            return str(p)
        p = p.parent
    return str(Path(report_dir).resolve().parent.parent)


REPO_ROOT = ""


def normalize_path(filepath: str) -> str:
    """Normalize absolute paths to repo-relative paths."""
    if not filepath:
        return filepath
    if REPO_ROOT and filepath.startswith(REPO_ROOT):
        rel = filepath[len(REPO_ROOT):]
        if rel.startswith("/"):
            rel = rel[1:]
        return rel
    return filepath


def _extract_symbol_name_from_ruff_msg(message: str) -> str:
    """Extract the actual symbol name from ruff's message.
    e.g. 'Local variable `agent` is assigned to but never used' -> 'agent'
    e.g. 'Redefinition of unused `time` from line 54' -> 'time'
    """
    import re
    m = re.search(r'`(\w+)`', message)
    if m:
        return m.group(1)
    return ""


def process_ruff(raw_dir: str) -> tuple[list, list]:
    """Parse ruff JSON outputs. Returns (unused_imports, dead_symbols)."""
    unused_imports = []
    dead_symbols = []

    for fname in sorted(os.listdir(raw_dir)):
        if not fname.endswith("-ruff.json"):
            continue
        pkg = fname.replace("-ruff.json", "")
        data = load_json(os.path.join(raw_dir, fname))
        if not data or not isinstance(data, list):
            continue

        for item in data:
            code = item.get("code", "")
            raw_msg = item.get("message", "")
            sym_name = _extract_symbol_name_from_ruff_msg(raw_msg)
            entry = {
                "file": normalize_path(item.get("filename", "")),
                "name": sym_name or raw_msg,
                "line": item.get("location", {}).get("row", 0),
                "evidence": [f"ruff:{code}"],
                "source": "ruff",
            }
            if code == "F401":
                unused_imports.append(entry)
            elif code in ("F811", "F841"):
                entry["kind"] = "variable" if code == "F841" else "redefinition"
                dead_symbols.append(entry)

    return unused_imports, dead_symbols


def process_vulture(raw_dir: str) -> list:
    """Parse vulture JSON outputs."""
    results = []
    for fname in sorted(os.listdir(raw_dir)):
        if not fname.endswith("-vulture.json"):
            continue
        pkg = fname.replace("-vulture.json", "")
        data = load_json(os.path.join(raw_dir, fname))
        if not data or not isinstance(data, list):
            continue

        for item in data:
            msg = item.get("message", "")
            kind = "unknown"
            if "function" in msg or "method" in msg:
                kind = "function"
            elif "class" in msg:
                kind = "class"
            elif "variable" in msg:
                kind = "variable"
            elif "import" in msg:
                kind = "import"
            elif "attribute" in msg:
                kind = "attribute"
            elif "property" in msg:
                kind = "property"

            results.append({
                "file": normalize_path(item.get("file", "")),
                "name": item.get("name", ""),
                "kind": kind,
                "line": item.get("line", 0),
                "confidence_pct": item.get("confidence", 0),
                "size_lines": item.get("size_lines", 1),
                "evidence": [f"vulture:{item.get('confidence', 0)}"],
                "source": "vulture",
            })

    return results


def process_grimp(raw_dir: str) -> list:
    """Parse grimp JSON outputs for orphan modules."""
    orphans = []
    for fname in sorted(os.listdir(raw_dir)):
        if not fname.endswith("-grimp.json"):
            continue
        data = load_json(os.path.join(raw_dir, fname))
        if not data or "orphan_modules" not in data:
            continue

        for item in data["orphan_modules"]:
            orphans.append({
                "package": item.get("package", ""),
                "module": item.get("module", ""),
                "evidence": ["grimp:orphan"],
                "source": "grimp",
            })

    return orphans


def process_knip(raw_dir: str) -> dict:
    """Parse knip JSON outputs."""
    result = {
        "dead_files": [],
        "dead_exports": [],
        "unused_deps": [],
        "unused_dev_deps": [],
        "unlisted": [],
        "unused_types": [],
    }

    for fname in sorted(os.listdir(raw_dir)):
        if not fname.endswith("-knip.json"):
            continue
        pkg = fname.replace("-knip.json", "")
        data = load_json(os.path.join(raw_dir, fname))
        if not data or not isinstance(data, dict):
            continue

        issues = data.get("issues", data.get("files", []))
        if isinstance(issues, list):
            for issue in issues:
                if not isinstance(issue, dict):
                    continue

                for f in issue.get("files", []):
                    result["dead_files"].append({
                        "path": normalize_path(f.get("name", str(f)) if isinstance(f, dict) else str(f)),
                        "lang": "ts",
                        "evidence": ["knip:files"],
                        "source": "knip",
                        "package": pkg,
                    })

                for exp in issue.get("exports", []):
                    if isinstance(exp, dict):
                        result["dead_exports"].append({
                            "file": normalize_path(issue.get("file", "")),
                            "name": exp.get("name", ""),
                            "line": exp.get("line", 0),
                            "col": exp.get("col", 0),
                            "evidence": ["knip:exports"],
                            "source": "knip",
                            "package": pkg,
                        })

                for dep in issue.get("dependencies", []):
                    name = dep.get("name", str(dep)) if isinstance(dep, dict) else str(dep)
                    result["unused_deps"].append({
                        "package_name": name,
                        "evidence": ["knip:dependencies"],
                        "source": "knip",
                        "manifest_package": pkg,
                    })

                for dep in issue.get("devDependencies", []):
                    name = dep.get("name", str(dep)) if isinstance(dep, dict) else str(dep)
                    result["unused_dev_deps"].append({
                        "package_name": name,
                        "evidence": ["knip:devDependencies"],
                        "source": "knip",
                        "manifest_package": pkg,
                    })

                for t in issue.get("types", []):
                    if isinstance(t, dict):
                        result["unused_types"].append({
                            "file": normalize_path(issue.get("file", "")),
                            "name": t.get("name", ""),
                            "evidence": ["knip:types"],
                            "source": "knip",
                            "package": pkg,
                        })

        # Also check top-level keys (older knip format)
        for f in data.get("files", []):
            if isinstance(f, str):
                result["dead_files"].append({
                    "path": normalize_path(f),
                    "lang": "ts",
                    "evidence": ["knip:files"],
                    "source": "knip",
                    "package": pkg,
                })

    return result


def process_madge(raw_dir: str) -> tuple[list, list]:
    """Parse madge circular + orphan outputs."""
    circular = []
    orphans = []

    for fname in sorted(os.listdir(raw_dir)):
        if fname.endswith("-madge-circular.json"):
            pkg = fname.replace("-madge-circular.json", "")
            data = load_json(os.path.join(raw_dir, fname))
            if data and isinstance(data, list):
                for cycle in data:
                    circular.append({
                        "cycle": cycle,
                        "package": pkg,
                        "evidence": ["madge:circular"],
                    })

        elif fname.endswith("-madge-orphans.json"):
            pkg = fname.replace("-madge-orphans.json", "")
            data = load_json(os.path.join(raw_dir, fname))
            if data and isinstance(data, list):
                for f in data:
                    orphans.append({
                        "path": f,
                        "lang": "ts",
                        "evidence": ["madge:orphans"],
                        "source": "madge",
                        "package": pkg,
                    })

    return circular, orphans


def merge_dead_files(knip_files: list, madge_orphans: list, grimp_orphans: list) -> list:
    """Merge dead file detections from multiple sources, computing confidence."""
    by_path = defaultdict(lambda: {"evidence": [], "lang": "unknown", "sources": set()})

    for item in knip_files:
        key = item["path"]
        by_path[key]["evidence"].extend(item["evidence"])
        by_path[key]["lang"] = item.get("lang", "ts")
        by_path[key]["sources"].add("knip")
        by_path[key]["package"] = item.get("package", "")

    for item in madge_orphans:
        key = item["path"]
        by_path[key]["evidence"].extend(item["evidence"])
        by_path[key]["lang"] = item.get("lang", "ts")
        by_path[key]["sources"].add("madge")
        by_path[key]["package"] = item.get("package", "")

    for item in grimp_orphans:
        key = item["module"]
        by_path[key]["evidence"].extend(item["evidence"])
        by_path[key]["lang"] = "py"
        by_path[key]["sources"].add("grimp")
        by_path[key]["package"] = item.get("package", "")

    results = []
    for path, info in sorted(by_path.items()):
        n_sources = len(info["sources"])
        confidence = min(0.5 + 0.25 * n_sources, 1.0)
        results.append({
            "path": path,
            "lang": info["lang"],
            "evidence": info["evidence"],
            "confidence": round(confidence, 2),
            "package": info.get("package", ""),
        })

    return sorted(results, key=lambda x: -x["confidence"])


def merge_dead_symbols(ruff_symbols: list, vulture_symbols: list, knip_exports: list) -> list:
    """Merge dead symbol detections, computing confidence."""
    by_key = defaultdict(lambda: {"evidence": [], "sources": set(), "kind": "unknown", "line": 0, "file": ""})

    for item in ruff_symbols:
        key = f"{item['file']}:{item.get('name', '')}"
        by_key[key]["evidence"].extend(item["evidence"])
        by_key[key]["sources"].add("ruff")
        by_key[key]["kind"] = item.get("kind", "unknown")
        by_key[key]["line"] = item.get("line", 0)
        by_key[key]["file"] = item.get("file", "")
        by_key[key]["name"] = item.get("name", "")

    for item in vulture_symbols:
        key = f"{item['file']}:{item.get('name', '')}"
        by_key[key]["evidence"].extend(item["evidence"])
        by_key[key]["sources"].add("vulture")
        by_key[key]["kind"] = item.get("kind", "unknown")
        by_key[key]["line"] = item.get("line", 0)
        by_key[key]["file"] = item.get("file", "")
        by_key[key]["name"] = item.get("name", "")

    for item in knip_exports:
        key = f"{item['file']}:{item.get('name', '')}"
        by_key[key]["evidence"].extend(item["evidence"])
        by_key[key]["sources"].add("knip")
        by_key[key]["kind"] = "export"
        by_key[key]["line"] = item.get("line", 0)
        by_key[key]["file"] = item.get("file", "")
        by_key[key]["name"] = item.get("name", "")

    results = []
    for key, info in sorted(by_key.items()):
        n_sources = len(info["sources"])
        confidence = min(0.4 + 0.3 * n_sources, 1.0)
        results.append({
            "file": info["file"],
            "name": info.get("name", ""),
            "kind": info["kind"],
            "line": info["line"],
            "evidence": info["evidence"],
            "confidence": round(confidence, 2),
        })

    return sorted(results, key=lambda x: -x["confidence"])


def build_report(report_dir: str) -> dict:
    raw_dir = os.path.join(report_dir, "raw")
    if not os.path.isdir(raw_dir):
        return {"error": "No raw/ directory found"}

    global REPO_ROOT
    REPO_ROOT = _detect_repo_root(report_dir)

    ruff_imports, ruff_symbols = process_ruff(raw_dir)
    vulture_symbols = process_vulture(raw_dir)
    grimp_orphans = process_grimp(raw_dir)
    knip_data = process_knip(raw_dir)
    madge_circular, madge_orphans = process_madge(raw_dir)

    dead_files = merge_dead_files(knip_data["dead_files"], madge_orphans, grimp_orphans)
    dead_symbols = merge_dead_symbols(ruff_symbols, vulture_symbols, knip_data["dead_exports"])

    unused_deps = knip_data["unused_deps"] + knip_data["unused_dev_deps"]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo": "llm-graph-builder",
        "summary": {
            "dead_files": len(dead_files),
            "dead_symbols": len(dead_symbols),
            "unused_imports": len(ruff_imports),
            "unused_dependencies": len(unused_deps),
            "circular_deps": len(madge_circular),
        },
        "dead_files": dead_files,
        "dead_symbols": dead_symbols,
        "unused_imports": ruff_imports,
        "unused_dependencies": unused_deps,
        "circular_deps": madge_circular,
    }

    return report


def render_markdown(report: dict) -> str:
    lines = [
        "# Zombie Code Report",
        "",
        f"Generated: {report.get('generated_at', 'unknown')}",
        "",
        "## Summary",
        "",
        f"| Category | Count |",
        f"|----------|-------|",
    ]

    summary = report.get("summary", {})
    for key, val in summary.items():
        label = key.replace("_", " ").title()
        lines.append(f"| {label} | {val} |")

    lines.append("")

    def section(title, items, high_thresh=0.8, med_thresh=0.5):
        if not items:
            return
        lines.append(f"## {title}")
        lines.append("")

        high = [i for i in items if i.get("confidence", 0) >= high_thresh]
        med = [i for i in items if med_thresh <= i.get("confidence", 0) < high_thresh]
        low = [i for i in items if i.get("confidence", 0) < med_thresh]

        for label, group in [("HIGH confidence", high), ("MEDIUM confidence", med), ("LOW confidence", low)]:
            if not group:
                continue
            lines.append(f"### {label} ({len(group)})")
            lines.append("")
            for item in group:
                conf = item.get("confidence", "?")
                path = item.get("path", item.get("file", "?"))
                name = item.get("name", "")
                evidence = ", ".join(item.get("evidence", []))
                if name:
                    lines.append(f"- `{path}` — **{name}** (conf={conf}, evidence: {evidence})")
                else:
                    lines.append(f"- `{path}` (conf={conf}, evidence: {evidence})")
            lines.append("")

    section("Dead Files", report.get("dead_files", []))
    section("Dead Symbols", report.get("dead_symbols", []))

    unused_imports = report.get("unused_imports", [])
    if unused_imports:
        lines.append("## Unused Imports")
        lines.append("")
        by_file = defaultdict(list)
        for item in unused_imports:
            by_file[item.get("file", "?")].append(item)
        for f in sorted(by_file):
            lines.append(f"### `{f}` ({len(by_file[f])} unused)")
            lines.append("")
            for item in by_file[f]:
                lines.append(f"- Line {item.get('line', '?')}: {item.get('name', '?')}")
            lines.append("")

    unused_deps = report.get("unused_dependencies", [])
    if unused_deps:
        lines.append("## Unused Dependencies")
        lines.append("")
        for item in unused_deps:
            pkg = item.get("package_name", "?")
            manifest = item.get("manifest_package", "?")
            evidence = ", ".join(item.get("evidence", []))
            lines.append(f"- **{pkg}** in {manifest} ({evidence})")
        lines.append("")

    circular = report.get("circular_deps", [])
    if circular:
        lines.append("## Circular Dependencies")
        lines.append("")
        for item in circular:
            cycle = item.get("cycle", [])
            pkg = item.get("package", "")
            lines.append(f"- [{pkg}] {' -> '.join(cycle)}")
        lines.append("")

    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 aggregate.py <report_dir>")
        sys.exit(1)

    report_dir = sys.argv[1]
    report = build_report(report_dir)

    json_path = os.path.join(report_dir, "zombie-report.json")
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"[aggregate] JSON report: {json_path}")

    md_path = os.path.join(report_dir, "zombie-report.md")
    md_content = render_markdown(report)
    with open(md_path, "w") as f:
        f.write(md_content)
    print(f"[aggregate] Markdown report: {md_path}")

    s = report.get("summary", {})
    print(f"[aggregate] Summary: {s.get('dead_files', 0)} dead files, "
          f"{s.get('dead_symbols', 0)} dead symbols, "
          f"{s.get('unused_imports', 0)} unused imports, "
          f"{s.get('unused_dependencies', 0)} unused deps, "
          f"{s.get('circular_deps', 0)} circular deps")


if __name__ == "__main__":
    main()
