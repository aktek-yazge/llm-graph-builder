"""Tests for OCR text preprocessing (`kb_overlay.ingestion.preprocess`)."""
from __future__ import annotations

from kb_overlay.ingestion import normalize_ocr_text


class TestEmptyAndTrivial:
    def test_empty(self):
        assert normalize_ocr_text("") == ""

    def test_none_safe(self):
        # Non-string falsy → returned as-is
        assert normalize_ocr_text(None) is None  # type: ignore[arg-type]

    def test_single_line(self):
        assert normalize_ocr_text("Hello world") == "Hello world"

    def test_single_line_with_trailing_newline(self):
        assert normalize_ocr_text("Hello\n") == "Hello\n"


class TestBasicMerge:
    def test_two_lines_no_terminator(self):
        # OCR satır kırılması → birleştir
        text = "Aksa Akrilik Kimya Sanayii\nAnonim Şirketi"
        assert normalize_ocr_text(text) == "Aksa Akrilik Kimya Sanayii Anonim Şirketi"

    def test_three_lines_chained(self):
        text = "şirketin\nmerkezi\nİstanbul"
        assert normalize_ocr_text(text) == "şirketin merkezi İstanbul"

    def test_extra_whitespace_collapsed(self):
        text = "abc   \n   def"
        assert normalize_ocr_text(text) == "abc def"


class TestSentenceTerminators:
    def test_period_preserved(self):
        text = "Bu bir cümle.\nBu da başka."
        assert normalize_ocr_text(text) == "Bu bir cümle.\nBu da başka."

    def test_question_preserved(self):
        text = "Soru?\nCevap"
        assert normalize_ocr_text(text) == "Soru?\nCevap"

    def test_exclamation_preserved(self):
        text = "Dur!\nGel"
        assert normalize_ocr_text(text) == "Dur!\nGel"

    def test_colon_preserved(self):
        text = "Konular:\nMadde 1"
        assert normalize_ocr_text(text) == "Konular:\nMadde 1"

    def test_semicolon_preserved(self):
        text = "abc;\ndef"
        assert normalize_ocr_text(text) == "abc;\ndef"


class TestParagraphBoundaries:
    def test_blank_line_preserved(self):
        text = "Birinci paragraf\n\nİkinci paragraf"
        assert normalize_ocr_text(text) == "Birinci paragraf\n\nİkinci paragraf"

    def test_multiple_blank_lines(self):
        text = "para1\n\n\n\npara2"
        assert normalize_ocr_text(text) == "para1\n\n\n\npara2"

    def test_blank_then_continuation(self):
        text = "satır1\nsatır2\n\nyeni\nparagraf"
        assert normalize_ocr_text(text) == "satır1 satır2\n\nyeni paragraf"


class TestHyphenation:
    def test_basic_hyphenation(self):
        text = "şir-\nket"
        assert normalize_ocr_text(text) == "şirket"

    def test_hyphenation_in_paragraph(self):
        text = "Türkiye'deki büyük şir-\nketler için"
        assert normalize_ocr_text(text) == "Türkiye'deki büyük şirketler için"

    def test_hyphen_then_capital_not_hyphenation(self):
        # "Bay-\nBayan" → büyük harfle başlıyor, kelime kırılması değil
        # (compound word veya isim listesi olabilir) — birleştir ama tire'yi
        # koru. Aslında daha güvenli yaklaşım: birleştir, tire'siz.
        # Şu an "Bay-\nBayan" → "Bay- Bayan" (tire korunur, birleşir).
        text = "Bay-\nBayan"
        result = normalize_ocr_text(text)
        # Capital ile başladığı için hyphenation değil; standart merge:
        assert result == "Bay- Bayan"

    def test_hyphen_at_end_no_letter_before(self):
        text = "abc -\ndef"
        # Tire'den önce harf değil (boşluk) → standart merge
        assert normalize_ocr_text(text) == "abc - def"


class TestMarkdownAndPageMarkers:
    def test_markdown_heading_preserved(self):
        text = "intro paragraph\n# Heading\nbody"
        assert normalize_ocr_text(text) == "intro paragraph\n# Heading\nbody"

    def test_page_marker_double_bracket(self):
        text = "Sonraki sayfa\n[[PAGE:2]]\nİçerik"
        assert normalize_ocr_text(text) == "Sonraki sayfa\n[[PAGE:2]]\nİçerik"

    def test_page_marker_single_bracket(self):
        text = "Sayfa sonu\n[PAGE:5]\nDevam"
        assert normalize_ocr_text(text) == "Sayfa sonu\n[PAGE:5]\nDevam"

    def test_code_fence_preserved(self):
        text = "Kod bloğu:\n```python\nprint('hi')\n```\nDevam"
        assert normalize_ocr_text(text) == "Kod bloğu:\n```python\nprint('hi')\n```\nDevam"

    def test_blockquote_preserved(self):
        text = "Önce\n> Alıntı\nSonra"
        assert normalize_ocr_text(text) == "Önce\n> Alıntı\nSonra"

    def test_table_pipe_preserved(self):
        text = "tablo:\n| col1 | col2 |\n| ---- | ---- |"
        assert normalize_ocr_text(text) == "tablo:\n| col1 | col2 |\n| ---- | ---- |"


class TestListMarkers:
    def test_numbered_list_dot(self):
        text = "Maddeler\n1. Birinci\n2. İkinci"
        assert normalize_ocr_text(text) == "Maddeler\n1. Birinci\n2. İkinci"

    def test_numbered_list_paren(self):
        text = "Maddeler\n1) Birinci\n2) İkinci"
        assert normalize_ocr_text(text) == "Maddeler\n1) Birinci\n2) İkinci"

    def test_bullet_dash(self):
        text = "Liste\n- alpha\n- beta"
        assert normalize_ocr_text(text) == "Liste\n- alpha\n- beta"

    def test_bullet_star(self):
        text = "Liste\n* alpha\n* beta"
        assert normalize_ocr_text(text) == "Liste\n* alpha\n* beta"

    def test_bullet_unicode(self):
        text = "Liste\n• alpha\n• beta"
        assert normalize_ocr_text(text) == "Liste\n• alpha\n• beta"

    def test_letter_list_paren(self):
        text = "Maddeler\na) ilk\nb) ikinci"
        assert normalize_ocr_text(text) == "Maddeler\na) ilk\nb) ikinci"

    def test_turkish_letter_list(self):
        text = "Maddeler\nç) çeşit\nş) şekil"
        assert normalize_ocr_text(text) == "Maddeler\nç) çeşit\nş) şekil"

    def test_roman_numeral_list(self):
        text = "Maddeler\n(i) bir\n(ii) iki\n(iii) üç"
        assert normalize_ocr_text(text) == "Maddeler\n(i) bir\n(ii) iki\n(iii) üç"


class TestParenthesesAnnotation:
    def test_full_line_parens_preserved(self):
        text = "Aksa Akrilik Kimya Sanayii\n(Kaşe ve İmzalar)\nDevam metin"
        # Tek-satırlık metadata; birleştirme yok
        assert normalize_ocr_text(text) == "Aksa Akrilik Kimya Sanayii\n(Kaşe ve İmzalar)\nDevam metin"

    def test_partial_parens_still_merges(self):
        # Sadece açık parantez var, kapanmıyor → annotation değil
        text = "Bilgi\n(devam burada"
        assert normalize_ocr_text(text) == "Bilgi (devam burada"


class TestRealWorldAksa:
    def test_aksa_company_full_name(self):
        # Gerçek Aksa OCR senaryosu
        text = (
            "Saygılarımızla,\n"
            "Aksa Akrilik Kimya Sanayii\n"
            "Anonim Şirketi\n"
            "(Kaşe ve İmzalar)\n"
            "Aksa Akrilik Kimya Sanayii\n"
            "Anonim Şirketi'nin\n"
            "2015 Yılına Ait Olağan Genel"
        )
        result = normalize_ocr_text(text)

        # Şirket adının iki versiyonu da tek satıra birleşmeli
        assert "Aksa Akrilik Kimya Sanayii Anonim Şirketi" in result
        assert "Aksa Akrilik Kimya Sanayii Anonim Şirketi'nin 2015 Yılına Ait Olağan Genel" in result

        # Annotation satırı korunmalı
        assert "(Kaşe ve İmzalar)" in result
        # "(Kaşe ve İmzalar)" kendi satırında durmalı, etrafına bulaşmamalı
        lines = result.split("\n")
        assert "(Kaşe ve İmzalar)" in lines

    def test_aksa_with_page_marker(self):
        text = (
            "[[PAGE:1]]\n"
            "\n"
            "9 MART 2016 SAYI : 9028\n"
            "TÜRKİYE TİCARET SİCİLİ GAZETESİ\n"
            "SAYFA : 1371"
        )
        result = normalize_ocr_text(text)
        # Page marker korunmalı
        assert result.startswith("[[PAGE:1]]")
        # "SAYI :" sonrası iki nokta → SAYFA satırı ayrı kalır
        # "TÜRKİYE TİCARET SİCİLİ GAZETESİ" başlık → bir sonraki satırla birleşmeli
        assert "TÜRKİYE TİCARET SİCİLİ GAZETESİ SAYFA : 1371" in result


class TestIdempotency:
    def test_idempotent_basic(self):
        text = "Aksa Akrilik Kimya Sanayii\nAnonim Şirketi\n\nİkinci paragraf"
        once = normalize_ocr_text(text)
        twice = normalize_ocr_text(once)
        assert once == twice

    def test_idempotent_complex(self):
        text = (
            "[[PAGE:1]]\n\n"
            "Başlık\n\n"
            "1. madde\n2. madde\n\n"
            "Aksa Akrilik\nKimya\n\n"
            "(Kaşe)\n"
            "şir-\nket\n"
        )
        once = normalize_ocr_text(text)
        twice = normalize_ocr_text(once)
        assert once == twice


class TestTrailingNewline:
    def test_trailing_newline_preserved(self):
        assert normalize_ocr_text("hello\nworld\n") == "hello world\n"

    def test_no_trailing_newline_preserved(self):
        assert normalize_ocr_text("hello\nworld") == "hello world"

    def test_crlf_treated_as_newline(self):
        # splitlines() handles \r\n; trailing \r counts as line ending
        assert normalize_ocr_text("hello\r\nworld\r\n") == "hello world\n"
