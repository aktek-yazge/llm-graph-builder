"""
Wiki Tools
==========

Self-Evolving Agent'in wiki sayfalarini yonetmek icin kullandigi tool'lar.
Obsidian-style interlinked markdown sayfalar olusturur, gunceller, arar.

Lint, traverse ve log tool'lari dahil.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from ..wiki_store import WikiStore

logger = logging.getLogger(__name__)


def create_wiki_tools(agent_id: str, wiki: WikiStore) -> list:
    """Wiki yonetimi icin LangChain tool'lari olustur."""

    @tool
    async def create_wiki_page(path: str, content: str) -> str:
        """Wiki sayfasi olustur veya guncelle.

        Sayfalar [[wikilink]] ile birbirine baglanabilir.
        Sayfa yolu kategoriyi belirler.

        Args:
            path: Sayfa yolu. Ornekler:
                  'entities/Sirket' - entity tanimi
                  'relationships/ORTAGI' - iliski tanimi
                  'patterns/unvan-normalize' - ogrenilen pattern
                  'sources/batch-3-summary' - kaynak ozeti
                  'analysis/quality-report' - analiz
            content: Markdown icerik. [[HedefSayfa]] ile cross-reference kurulur.
        """
        result = await wiki.create_page(agent_id, path, content)
        return f"Wiki sayfasi olusturuldu: {path} (v{result['version']})"

    @tool
    async def update_wiki_page(path: str, content: str) -> str:
        """Mevcut wiki sayfasini guncelle (yeni versiyon olusur).

        Args:
            path: Guncellenecek sayfa yolu (orn: 'entities/Sirket')
            content: Yeni icerik (tum sayfa icerigini ver, parcali degil)
        """
        existing = await wiki.get_page(agent_id, path)
        if not existing:
            return f"Sayfa bulunamadi: {path}. create_wiki_page ile olustur."
        result = await wiki.update_page(agent_id, path, content)
        return f"Wiki sayfasi guncellendi: {path} (v{result['version']})"

    @tool
    async def get_wiki_page(path: str) -> str:
        """Wiki sayfasini oku. Backlinks de gosterilir.

        Args:
            path: Sayfa yolu (orn: 'entities/Sirket')
        """
        page = await wiki.get_page(agent_id, path)
        if not page:
            return f"Sayfa bulunamadi: {path}"
        backlinks = await wiki.get_backlinks(agent_id, path)
        result = page["content"]
        if backlinks:
            result += f"\n\n---\nBacklinks: {', '.join(backlinks)}"
        return result

    @tool
    async def search_wiki(query: str) -> str:
        """Wiki sayfalarinda full-text arama yap.

        PostgreSQL ts_vector ile arar, bulamazsa ILIKE'a duser.

        Args:
            query: Aranacak metin
        """
        results = await wiki.search(agent_id, query)
        if not results:
            return f"'{query}' icin sonuc bulunamadi."
        lines = [f"'{query}' icin {len(results)} sonuc:"]
        for r in results[:10]:
            summary = r["content"][:100].replace("\n", " ")
            lines.append(f"- [{r['path']}]: {summary}...")
        return "\n".join(lines)

    @tool
    async def get_wiki_index() -> str:
        """Tum wiki sayfalarinin listesini getir.

        Her sayfa icin backlink sayisi (refs) gosterilir.
        Obsidian graph view'daki node buyuklugu gibi dusun:
        ne kadar cok referans edilirse o kadar onemli.
        """
        return await wiki.get_index(agent_id)

    @tool
    async def add_learned_pattern(
        pattern_name: str,
        description: str,
        examples: str = "",
        related_entities: str = "",
    ) -> str:
        """Ogrenilen bir pattern'i wiki sayfasi olarak kaydet.

        Extraction sirasinda kesfedilen kurallar, normalizasyon mantiklari,
        edge case'ler icin kullanilir. Bu bilgi gelecekteki extraction'larda
        LLM'e rehber olarak verilir.

        Args:
            pattern_name: Pattern adi (orn: 'unvan-normalization', 'tarih-formati')
            description: Pattern'in aciklamasi
            examples: Ornek durumlar (opsiyonel)
            related_entities: Ilgili entity isimleri virgullu (orn: 'Sirket,GercekKisi')
        """
        entities = [e.strip() for e in related_entities.split(",") if e.strip()] if related_entities else []
        result = await wiki.add_pattern_page(
            agent_id, pattern_name, description,
            examples=examples,
            related_entities=entities,
            source="conversation",
        )
        return f"Pattern sayfasi olusturuldu: patterns/{pattern_name} (v{result['version']})"

    @tool
    async def lint_wiki() -> str:
        """Wiki saglik kontrolu calistir (Karpathy-style lint).

        Kontrol eder:
        - Orphan sayfalar (hicbir sayfa tarafindan referans edilmeyen)
        - Kirik linkler ([[HedefSayfa]] ama HedefSayfa yok)
        - Unresolved hedefler (olusturulmamis sayfalar)

        Index sayfasini da yeniden olusturur.
        """
        result = await wiki.lint(agent_id)

        lines = [f"Wiki Lint Raporu ({result['total_pages']} sayfa, {result['total_links']} link)"]
        lines.append(f"Toplam sorun: {result['issues_count']}")
        lines.append("")

        if result["orphan_pages"]:
            lines.append(f"## Orphan Sayfalar ({len(result['orphan_pages'])})")
            lines.append("Hicbir sayfa tarafindan referans edilmiyor:")
            for p in result["orphan_pages"]:
                lines.append(f"  - {p}")
            lines.append("")

        if result["broken_links"]:
            lines.append(f"## Kirik Linkler ({len(result['broken_links'])})")
            for bl in result["broken_links"]:
                lines.append(f"  - {bl['source']} -> [[{bl['target']}]] (yok)")
            lines.append("")

        if result["unresolved_targets"]:
            lines.append(f"## Olusturulmamis Hedefler ({len(result['unresolved_targets'])})")
            lines.append("Bu sayfalar henuz olusturulmamis:")
            for t in result["unresolved_targets"]:
                lines.append(f"  - [[{t}]]")
            lines.append("")

        if result["issues_count"] == 0:
            lines.append("Tum linkler gecerli, orphan sayfa yok. Wiki saglikli.")

        lines.append("Index sayfasi yeniden olusturuldu.")
        return "\n".join(lines)

    @tool
    async def traverse_wiki(start_path: str, depth: int = 2) -> str:
        """Bir sayfadan baslayip wikilink'ler uzerinden yuru.

        Obsidian graph view gibi: baslangic noktasindan N seviye
        derinlikteki tum bagli sayfalari gosterir.

        Args:
            start_path: Baslangic sayfasi (orn: 'entities/Sirket')
            depth: Kac seviye derinlige inilecegi (varsayilan: 2)
        """
        if depth > 4:
            depth = 4

        reachable = await wiki.traverse(agent_id, start_path, depth=depth)
        if not reachable:
            return f"'{start_path}' sayfasi bulunamadi veya hicbir baglantisi yok."

        lines = [f"'{start_path}' sayfasindan {depth} seviye derinlikte {len(reachable)} sayfa:"]
        lines.append("")
        for path, page in reachable.items():
            link_targets = page.get("links", [])
            link_str = ", ".join(f"[[{l}]]" for l in link_targets[:5])
            if len(link_targets) > 5:
                link_str += f" (+{len(link_targets) - 5} more)"
            summary = _first_line_summary(page.get("content", ""))
            lines.append(f"- **{path}**: {summary}")
            if link_str:
                lines.append(f"  Links: {link_str}")
        return "\n".join(lines)

    @tool
    async def get_wiki_log(limit: int = 20) -> str:
        """Son wiki degisikliklerini goster (kronolojik log).

        Args:
            limit: Kac kayit gosterilecegi (varsayilan: 20)
        """
        if limit > 100:
            limit = 100
        entries = await wiki.get_log(agent_id, limit=limit)
        if not entries:
            return "Henuz wiki degisikligi yok."
        lines = [f"Son {len(entries)} wiki degisikligi:"]
        for e in entries:
            lines.append(f"- [{e['timestamp']}] {e['action']} | {e['path']}")
        return "\n".join(lines)

    return [
        create_wiki_page,
        update_wiki_page,
        get_wiki_page,
        search_wiki,
        get_wiki_index,
        add_learned_pattern,
        lint_wiki,
        traverse_wiki,
        get_wiki_log,
    ]


def _first_line_summary(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:100]
    return ""
