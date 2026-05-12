"""Synthetic distillation için OCR belge envanteri ve paragraph chunking.

Strateji (OCR yetersiz fallback — bkz. user rules "Karşı senaryo 1"):
- Mevcut benzersiz Türkçe Ticaret Sicili sayfaları sayıca çok az (~5 sayfa).
- Aksa-09.03.2016-9028 belgesini sayfa bazında SPLIT EDİYORUZ:
    * page_001 + page_002 -> training corpus (kritik test entity'lerinden hiçbiri burada DEĞİL)
    * page_003 -> SADECE benchmark (Ata, İZBAŞ, MENDERES, Kreston burada)
- Aksa-18.01.2016-8991 ve Aksa-28.07.2016-9125 -> training corpus (benzersiz, test'te kullanılmıyor)
- Her sayfa metni paragraf-bazında chunk'lara bölünür (200-1500 char), GLiNER eğitimi için
  daha iyi token-uzunluğunda örnek üretmek üzere.

Çıktılar:
    data/synthetic_distill/corpus_inventory.json
    data/synthetic_distill/chunks/{doc_id}_p{NNN}_c{NN}.txt
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_CELERY = (
    REPO_ROOT.parent / "celery_worker" / "output_celery"
)

CHUNK_TARGET_CHARS = 1200
CHUNK_MIN_CHARS = 250
CHUNK_MAX_CHARS = 2500


def _strip_page_marker(text: str) -> str:
    return re.sub(r"\[\[PAGE:\d+\]\]\s*", "", text).strip()


def _normalize_whitespace(text: str) -> str:
    """Tüm whitespace'i tek boşluğa çevir (OCR satır kaymalarını sil).

    Bu, Gemini cevabındaki düzleştirilmiş entity text'lerinin orijinal metinde
    `text.find()` ile bulunabilmesini garanti eder. GLiNER'ın WordsSplitter'ı
    da whitespace-tabanlı tokenize ettiği için fark yaratmaz.
    """
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _split_paragraphs(text: str) -> list[str]:
    """Önce paragraflara böl (boş satırlara göre), sonra her paragrafı düzleştir.

    Tek-satır paragraflar arasında \\n\\n yapısı korunur ki chunker'da paragraf
    bütünlüğü çiğnenmesin.
    """
    text = text.replace("\u00a0", " ")
    paragraphs = re.split(r"\n\s*\n", text)
    flat = [_normalize_whitespace(p) for p in paragraphs]
    return [p for p in flat if p]


def _split_into_chunks(text: str) -> list[str]:
    """Paragraf-bazında chunk'la, target ~1200 char, min 250, max 2500.

    OCR sayfalarında paragraflar bazen çok kısa, bazen çok uzun. Komşu paragrafları
    birleştirerek hedefe yakın chunk'lar üretiyoruz.
    """
    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        flat = _normalize_whitespace(text)
        return [flat] if len(flat) >= CHUNK_MIN_CHARS else []

    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0

    for para in paragraphs:
        # Çok uzun paragrafı ortadan bölmeye çalışma — duraklara göre kes
        if len(para) > CHUNK_MAX_CHARS:
            if buf:
                chunks.append("\n\n".join(buf))
                buf, buf_len = [], 0
            sentences = re.split(r"(?<=[.!?])\s+", para)
            sub_buf: list[str] = []
            sub_len = 0
            for s in sentences:
                if sub_len + len(s) > CHUNK_TARGET_CHARS and sub_buf:
                    chunks.append(" ".join(sub_buf))
                    sub_buf, sub_len = [], 0
                sub_buf.append(s)
                sub_len += len(s) + 1
            if sub_buf:
                chunks.append(" ".join(sub_buf))
            continue

        if buf_len + len(para) + 2 > CHUNK_TARGET_CHARS and buf_len >= CHUNK_MIN_CHARS:
            chunks.append("\n\n".join(buf))
            buf, buf_len = [para], len(para)
        else:
            buf.append(para)
            buf_len += len(para) + 2

    if buf:
        chunks.append("\n\n".join(buf))

    # Filtre: çok kısa son chunk'ı önceki ile birleştir (varsa)
    if len(chunks) >= 2 and len(chunks[-1]) < CHUNK_MIN_CHARS:
        chunks[-2] = chunks[-2] + "\n\n" + chunks[-1]
        chunks.pop()

    # min char altı tek chunk varsa yine de tut (en az 1 chunk üret)
    return [c for c in chunks if len(c) >= 50]


def _read_pages(path: Path) -> list[tuple[int, str]]:
    """Klasördeki ocr_pages/page_NNN_ocr.md dosyalarını oku."""
    if path.is_dir() and (path / "ocr_pages").is_dir():
        files = sorted((path / "ocr_pages").glob("page_*_ocr.md"))
        out: list[tuple[int, str]] = []
        for f in files:
            page_num_match = re.search(r"page_(\d+)", f.name)
            if not page_num_match:
                continue
            text = _strip_page_marker(f.read_text(encoding="utf-8"))
            out.append((int(page_num_match.group(1)), text))
        return out
    return []


def _read_single_md(path: Path, page_num: int = 1) -> list[tuple[int, str]]:
    """Tek md dosyasını tek sayfa olarak oku."""
    if path.is_file():
        text = _strip_page_marker(path.read_text(encoding="utf-8"))
        if text:
            return [(page_num, text)]
    return []


def main() -> int:
    if not OUTPUT_CELERY.is_dir():
        print(f"Celery output bulunamadı: {OUTPUT_CELERY}", file=sys.stderr)
        return 2

    chunks_dir = REPO_ROOT / "data" / "synthetic_distill" / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    # Belge tanımları (benzersiz Türkçe Ticaret Sicili OCR'ları)
    docs: list[dict] = []

    aksa_09_dir = OUTPUT_CELERY / "Aksa-09.03.2016-9028-GENEL KURUL TOPLANTIYA ÇAĞIRI"
    aksa_09_pages = _read_pages(aksa_09_dir)
    docs.append({
        "doc_id": "aksa-09",
        "company": "AKSA AKRİLİK KİMYA SANAYİİ A.Ş.",
        "title": "Aksa-09.03.2016-9028 (Genel Kurul Çağrı)",
        "raw_pages": aksa_09_pages,
        # page_001 + page_002 train, page_003 yalnız test (kritik entity'leri burada)
        "train_pages": [p for p, _ in aksa_09_pages if p in (1, 2)],
        "test_pages": [3],
    })

    aksa_18_md = OUTPUT_CELERY / "Aksa-18.01.2016-8991-ANONİM ŞİRKET (YÖNETİM - TEMSİL VE DİĞER)" / "Aksa-18.01.2016-8991-ANONİM ŞİRKET (YÖNETİM - TEMSİL VE DİĞER).md"
    docs.append({
        "doc_id": "aksa-18",
        "company": "AKSA AKRİLİK KİMYA SANAYİİ A.Ş.",
        "title": "Aksa-18.01.2016-8991 (İmza Yetkisi)",
        "raw_pages": _read_single_md(aksa_18_md, page_num=1),
        "train_pages": [1],
        "test_pages": [],
    })

    aksa_28_md = OUTPUT_CELERY / "Aksa-28.07.2016-9125-DURUŞMA GÜNÜ (İPTAL - BUTLAN DAVASI)" / "Aksa-28.07.2016-9125-DURUŞMA GÜNÜ (İPTAL - BUTLAN DAVASI).md"
    docs.append({
        "doc_id": "aksa-28",
        "company": "AKSA AKRİLİK KİMYA SANAYİİ A.Ş.",
        "title": "Aksa-28.07.2016-9125 (Duruşma Günü)",
        "raw_pages": _read_single_md(aksa_28_md, page_num=1),
        "train_pages": [1],
        "test_pages": [],
    })

    # Inventory + chunking
    total_chars = 0
    total_pages = 0
    total_chunks = 0
    inventory_docs: list[dict] = []

    for doc in docs:
        doc_chunks: list[dict] = []
        for page_num, page_text in doc["raw_pages"]:
            char_count = len(page_text)
            total_chars += char_count
            total_pages += 1
            for_train = page_num in doc["train_pages"]
            for_test = page_num in doc["test_pages"]
            chunks = _split_into_chunks(page_text)
            for ci, chunk_text in enumerate(chunks, start=1):
                chunk_id = f"{doc['doc_id']}_p{page_num:03d}_c{ci:02d}"
                out_path = chunks_dir / f"{chunk_id}.txt"
                out_path.write_text(chunk_text, encoding="utf-8")
                total_chunks += 1
                doc_chunks.append({
                    "chunk_id": chunk_id,
                    "page": page_num,
                    "char_count": len(chunk_text),
                    "for_training": for_train,
                    "for_test": for_test,
                    "path": str(out_path.relative_to(REPO_ROOT)),
                })
        inventory_docs.append({
            "doc_id": doc["doc_id"],
            "company": doc["company"],
            "title": doc["title"],
            "page_count": len(doc["raw_pages"]),
            "train_pages": doc["train_pages"],
            "test_pages": doc["test_pages"],
            "chunks": doc_chunks,
        })

    inventory = {
        "total_documents": len(docs),
        "total_pages": total_pages,
        "total_chars": total_chars,
        "total_chunks": total_chunks,
        "training_chunks": sum(1 for d in inventory_docs for c in d["chunks"] if c["for_training"]),
        "test_chunks": sum(1 for d in inventory_docs for c in d["chunks"] if c["for_test"]),
        "documents": inventory_docs,
        "excluded_for_test_entities_only": [
            "Aksa-09.03.2016-9028 page_003 (Ata, İZBAŞ, Menderes, Kreston bu sayfada)"
        ],
        "fallback_strategy_note": (
            "OCR pool çok kısıtlı (~5 sayfa). User rules 'Karşı senaryo 1' uyarınca "
            "Aksa-09 belgesini sayfa bazında split ettik: page_001+page_002 -> training, "
            "page_003 -> sadece test. Kritik entity'ler (Ata, İZBAŞ, MENDERES, Kreston) "
            "yalnızca page_003'te geçtiği için split lekesizdir (entity-leakage yok)."
        ),
    }

    inventory_path = REPO_ROOT / "data" / "synthetic_distill" / "corpus_inventory.json"
    inventory_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[ok] inventory yazıldı: {inventory_path}")
    print(f"  toplam belge:     {inventory['total_documents']}")
    print(f"  toplam sayfa:     {inventory['total_pages']}")
    print(f"  toplam karakter:  {inventory['total_chars']}")
    print(f"  toplam chunk:     {inventory['total_chunks']}")
    print(f"  training chunks:  {inventory['training_chunks']}")
    print(f"  test chunks:      {inventory['test_chunks']}")
    for d in inventory["documents"]:
        print(f"  - {d['doc_id']}: {len(d['chunks'])} chunk (train_pages={d['train_pages']}, test_pages={d['test_pages']})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
