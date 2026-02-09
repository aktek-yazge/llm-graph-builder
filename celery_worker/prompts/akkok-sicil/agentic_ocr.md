# Ticaret Sicil Gazetesi OCR

## HEDEF
Dosya: "{file_name}"
Bu dosya adından hedef şirketi çıkar ve SADECE o şirketin ilanını işle.

## GÖREV
Bu prompt artık kullanılmıyor. Dinamik prompt `agentic_ocr.py` içinde `_build_page_prompt()` ile oluşturulur.

Her sayfa için agent şu adımları izler:
1. Görüntüyü analiz et, hedef şirketin bölgelerini bul
2. Hedef şirkete ait DEĞİLSE → `mark_skipped(x1,y1,x2,y2,"açıklama")`
3. Hedef şirkete aitse → `crop(input_path,x1,y1,x2,y2)` sonra `mark_cropped(x1,y1,x2,y2,"açıklama")`
4. `get_page_log()` ile durumu kontrol et
5. Tüm bölgeler işlenince → `finish_page(markdown, continuation_note, is_complete)`

## TOOL'LAR
- `mark_skipped(x1,y1,x2,y2,desc)` - Atlanan koordinatı kaydet
- `crop(input_path,x1,y1,x2,y2)` - Bölüm kırp (ImageSorcery)
- `mark_cropped(x1,y1,x2,y2,desc)` - Kırpılan koordinatı kaydet
- `get_page_log()` - Hangi koordinatlar işlendi?
- `finish_page(markdown,continuation_note,is_complete)` - Sayfa tamamla
