# Entity Extraction Prompt

Verilen sigorta poliçesi belgesinden aşağıdaki bilgileri çıkar ve JSON formatında döndür.

Belge adı: "{file_name}"

Belge İçeriği:
---
{document_content}
---

Çıkarılacak bilgiler (tüm alanlar opsiyonel, varsa doldur):

1. CUSTOMER (Müşteri) - ÖNEMLİ:
   - name: Müşteri adı (kişi adı veya şirket adı)
   - tc_kimlik_no: TC Kimlik No (11 haneli)
   - vergi_no: Vergi No (10 haneli)
   - phone: Telefon numarası
   - email: E-posta adresi
   - address: Adres

2. POLICY (Poliçe) - ÖNEMLİ:
   - policy_number: Poliçe numarası
   - renewal_number: Yenileme numarası
   - endorsement_number: Zeyil numarası
   - policy_type: Poliçe türü (Kasko, Trafik, Konut, DASK, Sağlık, Seyahat, vb.)

3. INSURANCE_COMPANY (Sigorta Şirketi):
   - name: Şirket adı
   - code: Şirket kodu

4. AGENT (Acente):
   - name: Acente adı/ünvanı
   - code: Acente kodu

5. DATES (Tarihler) - ÖNEMLİ:
   - start_date: Başlama tarihi (DD.MM.YYYY)
   - end_date: Bitiş tarihi (DD.MM.YYYY)
   - issue_date: Tanzim tarihi (DD.MM.YYYY)

6. PREMIUM (Prim):
   - gross_premium: Brüt prim
   - net_premium: Net prim
   - tax: Vergi tutarı
   - total: Toplam tutar
   - currency: Para birimi (TL, USD, EUR)

7. COVERAGE_LIMITS (Teminat Limitleri):
   - Array of objects with: coverage_name, limit, deductible

8. RISK_ADDRESS (Riziko Adresi):
   - full_address: Tam adres
   - city: İl
   - district: İlçe

9. VEHICLE (Araç - Kasko/Trafik için):
   - plate: Plaka
   - brand: Marka
   - model: Model
   - year: Model yılı
   - chassis_no: Şasi numarası
   - engine_no: Motor numarası

10. DOCUMENT_TYPE:
    - type: "MAIN_POLICY" | "ENDORSEMENT" | "RENEWAL" | "CANCELLATION"

JSON formatında döndür. Markdown code block (```) KULLANMA.
Bulunamayan alanları null olarak bırak.
