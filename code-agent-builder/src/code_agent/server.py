"""FastMCP server exposing code graph tools, prompts, and resources to Cursor."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from fastmcp import FastMCP

from code_agent.enricher.llm_enricher import LLMEnricher
from code_agent.graph.kuzu_client import KuzuClient
from code_agent.graph import queries
from code_agent.indexer import Indexer
from code_agent.temporal.event_store import EventStore
from code_agent.watcher import FileWatcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("code-agent")

DATA_DIR = Path(os.environ.get("CODE_AGENT_DATA_DIR", Path.home() / ".code-agent"))
KUZU_PATH = DATA_DIR / "graph.kuzu"
SQLITE_PATH = DATA_DIR / "events.db"

mcp = FastMCP(
    "code-agent",
    instructions="""Code Agent MCP Server - Kodu graph olarak saklar ve temporal degisiklikleri izler.

Bu server su yeteneklere sahiptir:
1. Kod yapisini graph olarak sorgulama (Cypher ile)
2. Fonksiyon gecmisini ve evrimini takip etme (SQL ile)
3. Projeleri otomatik indeksleme ve dosya degisikliklerini izleme

Kod yazmadan once MUTLAKA mevcut yapilari kontrol edin:
- query_code_graph ile Cypher sorgusu yazin
- search_symbols ile benzer implementasyonlari arayin
- find_callers ile bagimliliklari kontrol edin

Graph Kuzu veritabaninda saklanir (Cypher sorgu dili).
Temporal veriler SQLite'da saklanir (SQL sorgu dili).
File watcher degisiklikleri otomatik izler.
""",
)

graph: KuzuClient | None = None
events: EventStore | None = None
watcher: FileWatcher | None = None
enricher: LLMEnricher | None = None


def _ensure_initialized() -> tuple[KuzuClient, EventStore]:
    global graph, events, watcher, enricher
    if graph is None:
        graph = KuzuClient(KUZU_PATH)
        graph.init_schema()
    if events is None:
        events = EventStore(SQLITE_PATH)
    if enricher is None:
        enricher = LLMEnricher(graph)
    return graph, events


def _get_indexer(project_root: str) -> Indexer:
    g, e = _ensure_initialized()
    return Indexer(g, e, project_root)


def _ensure_watcher() -> FileWatcher:
    global watcher
    if watcher is None:
        def on_change(path: str):
            g, e = _ensure_initialized()
            projects = e.get_watched_projects()
            for p in projects:
                if path.startswith(p["project_path"]):
                    indexer = Indexer(g, e, p["project_path"])
                    result = indexer.index_file(path)
                    logger.info("Auto-indexed %s: %s", path, result)
                    return
        watcher = FileWatcher(on_change)
    return watcher


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MCP TOOLS - Graph Sorgulama (Kuzu)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
def query_code_graph(cypher: str) -> str:
    """Kuzu graph veritabaninda serbest Cypher sorgusu calistir.

    Ornek sorgular:
    - MATCH (f:Function) WHERE f.name CONTAINS 'auth' RETURN f.qualified_name, f.signature
    - MATCH (m:Module)-[:CONTAINS]->(f:Function) RETURN m.name, collect(f.name) AS functions
    - MATCH (a:Function)-[:CALLS]->(b:Function) WHERE a.name = 'process' RETURN b.name
    - MATCH (c:Class)-[:INHERITS]->(parent:Class) RETURN c.name, parent.name

    Node tipleri: Module, Class, Function, Parameter, Variable, Decorator
    Iliski tipleri: CONTAINS, HAS_METHOD, HAS_PROPERTY, INHERITS, CALLS,
                    IMPORTS, HAS_PARAMETER, RETURNS_TYPE, USES, DECORATED_BY
    """
    g, _ = _ensure_initialized()
    try:
        rows = g.query(cypher)
        return json.dumps(rows, default=str, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Cypher error: {e}"


@mcp.tool()
def get_project_overview() -> str:
    """Indekslenmiş projenin genel istatistiklerini ve modül listesini döndür."""
    g, e = _ensure_initialized()
    stats = g.get_stats()
    modules = g.query(queries.MODULE_LIST)
    projects = e.get_watched_projects()
    return json.dumps({
        "stats": stats,
        "modules": modules,
        "watched_projects": projects,
    }, default=str, ensure_ascii=False, indent=2)


@mcp.tool()
def get_module_structure(module_path: str) -> str:
    """Bir modulun detayli yapisini getir (fonksiyonlar, siniflar, importlar).

    Args:
        module_path: Modulun dosya yolu (orn: /path/to/file.py)
    """
    g, _ = _ensure_initialized()
    rows = g.query(queries.MODULE_STRUCTURE, {"path": module_path})
    return json.dumps(rows, default=str, ensure_ascii=False, indent=2)


@mcp.tool()
def find_callers(function_name: str) -> str:
    """Bir fonksiyonu cagiran tum fonksiyonlari bul.

    Args:
        function_name: Fonksiyon adi veya qualified name
    """
    g, _ = _ensure_initialized()
    rows = g.query(queries.FIND_CALLERS, {"name": function_name})
    return json.dumps(rows, default=str, ensure_ascii=False, indent=2)


@mcp.tool()
def find_dependencies(module_path: str, depth: int = 3) -> str:
    """Bir modulun bagimlilik agacini cikar.

    Args:
        module_path: Modulun dosya yolu
        depth: Maksimum derinlik (varsayilan: 3)
    """
    g, _ = _ensure_initialized()
    cypher = queries.FIND_DEPENDENCIES % min(depth, 10)
    rows = g.query(cypher, {"path": module_path})
    return json.dumps(rows, default=str, ensure_ascii=False, indent=2)


@mcp.tool()
def get_class_hierarchy(class_name: str) -> str:
    """Bir sinifin kalitim agacini goster.

    Args:
        class_name: Sinif adi
    """
    g, _ = _ensure_initialized()
    rows = g.query(queries.CLASS_HIERARCHY, {"name": class_name})
    return json.dumps(rows, default=str, ensure_ascii=False, indent=2)


@mcp.tool()
def search_symbols(query: str, symbol_type: str = "Function") -> str:
    """Fonksiyon, sinif veya modul ara.

    Args:
        query: Arama terimi (isim veya qualified name icinde arar)
        symbol_type: 'Function', 'Class', veya 'Module'
    """
    g, _ = _ensure_initialized()
    valid_types = ("Function", "Class", "Module", "Variable", "Decorator")
    if symbol_type not in valid_types:
        symbol_type = "Function"
    if symbol_type == "Module":
        cypher = queries.SEARCH_SYMBOLS_MODULE
    else:
        cypher = queries.SEARCH_SYMBOLS_DEFAULT % symbol_type
    rows = g.query(cypher, {"query": query})
    return json.dumps(rows, default=str, ensure_ascii=False, indent=2)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MCP TOOLS - Temporal Sorgulama (SQLite)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
def get_function_history(function_id: str, limit: int = 10) -> str:
    """Bir fonksiyonun tum versiyon gecmisini getir.

    Args:
        function_id: Fonksiyonun qualified_name'i (orn: module.Class.method)
        limit: Maksimum versiyon sayisi
    """
    _, e = _ensure_initialized()
    rows = e.get_function_history(function_id, limit=limit)
    return json.dumps(rows, default=str, ensure_ascii=False, indent=2)


@mcp.tool()
def get_function_diff(function_id: str, version_a: int = 0, version_b: int = 0) -> str:
    """Bir fonksiyonun iki versiyonu arasindaki farki goster.

    Args:
        function_id: Fonksiyonun qualified_name'i
        version_a: Eski versiyon (0 = sondan bir onceki)
        version_b: Yeni versiyon (0 = en son)
    """
    _, e = _ensure_initialized()
    history = e.get_function_history(function_id, limit=50)
    if len(history) < 2:
        return json.dumps({"error": "Not enough versions for diff"})

    if version_a == 0 and version_b == 0:
        ver_b = history[0]
        ver_a = history[1]
    else:
        ver_a = next((h for h in history if h["version"] == version_a), None)
        ver_b = next((h for h in history if h["version"] == version_b), None)
        if not ver_a or not ver_b:
            return json.dumps({"error": f"Version {version_a} or {version_b} not found"})

    from code_agent.temporal.diff_engine import compute_diff
    diff = compute_diff(ver_a["body_text"], ver_b["body_text"], function_id)

    return json.dumps({
        "function": function_id,
        "from_version": ver_a["version"],
        "to_version": ver_b["version"],
        "from_commit": ver_a.get("git_commit", ""),
        "to_commit": ver_b.get("git_commit", ""),
        "diff": diff,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def get_recent_changes(since: str = "", entity_type: str = "", limit: int = 30) -> str:
    """Son degisiklikleri listele.

    Args:
        since: Baslangic tarihi (orn: '2025-01-01', bos birakilirsa tum zamanlar)
        entity_type: 'function', 'class', 'module' (bos = hepsi)
        limit: Maksimum kayit sayisi
    """
    _, e = _ensure_initialized()
    rows = e.get_recent_changes(since=since, entity_type=entity_type, limit=limit)
    return json.dumps(rows, default=str, ensure_ascii=False, indent=2)


@mcp.tool()
def query_events(sql: str) -> str:
    """SQLite event store uzerinde serbest SQL sorgusu calistir (sadece SELECT).

    Tablolar:
    - function_versions: function_id, module_path, function_name, signature,
      body_hash, body_text, docstring, complexity, version, git_commit, git_author,
      git_timestamp, created_at
    - change_events: entity_type, entity_id, event_type, before_hash, after_hash,
      diff_text, git_commit, git_message, created_at
    - snapshots: name, git_commit, node_count, edge_count, module_count,
      function_count, class_count, created_at

    Ornek:
    SELECT function_name, COUNT(*) as changes FROM change_events
    WHERE entity_type='function' GROUP BY entity_id ORDER BY changes DESC LIMIT 10
    """
    _, e = _ensure_initialized()
    try:
        rows = e.query(sql)
        return json.dumps(rows, default=str, ensure_ascii=False, indent=2)
    except Exception as err:
        return f"SQL error: {err}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MCP TOOLS - Islem
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
def index_project(project_path: str, languages: list[str] | None = None) -> str:
    """Bir projeyi tara, parse et ve graph veritabanina yukle.

    Ilk seferde cagrilmali. Sonrasinda file watcher degisiklikleri otomatik izler.

    Args:
        project_path: Proje kok dizini (orn: /Users/me/project)
        languages: Desteklenen diller listesi ['python', 'typescript'] (varsayilan: ikisi de)
    """
    g, e = _ensure_initialized()
    indexer = _get_indexer(project_path)
    stats = indexer.index_project(languages)

    w = _ensure_watcher()
    w.watch(project_path)

    module_list = g.query("MATCH (m:Module) RETURN m.path AS path, m.language AS language")
    if enricher and module_list:
        enricher.enrich_project_async(module_list)
        stats["llm_enrichment"] = "started_in_background"

    return json.dumps(stats, default=str, ensure_ascii=False, indent=2)


@mcp.tool()
def import_git_history(project_path: str, max_commits: int = 20) -> str:
    """Git gecmisinden fonksiyon versiyonlarini import et.

    Args:
        project_path: Proje kok dizini
        max_commits: Her dosya icin kontrol edilecek maksimum commit sayisi
    """
    indexer = _get_indexer(project_path)
    result = indexer.import_git_history(max_commits=max_commits)
    return json.dumps(result, default=str, ensure_ascii=False, indent=2)


@mcp.tool()
def get_watcher_status() -> str:
    """File watcher durumunu goster (izlenen klasorler, son degisiklikler)."""
    w = _ensure_watcher()
    return json.dumps(w.get_status(), default=str, ensure_ascii=False, indent=2)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MCP PROMPTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.prompt()
def analyze_before_coding() -> str:
    """Kod yazmadan once projeyi analiz et - tekrar ve eksik kodlari onle."""
    return """Bu projede kod yazmadan once su adimlari MUTLAKA izle:

1. **Mevcut yapilari kontrol et:**
   query_code_graph ile ilgili modulleri, sinif ve fonksiyonlari ara.
   Ornek: MATCH (f:Function) WHERE f.name CONTAINS 'auth' RETURN f.qualified_name, f.signature

2. **Benzer implementasyonlari ara:**
   search_symbols ile ayni isi yapan kod var mi kontrol et.
   Ayni islevde kod zaten varsa onu kullan veya genislet.

3. **Bagimliliklari belirle:**
   find_dependencies ile etkilenecek modulleri gor.
   find_callers ile degistirecek fonksiyonu kimin cagirdigini kontrol et.

4. **Mevcut pattern'lere uy:**
   get_module_structure ile yakin modullerin yapisini incele.
   Ayni klasordeki dosyalarin kullandigi pattern'i takip et.

5. **Eksik birakma:**
   Yazdirdigin kodun tum importlari, tum hata yonetimi ve tum edge case'leri
   mevcut kodla tutarli olmali. Graph'tan kontrol et."""


@mcp.prompt()
def understand_project() -> str:
    """Projeyi hizlica anlamak icin kullanilacak arastirma plani."""
    return """Projeyi anlamak icin su adimlari izle:

1. get_project_overview ile genel yapi ve istatistikleri gor
2. query_code_graph ile modulleri listele:
   MATCH (m:Module) RETURN m.path, m.name, m.line_count ORDER BY m.line_count DESC
3. find_dependencies ile ana modullerin bagimliliklarini incele
4. get_class_hierarchy ile sinif yapilerini gor
5. query_code_graph ile en cok cagirilan fonksiyonlari bul:
   MATCH (f:Function)<-[:CALLS]-(caller:Function)
   RETURN f.name, f.qualified_name, count(caller) AS call_count ORDER BY call_count DESC LIMIT 20"""


@mcp.prompt()
def review_changes() -> str:
    """Son yapilan degisiklikleri incele ve tutarliligi kontrol et."""
    return """Son degisiklikleri incelemek icin:

1. get_recent_changes ile son degisiklikleri listele
2. Her degisiklik icin:
   - get_function_history ile fonksiyon gecmisini gor
   - get_function_diff ile ne degistigini incele
   - find_callers ile etkilenen yerleri kontrol et
3. query_events ile en cok degisen fonksiyonlari bul:
   SELECT entity_id, COUNT(*) as changes FROM change_events
   WHERE entity_type='function' GROUP BY entity_id ORDER BY changes DESC LIMIT 10"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MCP RESOURCES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.resource("code-graph://schema")
def graph_schema() -> str:
    """Kuzu graph schema - node ve relationship tipleri."""
    return """# Code Graph Schema

## Node Types
- **Module**: path (PK), name, language, file_hash, line_count, summary (LLM)
- **Class**: qualified_name (PK), name, docstring, start_line, end_line, is_abstract, complexity, design_pattern (LLM)
- **Function**: qualified_name (PK), name, signature, docstring, body_hash, start_line, end_line, complexity, is_async, param_count, purpose (LLM), category (LLM)
- **Parameter**: qualified_name (PK), name, type_hint, default_value, position
- **Variable**: qualified_name (PK), name, type_hint, scope, is_class_field
- **Decorator**: qualified_name (PK), name, has_args

## Relationship Types
- CONTAINS: Module -> Class/Function/Variable
- HAS_METHOD: Class -> Function
- HAS_PROPERTY: Class -> Variable
- INHERITS: Class -> Class
- CALLS: Function -> Function
- IMPORTS: Module -> Module (imported_name, alias)
- HAS_PARAMETER: Function -> Parameter
- RETURNS_TYPE: Function -> Class (return_type)
- USES: Function -> Function/Class/Variable
- DECORATED_BY: Function/Class -> Decorator"""


@mcp.resource("code-graph://stats")
def graph_stats() -> str:
    """Guncel graph istatistikleri."""
    g, _ = _ensure_initialized()
    stats = g.get_stats()
    return json.dumps(stats, indent=2)


@mcp.resource("code-graph://recent-changes")
def recent_changes_resource() -> str:
    """Son 24 saat degisiklikler."""
    _, e = _ensure_initialized()
    rows = e.get_recent_changes(limit=50)
    return json.dumps(rows, default=str, indent=2)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Entry Point
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def main():
    mcp.run()


if __name__ == "__main__":
    main()
