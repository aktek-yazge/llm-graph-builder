# Ticaret Sicil Gazetesi - Entity Extraction

## HEDEF ŞİRKET

**Dosya Adı:** "{file_name}"

Dosya adından hedef şirketi belirle ve SADECE o şirketle ilgili bilgileri çıkar.
Ticaret Sicil Gazetesi aynı sayfada birden fazla şirketin ilanını içerir - diğerlerini ATLA.

## GÖREV

Bu belgeden hedef şirket için:
1. Hangi bilgiler açıkça yazılı?
2. Bir kullanıcı bu şirket hakkında ne öğrenmek ister?
3. Bu soruları cevaplayacak unique bilgileri çıkar

## KRİTİK KURALLAR

- **SADECE belgede açıkça yazılı bilgileri çıkar**
- **Belgede yoksa çıkarma** - boş array dön, uydurmak yasak
- **Tahmin etme, çıkarım yapma** - sadece gördüğünü yaz
- Hedef şirket dışındaki tüm bilgileri ATLA

## Belge İçeriği:

{document_content}

---

## ÇIKTI FORMATI

```json
{{
  "target_company": "Dosya adından belirlenen hedef şirket adı",
  "document_type": "Belge tipi (ör: Genel Kurul Çağrısı, Yönetim Kurulu Kararı, Sermaye Artırımı)",
  
  "nodes": [
    {{
      "label": "Entity tipi (Company, Person, Meeting, Capital, vb.)",
      "id": "unique_id",
      "properties": {{
        "belgede bulunan özellikler"
      }}
    }}
  ],
  
  "relationships": [
    {{
      "from_id": "kaynak_node_id",
      "to_id": "hedef_node_id", 
      "type": "İLİŞKİ_TİPİ"
    }}
  ]
}}
```

## ID KURALLARI

- **Şirket:** `company_[sicil_no]` veya `company_[normalized_name]`
- **Kişi:** `person_[normalized_name]` (ad_soyad formatında, türkçe karakter yok)
- **Toplantı:** `meeting_[company_id]_[YYYYMMDD]`
- **Normalization:** küçük harf, türkçe karakter dönüştür (ş→s, ğ→g, ü→u, ö→o, ç→c, ı→i), boşluk→_

## ENTITY NORMALİZASYON

Her entity için `normalized_name` property ekle - arama ve eşleştirme için:

| Belgede Yazılan | normalized_name |
|-----------------|-----------------|
| "AKSA AKRİLİK KİMYA SANAYİİ A.Ş." | `aksa` |
| "Mehmet Öztürk" | `mehmet_ozturk` |

## ÖRNEK DURUMLAR

### Durum 1: Genel Kurul Çağrısı (kişi adı yok)

Belge sadece toplantı daveti içeriyorsa:
- Company node çıkar (şirket bilgileri)
- Meeting node çıkar (toplantı tarihi, saati, adresi)
- Person node ÇIKARMA (belgede kişi adı yoksa)

### Durum 2: Yönetim Kurulu Kararı (kişi adları var)

Belgede kişi adları açıkça yazılıysa:
- Company, Meeting, Person node'ları çıkar
- Kişilerin pozisyonlarını relationship'e ekle

---

**HATIRLA:** Belgede yazmayan hiçbir bilgiyi ekleme. Boş array döndürmek, yanlış bilgi döndürmekten iyidir.
