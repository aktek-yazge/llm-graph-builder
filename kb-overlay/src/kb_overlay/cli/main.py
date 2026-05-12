"""kb-overlay CLI giriş noktası.

Kurulumdan sonra ``kbo`` komutu olarak kullanılabilir.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..dictionary import AliasStore, EntityType
from ..discovery import DocumentExtractor, get_ner_backend
from ..gazetteer import GazetteerBuilder, GazetteerSpotter
from ..resolver import CascadingResolver
from ..review import PendingKind, PendingStatus, ReviewQueue

console = Console()

DEFAULT_DB = "data/aliases.db"


def _compute_content_hash(text: str) -> str:
    """Content hash — SHA-256 ilk 16 hex char (Phase 3 cache key parçası)."""
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _build_extractor_version(
    *,
    ner_name: str,
    llm_provider: Optional[str],
    llm_model: Optional[str],
    schema_version: str = "2",
) -> str:
    """Cache invalidation için stabil bir extractor identity string'i.

    Bu string değişirse (NER backend güncellendi, LLM model değişti, schema
    bump'landı) eski cache hit'leri otomatik invalidate olur.
    """
    parts = [f"ner={ner_name}", f"schema={schema_version}"]
    if llm_provider:
        parts.append(f"llm={llm_provider}")
    if llm_model:
        parts.append(f"model={llm_model}")
    return ";".join(parts)


def _open_store(db_path: str) -> AliasStore:
    store = AliasStore(db_path)
    store.init_schema()
    return store


def _build_extractor(
    store: AliasStore,
    *,
    ner_name: str = "naive",
    use_gazetteer: bool = True,
    fuzzy_threshold: int = 90,
    auto_create: bool = True,
    review_queue: Optional[ReviewQueue] = None,
    ner_kwargs: Optional[dict] = None,
    ocr_normalize: bool = True,
) -> DocumentExtractor:
    spotter = None
    if use_gazetteer and GazetteerBuilder.is_available():
        try:
            automaton = GazetteerBuilder(store).build()
            spotter = GazetteerSpotter(automaton)
        except Exception as exc:
            console.print(f"[yellow]Gazetteer build atlandı: {exc}[/yellow]")
    elif use_gazetteer:
        console.print("[yellow]pyahocorasick yok → gazetteer atlandı[/yellow]")

    resolver = CascadingResolver(store, fuzzy_threshold=fuzzy_threshold)
    ner_backend = get_ner_backend(ner_name, **(ner_kwargs or {}))

    return DocumentExtractor(
        store,
        resolver,
        ner_backend=ner_backend,
        gazetteer_spotter=spotter,
        review_queue=review_queue,
        auto_create_new_entities=auto_create,
        ocr_normalize=ocr_normalize,
    )


# ---------------------------------------------------------------------------
# Top-level group
# ---------------------------------------------------------------------------


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--db",
    "db_path",
    default=DEFAULT_DB,
    show_default=True,
    help="SQLite alias dictionary path",
)
@click.pass_context
def cli(ctx: click.Context, db_path: str) -> None:
    """kb-overlay — discovery-first entity resolution CLI."""
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = db_path


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@cli.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """SQLite veritabanını ve şemayı başlat."""
    db_path = ctx.obj["db_path"]
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    store = _open_store(db_path)
    ReviewQueue(store)  # review tablolarını da oluştur
    console.print(Panel.fit(
        f"[green]Schema initialized[/green]\nDB: [cyan]{db_path}[/cyan]",
        title="kb-overlay",
    ))


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--file", "file_path", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--doc-id", "doc_id", required=True, help="Belge benzersiz ID")
@click.option("--title", default=None, help="Belge başlığı (opsiyonel)")
@click.option(
    "--ner",
    "ner_name",
    default="naive",
    type=click.Choice(["naive", "spacy", "gliner", "llm", "nuextract"]),
    help=(
        "NER backend. 'llm' = chat-style (Cosmos/Gemma/Trendyol vb.); "
        "'nuextract' = NuExtract 2.0 native template format (Ollama)."
    ),
)
@click.option(
    "--llm-provider",
    default="ollama",
    type=click.Choice(["ollama", "openai", "mlx", "stub"]),
    help="--ner llm için LLM provider. 'mlx' = vMLX / mlx_lm.server shortcut.",
)
@click.option(
    "--llm-model",
    default=None,
    help="LLM model adı (default: cosmos-gemma:9b @ ollama, "
    "gpt-4o-mini @ openai, mlx-community/gemma-4-e4b-it-nvfp4 @ mlx)",
)
@click.option(
    "--llm-base-url",
    default=None,
    help="LLM endpoint base URL (vMLX: http://127.0.0.1:8004/v1 vb.)",
)
@click.option(
    "--llm-api-key",
    default=None,
    help="LLM API key (lokal sunucularda gerekmez; default: 'not-needed')",
)
@click.option(
    "--llm-temperature",
    default=0.2,
    type=float,
    show_default=True,
    help="LLM sampling sıcaklığı (0.0 vermeyin — bazı modeller için sonsuz tekrar riski)",
)
@click.option(
    "--llm-max-concurrency",
    default=1,
    type=int,
    show_default=True,
    help="Aynı anda gönderilecek chunk sayısı (vMLX/mlx_lm için 4-8, Ollama için 1-2)",
)
@click.option(
    "--llm-chunk-chars",
    default=6000,
    type=int,
    show_default=True,
    help="Chunk başına maksimum karakter (Gemma 4 128K context için 12000-50000 denenebilir)",
)
@click.option("--no-gazetteer", is_flag=True, help="Gazetteer'ı devre dışı bırak")
@click.option("--no-auto-create", is_flag=True, help="Yeni entity yaratma; sadece review'a at")
@click.option("--fuzzy-threshold", default=90, type=int, show_default=True)
@click.option("--encoding", default="utf-8", show_default=True)
@click.option(
    "--no-ocr-normalize",
    is_flag=True,
    help="OCR satır kırılması düzeltmeyi kapat (örn. 'Aksa Akrilik\\nA.Ş.' → birleştirme yok)",
)
@click.option(
    "--force",
    is_flag=True,
    help="Content-hash cache'i bypass et — aynı dosya/extractor olsa bile yeniden işle",
)
@click.option(
    "--no-cache",
    is_flag=True,
    help="Cache'i ne oku ne de yaz (debugging / one-shot run)",
)
@click.pass_context
def ingest(
    ctx: click.Context,
    file_path: str,
    doc_id: str,
    title: Optional[str],
    ner_name: str,
    llm_provider: str,
    llm_model: Optional[str],
    llm_base_url: Optional[str],
    llm_api_key: Optional[str],
    llm_temperature: float,
    llm_max_concurrency: int,
    llm_chunk_chars: int,
    no_gazetteer: bool,
    no_auto_create: bool,
    fuzzy_threshold: int,
    encoding: str,
    no_ocr_normalize: bool,
    force: bool,
    no_cache: bool,
) -> None:
    """Bir OCR/metin dosyasını ingest et: mention çıkar → resolve → KB güncelle.

    Phase 3: SHA-256 content-hash cache. Aynı dosya + aynı extractor
    konfigürasyonu daha önce işlendiyse LLM ÇAĞRISI ATLANIR (cache hit).
    Cache key: ``(doc_id, sha256(content)[:16], extractor_version)``.
    extractor_version değişimi (NER/LLM güncellemesi) cache'i invalidate eder.
    --force ile cache bypass; --no-cache ile cache'i tamamen devre dışı bırak.
    """
    db_path = ctx.obj["db_path"]
    store = _open_store(db_path)
    review_q = ReviewQueue(store)

    text = Path(file_path).read_text(encoding=encoding)
    console.print(
        f"[cyan]Processing[/cyan] {file_path} ({len(text)} chars) → doc_id=[bold]{doc_id}[/bold]"
    )

    # Cache lookup (Phase 3) — LLM çağrısından ÖNCE
    content_hash = _compute_content_hash(text)
    extractor_version = _build_extractor_version(
        ner_name=ner_name,
        llm_provider=llm_provider if ner_name in ("llm", "nuextract") else None,
        llm_model=llm_model if ner_name in ("llm", "nuextract") else None,
    )

    if not no_cache and not force:
        cached = store.has_ingest_cache(
            doc_id=doc_id,
            content_hash=content_hash,
            extractor_version=extractor_version,
        )
        if cached is not None:
            console.print(
                Panel.fit(
                    f"[green]Cache hit[/green] — bu doc_id + content + extractor "
                    f"daha önce işlenmiş.\n"
                    f"  ingested_at:    {cached['ingested_at']}\n"
                    f"  mentions_count: {cached['mentions_count']}\n"
                    f"  relations_count:{cached['relations_count']}\n"
                    f"  extractor:      {extractor_version}\n"
                    f"  content_hash:   {content_hash}\n\n"
                    f"[dim]LLM çağrısı atlandı. Yeniden işlemek için: --force[/dim]",
                    title="ingest cache",
                )
            )
            return

    ner_kwargs: dict = {}
    if ner_name in ("llm", "nuextract"):
        ner_kwargs = {
            "provider": llm_provider,
            "model": llm_model,
            "base_url": llm_base_url,
            "api_key": llm_api_key,
            "temperature": llm_temperature,
            "max_concurrency": llm_max_concurrency,
            "chunk_chars": llm_chunk_chars,
        }
        # NuExtract için temperature default 0.0 olmalı (NuExtract dökümanı)
        # ve chunk_chars 18000 daha uygun (32K context). Kullanıcı CLI'den
        # değer geçtiyse onu yine dinleriz; geçmediyse Click default'u
        # (0.2 / 6000) ile geliyor — onları nuextract için override edelim.
        if ner_name == "nuextract":
            # Click default değerleriyse → NuExtract-uygun değerlere kaydır
            if llm_temperature == 0.2:  # Click default
                ner_kwargs["temperature"] = 0.0
            if llm_chunk_chars == 6000:  # Click default
                ner_kwargs["chunk_chars"] = 18000
    extractor = _build_extractor(
        store,
        ner_name=ner_name,
        use_gazetteer=not no_gazetteer,
        fuzzy_threshold=fuzzy_threshold,
        auto_create=not no_auto_create,
        review_queue=review_q,
        ner_kwargs=ner_kwargs,
        ocr_normalize=not no_ocr_normalize,
    )

    result = extractor.extract(text, doc_id=doc_id, title=title)

    # Cache write (relations_count yok — bu komut sadece mention extraction yapar;
    # relation extraction extract-relations CLI komutunda)
    if not no_cache:
        store.save_ingest_cache(
            doc_id=doc_id,
            content_hash=content_hash,
            extractor_version=extractor_version,
            relations_count=0,
            mentions_count=len(result.mentions),
        )

    # Özet
    summary = Table(title=f"Extraction summary — {doc_id}")
    summary.add_column("Metric", style="bold")
    summary.add_column("Value", style="cyan")
    summary.add_row("Mentions total", str(len(result.mentions)))
    summary.add_row("New candidates", str(len(result.new_candidates)))
    summary.add_row("Ambiguous", str(len(result.ambiguous)))
    for stage, n in sorted(result.counts_by_stage.items()):
        summary.add_row(f"  by stage: {stage}", str(n))
    for kind, n in sorted(result.counts_by_type.items()):
        summary.add_row(f"  by type:  {kind}", str(n))
    console.print(summary)

    # İlk 25 mention
    if result.mentions:
        t = Table(title=f"First {min(25, len(result.mentions))} mentions")
        t.add_column("#", justify="right")
        t.add_column("Surface")
        t.add_column("Type")
        t.add_column("Stage")
        t.add_column("Confidence", justify="right")
        t.add_column("Canonical ID")
        for i, m in enumerate(result.mentions[:25], 1):
            t.add_row(
                str(i),
                m.surface[:60],
                m.entity_type_guess,
                m.stage,
                f"{m.confidence:.2f}",
                m.canonical_id or "—",
            )
        console.print(t)


# ---------------------------------------------------------------------------
# lookup
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("query")
@click.option("--type", "entity_type", default=None, help="company | person | ...")
@click.pass_context
def lookup(ctx: click.Context, query: str, entity_type: Optional[str]) -> None:
    """Sözlükten bir surface_form ara (sorgu-zamanı resolver demosu)."""
    db_path = ctx.obj["db_path"]
    store = _open_store(db_path)
    resolver = CascadingResolver(store)
    res = resolver.resolve(query, entity_type=entity_type)

    p = Panel.fit(
        f"[bold]Query:[/bold] {query}\n"
        f"[bold]Stage:[/bold] {res.stage.value}\n"
        f"[bold]Canonical ID:[/bold] {res.canonical_id or '—'}\n"
        f"[bold]Confidence:[/bold] {res.confidence:.2f}\n"
        f"[bold]Ambiguous:[/bold] {res.is_ambiguous}\n"
        f"[bold]Is new:[/bold] {res.is_new}\n"
        f"[bold]Notes:[/bold] {res.notes or '—'}",
        title="resolve()",
    )
    console.print(p)

    if res.canonical_id:
        entity = store.get_entity(res.canonical_id)
        if entity:
            console.print(f"\n[green]Matched entity:[/green] {entity.canonical_name} ({entity.entity_type})")

    if res.candidates:
        t = Table(title="Top candidates")
        t.add_column("Canonical ID")
        t.add_column("Score", justify="right")
        t.add_column("Via")
        t.add_column("Matched text")
        for c in res.candidates[:10]:
            t.add_row(c.canonical_id, f"{c.score:.2f}", c.via, c.matched_text or "—")
        console.print(t)


# ---------------------------------------------------------------------------
# entity
# ---------------------------------------------------------------------------


@cli.group()
def entity() -> None:
    """Canonical entity'leri sorgula/yönet."""


@entity.command("list")
@click.option("--type", "entity_type", default=None)
@click.option("--status", default=None)
@click.option("--limit", default=50, type=int)
@click.pass_context
def entity_list(
    ctx: click.Context,
    entity_type: Optional[str],
    status: Optional[str],
    limit: int,
) -> None:
    db_path = ctx.obj["db_path"]
    store = _open_store(db_path)
    items = store.list_entities(entity_type=entity_type, status=status, limit=limit)
    t = Table(title=f"Entities (n={len(items)})")
    t.add_column("Canonical ID")
    t.add_column("Name")
    t.add_column("Type")
    t.add_column("Status")
    t.add_column("Source")
    for e in items:
        t.add_row(e.canonical_id, e.canonical_name[:60], e.entity_type, e.status, e.source)
    console.print(t)


@entity.command("show")
@click.argument("canonical_id")
@click.pass_context
def entity_show(ctx: click.Context, canonical_id: str) -> None:
    db_path = ctx.obj["db_path"]
    store = _open_store(db_path)
    e = store.get_entity(canonical_id)
    if not e:
        console.print(f"[red]Not found:[/red] {canonical_id}")
        sys.exit(1)
    aliases = store.get_aliases_for(canonical_id)
    console.print(Panel.fit(
        f"[bold cyan]{e.canonical_name}[/bold cyan]\n"
        f"ID: {e.canonical_id}\n"
        f"Type: {e.entity_type}\n"
        f"Status: {e.status}\n"
        f"Source: {e.source}\n"
        f"norm_strict: {e.norm_strict}\n"
        f"norm_loose:  {e.norm_loose}\n"
        f"metadata: {json.dumps(e.metadata, ensure_ascii=False)}",
        title="Entity",
    ))
    t = Table(title=f"Aliases (n={len(aliases)})")
    t.add_column("Surface form")
    t.add_column("norm_strict")
    t.add_column("Source")
    t.add_column("Confidence", justify="right")
    t.add_column("Doc")
    for a in aliases:
        t.add_row(
            a.surface_form[:60],
            a.norm_strict[:40],
            a.source,
            f"{a.confidence:.2f}",
            a.source_doc_id or "—",
        )
    console.print(t)


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------


@cli.group()
def review() -> None:
    """Pending entities + ambiguous resolutions için review."""


@review.command("list")
@click.option("--status", default="pending", type=click.Choice(["pending", "approved", "rejected"]))
@click.option("--kind", default=None, type=click.Choice([k.value for k in PendingKind]))
@click.option("--limit", default=50, type=int)
@click.pass_context
def review_list(ctx: click.Context, status: str, kind: Optional[str], limit: int) -> None:
    db_path = ctx.obj["db_path"]
    store = _open_store(db_path)
    queue = ReviewQueue(store)
    items = queue.list(status=PendingStatus(status), kind=kind, limit=limit)

    t = Table(title=f"Pending items — status={status} (n={len(items)})")
    t.add_column("ID", justify="right")
    t.add_column("Kind")
    t.add_column("Surface")
    t.add_column("Type guess")
    t.add_column("Doc")
    t.add_column("Created")
    for it in items:
        t.add_row(
            str(it.id),
            it.kind,
            it.surface[:50],
            it.entity_type_guess or "—",
            it.source_doc_id or "—",
            it.created_at or "—",
        )
    console.print(t)


@review.command("approve")
@click.argument("item_id", type=int)
@click.option("--canonical-name", default=None, help="Yeni canonical adı (boşsa surface kullanılır)")
@click.option("--type", "entity_type", default=None, help="company | person | organization | ...")
@click.option("--seed-aliases", "seed_aliases", multiple=True, help="Ek seed alias (tekrarlanabilir)")
@click.pass_context
def review_approve(
    ctx: click.Context,
    item_id: int,
    canonical_name: Optional[str],
    entity_type: Optional[str],
    seed_aliases: tuple[str, ...],
) -> None:
    """Pending item'ı yeni canonical entity olarak onayla."""
    db_path = ctx.obj["db_path"]
    store = _open_store(db_path)
    queue = ReviewQueue(store)
    cid = queue.approve_as_new_entity(
        item_id,
        canonical_name=canonical_name,
        entity_type=entity_type,
        seed_aliases=list(seed_aliases) if seed_aliases else None,
    )
    console.print(f"[green]Created canonical:[/green] {cid}")


@review.command("link")
@click.argument("item_id", type=int)
@click.option("--canonical", "canonical_id", required=True, help="Mevcut canonical_id")
@click.pass_context
def review_link(ctx: click.Context, item_id: int, canonical_id: str) -> None:
    """Pending item'ı mevcut bir canonical entity'ye alias olarak bağla."""
    db_path = ctx.obj["db_path"]
    store = _open_store(db_path)
    queue = ReviewQueue(store)
    queue.approve_link_to_existing(item_id, canonical_id=canonical_id)
    console.print(f"[green]Linked[/green] item {item_id} → {canonical_id}")


@review.command("reject")
@click.argument("item_id", type=int)
@click.option("--reason", default="", help="Reddetme nedeni")
@click.pass_context
def review_reject(ctx: click.Context, item_id: int, reason: str) -> None:
    db_path = ctx.obj["db_path"]
    store = _open_store(db_path)
    queue = ReviewQueue(store)
    queue.reject(item_id, reason=reason)
    console.print(f"[yellow]Rejected[/yellow] item {item_id}")


# ---------------------------------------------------------------------------
# cache (Phase 3)
# ---------------------------------------------------------------------------


@cli.group()
def cache() -> None:
    """SHA-256 ingest cache yonetimi (Phase 3 — graphify-adopted)."""


@cache.command("clear")
@click.option("--doc-id", default=None, help="Sadece bu doc_id'nin cache'ini sil (verilmezse hepsi)")
@click.pass_context
def cache_clear(ctx: click.Context, doc_id: Optional[str]) -> None:
    """Ingest cache'i temizle — silinen satir sayisini yazdirir."""
    db_path = ctx.obj["db_path"]
    store = _open_store(db_path)
    n = store.clear_ingest_cache(doc_id=doc_id)
    if doc_id:
        console.print(f"[yellow]Cleared[/yellow] {n} cache row(s) for doc_id={doc_id}")
    else:
        console.print(f"[yellow]Cleared[/yellow] {n} cache row(s) (all)")


@cache.command("stats")
@click.pass_context
def cache_stats(ctx: click.Context) -> None:
    """Cache içeriği özeti (kac belge, kac extractor_version, son ingest)."""
    db_path = ctx.obj["db_path"]
    store = _open_store(db_path)
    rows = store._connect().execute(
        """
        SELECT
            COUNT(*) AS rows_total,
            COUNT(DISTINCT doc_id) AS docs,
            COUNT(DISTINCT extractor_version) AS extractor_versions,
            MAX(ingested_at) AS last_ingested
        FROM ingest_cache
        """,
    ).fetchone()
    by_ver = store._connect().execute(
        """
        SELECT extractor_version, COUNT(*) AS n
        FROM ingest_cache
        GROUP BY extractor_version
        ORDER BY n DESC
        """,
    ).fetchall()

    t = Table(title="Ingest cache stats")
    t.add_column("Metric", style="bold")
    t.add_column("Value", style="cyan")
    t.add_row("Cache rows total", str(rows["rows_total"]))
    t.add_row("Distinct doc_ids", str(rows["docs"]))
    t.add_row("Distinct extractor versions", str(rows["extractor_versions"]))
    t.add_row("Last ingested at", str(rows["last_ingested"] or "—"))
    for r in by_ver:
        t.add_row(f"  ver: {r['extractor_version']}", str(r["n"]))
    console.print(t)


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


@cli.command()
@click.pass_context
def stats(ctx: click.Context) -> None:
    """KB istatistiklerini göster."""
    db_path = ctx.obj["db_path"]
    store = _open_store(db_path)
    queue = ReviewQueue(store)
    s = store.stats()
    rs = queue.stats()

    t = Table(title="kb-overlay stats")
    t.add_column("Metric", style="bold")
    t.add_column("Value", style="cyan")
    t.add_row("Entities total", str(s["entities_total"]))
    t.add_row("  verified", str(s["entities_verified"]))
    t.add_row("  pending", str(s["entities_pending"]))
    t.add_row("  auto_added", str(s["entities_auto_added"]))
    t.add_row("Aliases total", str(s["aliases_total"]))
    t.add_row("Documents", str(s["documents_processed"]))
    for etype, n in s["by_type"].items():
        t.add_row(f"  type {etype}", str(n))
    for src, n in s["aliases_by_source"].items():
        t.add_row(f"  alias source {src}", str(n))
    for status, n in rs["by_status"].items():
        t.add_row(f"  pending {status}", str(n))
    console.print(t)


# ---------------------------------------------------------------------------
# bootstrap (seed CSV → SQLite + opsiyonel Neo4j)
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--csv", "csv_path", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--status", default="verified", type=click.Choice(["verified", "pending", "auto_added"]))
@click.option("--no-strip-suffix", is_flag=True, help="Sirket suffix'siz varyant ekleme")
@click.option("--delimiter", default=",", show_default=True)
@click.option("--encoding", default="utf-8", show_default=True)
@click.option("--neo4j", is_flag=True, help="SQLite'tan Neo4j'ye senkronize et")
@click.option("--embed", is_flag=True, help="Neo4j sync sırasında embedding hesapla")
@click.pass_context
def bootstrap(
    ctx: click.Context,
    csv_path: str,
    status: str,
    no_strip_suffix: bool,
    delimiter: str,
    encoding: str,
    neo4j: bool,
    embed: bool,
) -> None:
    """Seed CSV'den canonical entity'leri SQLite (+opsiyonel Neo4j) ekler."""
    from pathlib import Path as _P
    from ..dictionary import EntityStatus
    # scripts.bootstrap_kb modülü zaten her şeyi yapıyor; doğrudan çağır
    import importlib.util
    import sys as _sys

    script_path = _P(__file__).resolve().parents[3] / "scripts" / "bootstrap_kb.py"
    spec = importlib.util.spec_from_file_location("kbo_bootstrap_script", script_path)
    if spec is None or spec.loader is None:  # pragma: no cover
        console.print(f"[red]bootstrap_kb.py bulunamadi:[/red] {script_path}")
        _sys.exit(1)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    db_path = ctx.obj["db_path"]
    _P(db_path).parent.mkdir(parents=True, exist_ok=True)
    store = _open_store(db_path)

    counts = mod.bootstrap_from_csv(
        _P(csv_path),
        store=store,
        status=EntityStatus(status),
        auto_strip_suffix=not no_strip_suffix,
        delimiter=delimiter,
        encoding=encoding,
    )
    console.print(Panel.fit(
        f"[green]SQLite seed completed[/green]\n"
        f"rows={counts['rows']}  entities={counts['entities_created']}  "
        f"aliases={counts['aliases_seeded']}  skipped={counts['skipped']}",
        title="bootstrap",
    ))

    if neo4j:
        import os as _os
        sync = mod.maybe_sync_neo4j(
            store,
            uri=_os.environ.get("KBO_NEO4J_URI", "bolt://localhost:7687"),
            user=_os.environ.get("KBO_NEO4J_USER", "neo4j"),
            password=_os.environ.get("KBO_NEO4J_PASS", "neo4j"),
            database=_os.environ.get("KBO_NEO4J_DB", "neo4j"),
            embed=embed,
        )
        console.print(Panel.fit(
            f"[green]Neo4j sync completed[/green]\n"
            f"synced={sync['synced']}  with_embedding={sync['with_embedding']}  "
            f"with_aliases={sync['with_aliases']}",
            title="neo4j",
        ))


# ---------------------------------------------------------------------------
# neo4j sync / sorgu
# ---------------------------------------------------------------------------


@cli.group()
def neo4j() -> None:
    """Neo4j graph komutlari."""


def _neo4j_config_from_env() -> "object":
    import os as _os
    from ..neo4j_store import Neo4jConfig
    return Neo4jConfig(
        uri=_os.environ.get("KBO_NEO4J_URI", "bolt://localhost:7687"),
        user=_os.environ.get("KBO_NEO4J_USER", "neo4j"),
        password=_os.environ.get("KBO_NEO4J_PASS", "neo4j"),
        database=_os.environ.get("KBO_NEO4J_DB", "neo4j"),
    )


@neo4j.command("init")
def neo4j_init() -> None:
    """Neo4j sema (constraint + index) uygula."""
    from ..neo4j_store import Neo4jStore
    cfg = _neo4j_config_from_env()
    with Neo4jStore(cfg) as graph:
        graph.init_schema()
    console.print(f"[green]Schema applied[/green] {cfg.uri} db={cfg.database}")


@neo4j.command("sync")
@click.option("--embed", is_flag=True, help="Embedding hesapla")
@click.option("--status", default=None, help="sadece bu status (verified/pending/...)")
@click.pass_context
def neo4j_sync(ctx: click.Context, embed: bool, status: Optional[str]) -> None:
    """SQLite'taki tum canonical'lari Neo4j'ye yansit."""
    db_path = ctx.obj["db_path"]
    store = _open_store(db_path)

    from ..neo4j_store import Neo4jStore
    cfg = _neo4j_config_from_env()

    embed_fn = None
    if embed:
        try:
            from sentence_transformers import SentenceTransformer
            import os as _os
            model_name = _os.environ.get(
                "KBO_EMBED_MODEL",
                "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
            )
            console.print(f"[cyan]Loading embedding model:[/cyan] {model_name}")
            model = SentenceTransformer(model_name)
            cfg.vector_dim = int(model.get_sentence_embedding_dimension())

            def embed_fn(text: str) -> list[float]:
                vec = model.encode(text, normalize_embeddings=True)
                return [float(x) for x in vec]
        except ImportError:
            console.print("[yellow]sentence-transformers yok, embedding atlandi[/yellow]")

    with Neo4jStore(cfg) as graph:
        graph.init_schema()
        out = graph.sync_from_alias_store(store, embed_fn=embed_fn, only_status=status)
    console.print(f"[green]Sync done:[/green] {out}")


# ---------------------------------------------------------------------------
# extract-relations (LLM)
# ---------------------------------------------------------------------------


@cli.command("extract-relations")
@click.option("--file", "file_path", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--doc-id", "doc_id", required=True)
@click.option("--title", default=None)
@click.option("--ner", "ner_name", default="naive", type=click.Choice(["naive", "spacy", "gliner"]))
@click.option(
    "--llm",
    "llm_name",
    default="stub",
    type=click.Choice(["stub", "openai", "ollama"]),
    help="Iliski cikarimi icin LLM provider",
)
@click.option("--llm-model", default=None, help="LLM model adi (provider'a gore)")
@click.option("--no-neo4j", is_flag=True, help="Neo4j'ye yazma; sadece JSON dondur")
@click.option("--encoding", default="utf-8")
@click.option(
    "--force",
    is_flag=True,
    help="Content-hash cache'i bypass et — aynı dosya/extractor/llm olsa bile yeniden işle",
)
@click.option(
    "--no-cache",
    is_flag=True,
    help="Cache'i ne oku ne de yaz",
)
@click.pass_context
def extract_relations(
    ctx: click.Context,
    file_path: str,
    doc_id: str,
    title: Optional[str],
    ner_name: str,
    llm_name: str,
    llm_model: Optional[str],
    no_neo4j: bool,
    encoding: str,
    force: bool,
    no_cache: bool,
) -> None:
    """Belgeyi ingest et + LLM ile iliski cikar + Neo4j'ye yaz.

    Phase 3: SHA-256 content-hash cache. Aynı dosya + aynı NER + aynı LLM
    konfigürasyonu daha önce işlendiyse LLM ÇAĞRISI ATLANIR. Cache key:
    ``(doc_id, sha256(content)[:16], extractor_version)``.
    extractor_version değişimi (NER/LLM güncellemesi) cache'i invalidate eder.
    """
    db_path = ctx.obj["db_path"]
    store = _open_store(db_path)
    review_q = ReviewQueue(store)

    text = Path(file_path).read_text(encoding=encoding)
    console.print(f"[cyan]Ingesting[/cyan] {file_path} ({len(text)} chars) -> doc_id={doc_id}")

    # Cache lookup — LLM çağrısından ÖNCE
    content_hash = _compute_content_hash(text)
    extractor_version = _build_extractor_version(
        ner_name=ner_name,
        llm_provider=llm_name,
        llm_model=llm_model,
    )
    if not no_cache and not force:
        cached = store.has_ingest_cache(
            doc_id=doc_id,
            content_hash=content_hash,
            extractor_version=extractor_version,
        )
        if cached is not None:
            console.print(
                Panel.fit(
                    f"[green]Cache hit[/green] — daha önce işlenmiş.\n"
                    f"  ingested_at:     {cached['ingested_at']}\n"
                    f"  mentions_count:  {cached['mentions_count']}\n"
                    f"  relations_count: {cached['relations_count']}\n"
                    f"  extractor:       {extractor_version}\n"
                    f"  content_hash:    {content_hash}\n\n"
                    f"[dim]LLM çağrısı atlandı. Yeniden işlemek için: --force[/dim]",
                    title="extract-relations cache",
                )
            )
            return

    extractor = _build_extractor(store, ner_name=ner_name, review_queue=review_q)

    # LLM provider
    from ..relations import (
        OllamaProvider, OpenAIProvider, RelationExtractor, RelationPipeline, StubProvider,
    )
    if llm_name == "openai":
        provider = OpenAIProvider(model=llm_model or "gpt-4o-mini")
    elif llm_name == "ollama":
        provider = OllamaProvider(model=llm_model or "qwen2.5:3b-instruct")
    else:
        provider = StubProvider()
    rel_ex = RelationExtractor(provider=provider)

    # Neo4j (opsiyonel)
    graph = None
    if not no_neo4j:
        try:
            from ..neo4j_store import Neo4jStore
            graph = Neo4jStore(_neo4j_config_from_env())
            graph.init_schema()
        except Exception as exc:
            console.print(f"[yellow]Neo4j atlandi:[/yellow] {exc}")
            graph = None

    pipeline = RelationPipeline(
        document_extractor=extractor,
        relation_extractor=rel_ex,
        neo4j_store=graph,
    )

    # text zaten yukarıda okundu (cache lookup için); pipeline'a o değişkeni geçir
    result = pipeline.ingest(text, doc_id=doc_id, title=title)

    # Cache write — başarılı ingest sonrası
    if not no_cache:
        store.save_ingest_cache(
            doc_id=doc_id,
            content_hash=content_hash,
            extractor_version=extractor_version,
            relations_count=len(result.relations.relations),
            mentions_count=len(result.extraction.mentions),
        )

    t = Table(title=f"Ingestion summary - {doc_id}")
    t.add_column("Metric", style="bold")
    t.add_column("Value", style="cyan")
    t.add_row("Mentions extracted", str(len(result.extraction.mentions)))
    t.add_row("Mentions linked to graph", str(result.mentions_linked))
    t.add_row("Entities written", str(result.nodes_written))
    t.add_row("Relations extracted (LLM)", str(len(result.relations.relations)))
    t.add_row("Relations written", str(result.relations_written))
    if result.discarded_relations:
        t.add_row("Relations discarded (schema)", str(result.discarded_relations))
    if result.validation_recovered:
        t.add_row("Validation recovered", "yes")
    if result.errors:
        t.add_row("Errors", str(len(result.errors)))
    console.print(t)

    if result.relations.relations:
        rt = Table(title="Top relations")
        rt.add_column("Subj")
        rt.add_column("Predicate")
        rt.add_column("Obj")
        rt.add_column("Conf", justify="right")
        rt.add_column("Evidence")
        for r in result.relations.relations[:20]:
            rt.add_row(
                r.subject_id,
                r.predicate,
                r.object_id,
                f"{r.confidence:.2f}",
                (r.evidence_text or "")[:60],
            )
        console.print(rt)

    if graph is not None:
        graph.close()


# ---------------------------------------------------------------------------
# ask (agent: query → resolve → cypher → run)
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("question")
@click.option("--template", default=None, help="Cypher sablon adi (otomatik secimi atla)")
@click.option("--limit", default=25, type=int)
@click.option("--no-run", is_flag=True, help="Cypher'i Neo4j'de calistirma, sadece goster")
@click.option("--ner", "ner_name", default="naive", type=click.Choice(["naive", "spacy", "gliner"]))
@click.pass_context
def ask(
    ctx: click.Context,
    question: str,
    template: Optional[str],
    limit: int,
    no_run: bool,
    ner_name: str,
) -> None:
    """Sorudaki entity'leri canonical_id'ye coz, sablon Cypher ile Neo4j'ye sor."""
    db_path = ctx.obj["db_path"]
    store = _open_store(db_path)

    from ..agent import (
        QueryResolver, build_system_prompt, list_template_names, render_template,
    )
    from ..discovery import get_ner_backend

    qr = QueryResolver(store, ner_backend=get_ner_backend(ner_name))
    resolved = qr.resolve(question)

    console.print(Panel.fit(
        f"[bold]Soru:[/bold] {question}\n\n"
        f"[bold]Cozulmus tablo:[/bold]\n{resolved.to_prompt_block()}",
        title="QueryResolver",
    ))

    if resolved.has_unresolved():
        console.print(
            "[yellow]Bazi entity'ler cozulemedi (unknown/ambiguous). "
            "Cypher uretmiyorum; oncelikle KB'yi besle veya disambiguate et.[/yellow]"
        )

    matched = [e for e in resolved.entities if e.canonical_id]
    if not matched:
        console.print("[red]Cozulmus entity yok. Cikis.[/red]")
        return

    if not template:
        # Basit heuristik secim
        q_lower = question.lower()
        if any(kw in q_lower for kw in ("ceo", "ceo'su", "genel mudur", "general manager")):
            template = "ceo_of_company"
        elif any(kw in q_lower for kw in ("calisan", "personel", "employee")):
            template = "company_employees"
        elif any(kw in q_lower for kw in ("yan kurulus", "subsidiary")):
            template = "subsidiaries_of"
        elif any(kw in q_lower for kw in ("hangi belge", "documents", "belgelerde")):
            template = "documents_mentioning"
        else:
            template = "entity_relations_outgoing"

    target = matched[0]
    cypher, params = render_template(
        template,
        {"canonical_id": target.canonical_id},
        default_limit=limit,
    )

    console.print(Panel.fit(
        f"[bold]Template:[/bold] {template}\n"
        f"[bold]Params:[/bold] {params}\n\n"
        f"[bold]Cypher:[/bold]\n{cypher.strip()}",
        title="Generated query",
    ))

    if no_run:
        return

    try:
        from ..neo4j_store import Neo4jStore
        with Neo4jStore(_neo4j_config_from_env()) as graph:
            rows = graph.execute_cypher(cypher, params)
    except Exception as exc:
        console.print(f"[red]Neo4j calistirma hatasi:[/red] {exc}")
        return

    if not rows:
        console.print("[yellow]Sonuc bulunamadi.[/yellow]")
        return

    rt = Table(title=f"Results (n={len(rows)})")
    cols = list(rows[0].keys())
    for c in cols:
        rt.add_column(c)
    for r in rows[:50]:
        rt.add_row(*[str(r.get(c, ""))[:80] for c in cols])
    console.print(rt)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """pyproject.toml'dan referans alınan entry point."""
    cli(obj={})


if __name__ == "__main__":
    main()
