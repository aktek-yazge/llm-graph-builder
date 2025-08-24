# Page Image Generation Feature

Bu özellik DoclingLoader kullanarak PDF dosyalarından page image'ları generate etmenizi sağlar.

## Yeni Özellikler

### 1. Page Image Generation

- PDF dosyalarını işlerken page image'larını otomatik olarak generate edebilir
- Image'lar belirtilen output klasörüne `dosya_adı_page_001.png` formatında kaydedilir
- Yüksek kalite için 2x resolution scale kullanılır

### 2. Güncellenmiş Fonksiyonlar

#### `get_documents_from_file_by_path()`

```python
def get_documents_from_file_by_path(file_path, file_name, generate_images=False, output_dir="output"):
    """
    Args:
        file_path: PDF dosyasının yolu
        file_name: Dosya adı
        generate_images: Page image'ları generate et (bool, default: False)
        output_dir: İmage'ların kaydedileceği klasör (str, default: "output")

    Returns:
        tuple: (file_name, pages, file_extension, generated_images)
    """
```

#### `generate_page_images()`

```python
def generate_page_images(file_path, output_dir="output"):
    """
    PDF dosyasından page image'larını generate eder.

    Args:
        file_path: PDF dosyasının yolu
        output_dir: Çıktı klasörü (varsayılan: "output")

    Returns:
        List[str]: Kaydedilen image dosyalarının yolları
    """
```

## Kullanım Örnekleri

### Temel Kullanım

```python
from src.document_sources.local_file import get_documents_from_file_by_path

# Sadece text extraction
file_name, pages, file_extension, generated_images = get_documents_from_file_by_path(
    file_path="document.pdf",
    file_name="document.pdf"
)

# Page image'ları da generate et
file_name, pages, file_extension, generated_images = get_documents_from_file_by_path(
    file_path="document.pdf",
    file_name="document.pdf",
    generate_images=True,
    output_dir="my_output_folder"
)

print(f"Generated {len(generated_images)} page images:")
for img_path in generated_images:
    print(f"  - {img_path}")
```

### Sadece Image Generation

```python
from src.document_sources.local_file import generate_page_images

# Sadece page image'ları generate et
generated_images = generate_page_images(
    file_path="document.pdf",
    output_dir="output"
)
```

## Test Etme

```bash
cd backend
python test_page_images.py
```

## Çıktı Formatı

Generete edilen image'lar şu formatta kaydedilir:

- `document_page_001.png`
- `document_page_002.png`
- `document_page_003.png`
- ...

## Teknik Detaylar

- **Image Resolution**: 2x scale (144 DPI equivalent)
- **Format**: PNG
- **Pipeline Options**:
  - `generate_page_images=True`
  - `generate_picture_images=True`
  - `images_scale=2.0`

## Gereksinimler

- docling
- langchain-docling
- Pillow (PIL)
- docling-core

## Loglama

Tüm işlemler logging ile takip edilir:

- Image generation başlangıcı ve bitişi
- Her page image'ının kaydedilme durumu
- Hata durumları
- İşlem süreleri

