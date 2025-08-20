---
applyTo: '**'
---

# Genel Kodlama Standartları

## llm-graph-builder Projesi Kodlama Standartları

### 1. İsimlendirme

- Python dosyalarında fonksiyonlar, değişkenler ve methodlar için snake_case kullanılır.
- Sınıf isimleri PascalCase ile yazılır.
- Sabitler (constants) genellikle ALL_CAPS ile tanımlanır.
- Modül ve dosya isimleri küçük harf ve alt çizgi ile yazılır (ör: graph_query.py).
- React/TypeScript tarafında componentler PascalCase, değişkenler camelCase, sabitler ALL_CAPS ile yazılır.

### 2. Fonksiyon ve Dosya Yapısı

- Her fonksiyon tek bir sorumluluğa sahip olmalı, uzun fonksiyonlar alt fonksiyonlara bölünmeli.
- Fonksiyonlar ve sınıflar dosya başında importlar, ardından sabitler, sonra ana kod ve yardımcı fonksiyonlar şeklinde sıralanır.
- Kodda tekrar eden bloklar fonksiyonlaştırılır.
- Gerekli yerlerde type hinting ve docstring kullanılır.

### 3. Hata Yönetimi

- try/except blokları ile hata yakalama yapılır, hata mesajları logging ile kaydedilir.
- Hatalar kullanıcıya sade ve anlaşılır şekilde iletilir, sistem hataları loglanır.
- API endpointlerinde ve asenkron fonksiyonlarda hata yönetimi zorunludur.

### 4. Loglama

- logging modülü ile bilgi, hata ve debug seviyesinde loglama yapılır.
- Her önemli işlem ve hata loglanır, log mesajları açıklayıcı olmalıdır.

### 5. Kodda Açıklama ve Dokümantasyon

- Karmaşık fonksiyonlar ve önemli iş akışları için açıklayıcı yorum satırları eklenir.
- Her modül ve ana fonksiyon için docstring bulunur.
- API endpointleri ve ana işlevler için örnek input/output açıklanır.

### 7. Bağımlılıklar ve Ortam

- requirements.txt ve constraints.txt dosyaları ile bağımlılıklar yönetilir.
- Ortam değişkenleri .env dosyası veya os.environ ile okunur.
- Docker ve docker-compose ile container ortamı desteklenir.
- Fonksiyon bağımlılıkları mutlaka import edilmelidir, global değişkenlerden kaçınılmalıdır.

### 8. Kodda Temizlik ve Düzen

- Kullanılmayan importlar ve değişkenler silinir.
- Fonksiyonlar ve sınıflar arasında bir satır boşluk bırakılır.
- Kodda magic number yerine sabitler kullanılır.

### 9. Güvenlik ve Veri

- Şifre, anahtar gibi hassas bilgiler kodda tutulmaz, ortam değişkeni ile alınır.
- Kullanıcıdan alınan veriler doğrulanır ve temizlenir.

### 10. Performans

- Büyük veri işlemlerinde gereksiz tekrar ve döngülerden kaçınılır.
- Sorgular optimize edilir, gereksiz veritabanı çağrılarından kaçınılır.

---

> Bu dosya, llm-graph-builder projesinin mevcut kod tabanındaki pratiklere ve standartlara göre hazırlanmıştır. Yeni kod eklerken veya refaktör yaparken bu kurallara uyulması beklenir.

İstenmden test kodu oluşturmaya çalışma

Mevcut kod tabanında ihtiyacın olan methodlar yer alabilir. Bunlar dikkate alınmalı ve yeni eklemeler ona göre yapılmalı

