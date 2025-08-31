# GitHub Copilot Instructions for LLM Graph Builder

Bu projede AI coding assistant'lara yönelik yönergeler ve standartlar. Yeni geliştirici veya AI agent onboarding için kritik bilgiler.

## 🧠 Bilgi Kaynakları ve MCP Tools

### Basic-Memory MCP
- **Proje detayları:** Tüm proje kuralları ve standartları basic-memory MCP'de saklanmaktadır
- **İlk başvuru:** Proje hakkında sorular sorulduğunda önce basic-memory'ye başvurun
- **Güncel bilgi:** Endpoint analizleri, workflow'lar, coding standards basic-memory'de mevcuttur
- **Kullanım:** `mcp_basic-memory2_search_notes` ile proje bilgilerini arayın

### Neo4j Aura MCP Tools
- **Graph database:** Neo4j Aura database ile etkileşim için MCP tools mevcuttur
- **Schema queries:** Database schema ve relationships sorgulamak için kullanın
- **Data operations:** Graph data okuma/yazma işlemleri için

### Grafana MCP Tools
- **Monitoring:** Grafana dashboard ve logging verilerine erişim
- **Metrics:** System performance ve health check verileri
- **Alerts:** Alert rules ve notification systems

### Bilgi Alma Stratejisi
1. **İlk adım:** basic-memory'de proje bilgilerini arayın
2. **Database işlemleri:** Neo4j MCP tools kullanın
3. **Monitoring:** Grafana MCP tools ile sistem durumunu kontrol edin
4. **Güncel tutma:** Değişiklikleri basic-memory'ye kaydedin

## 🏗️ Proje Mimarisi

### Tech Stack
- **Backend:** FastAPI + Python (uvicorn score:app --reload)
- **Frontend:** React + TypeScript + Vite (yarn dev)
- **Database:** Neo4j Aura (Graph Database)
- **Monitoring:** OpenTelemetry + Grafana + Loki + Promtail
- **File Processing:** Docling (PDF/DOCX → Markdown dönüştürme)
- **Storage:** S3/GCS entegrasyonu

### Dizin Yapısı
```
llm-graph-builder/
├── backend/           # FastAPI backend uygulaması
├── frontend/          # React frontend uygulaması  
├── src/               # Ana backend logic
├── data/              # Veri dosyaları
├── monitoring/        # Grafana/Loki konfigürasyonları
└── .github/           # GitHub Actions ve dökümanlar
```

## 🚀 Development Environment

### Backend Başlatma
```bash
cd backend
conda activate graph-builder  # Zorunlu conda environment
uvicorn score:app --reload    # Hot reload ile başlatma
```
- **Endpoint:** http://localhost:8000
- **Health Check:** http://localhost:8000/health
- **Chat API:** `/chat_bot`
- **Hot Reload:** Dosya değişikliklerinde otomatik restart

### Frontend Başlatma
```bash
cd frontend
yarn install  # İlk kurulum için
yarn dev      # Development server
```
- **URL:** http://localhost:5173
- **Hot Reload:** Vite HMR ile anlık güncelleme
- **UI Library:** neo4j-ndl/react (Neo4j UI components)

## 📝 Coding Standards

### Environment Variables
**KRİTİK KURAL:** `os.getenv()` kullanılırsa mutlaka:
```python
from dotenv import load_dotenv
load_dotenv()  # En üstte çağrılmalı

# Sonra environment variable kullanımı
api_key = os.getenv("API_KEY")
```

### Logging Standards
```python
import logging

# Structured logging format
logger = logging.getLogger(__name__)
logger.info(
    "Operation completed",
    extra={
        "operation": "file_upload",
        "file_name": filename,
        "status": "success",
        "duration_ms": duration
    }
)
```

### Test Code Patterns
```python
# Test class naming
class TestDocumentAPI:
    # Test method naming
    def test_upload_document_success(self):
        # Given-When-Then pattern
        # Given
        test_file = create_test_pdf()
        
        # When
        response = client.post("/upload", files={"file": test_file})
        
        # Then
        assert response.status_code == 200
        assert "document_id" in response.json()
```

## 📊 Graph Database Schema

### Entity Types (Node Labels)
- **Document:** Sigorta belgeleri (PDF/DOCX)
- **Endorsement:** Zeyilnameler (Poliçe ekleri/değişiklikleri)
- **Policy:** Sigorta poliçeleri (ana business entity)  
- **Customer:** Poliçe sahipleri/müşteriler
- **PolicyYear:** Poliçe yılları
- **InsuredItem:** Sigortalanan nesneler (ev, araba, eşya)
- **PolicyType:** Poliçe türleri (konut, kasko, sağlık)
- **Chunk:** Belge parçaları/içerik segmentleri

### Core Business Relationships
```cypher
Customer -[HAS_POLICY]-> Policy          # Müşterinin hangi poliçesi var
Policy -[HAS_ENDORSEMENT]-> Endorsement  # Poliçenin zeyilnameleri
Policy -[DOCUMENTED_IN]-> Document       # Poliçe hangi belgede yer alır
Policy -[HAS_YEAR]-> PolicyYear          # Poliçenin hangi yıla ait
Policy -[HAS_INSURED_ITEM]-> InsuredItem # Neyi sigortalar
Policy -[HAS_TYPE]-> PolicyType          # Poliçe türü
Customer -[HAS_DOC]-> Document           # Müşterinin hangi belgeleri var
Chunk -[PART_OF]-> Document              # Chunk hangi belgenin parçası
Chunk -[NEXT_CHUNK]-> Chunk              # Chunk sıralaması
Document -[FIRST_CHUNK]-> Chunk          # Belgenin ilk chunk'ı
```

### Business Logic Rules
- Her Document'ın bir Policy'si olabilir (DOCUMENTED_IN)
- Her Policy'nin bir veya daha fazla Endorsement'ı(Zeyilnamesi) olabilir (HAS_ENDORSEMENT)
- Her Customer'ın birden fazla Policy'si olabilir (HAS_POLICY)
- Her Policy'nin bir Customer'ı vardır (mandatory relationship)
- Her Policy'nin bir yılı (PolicyYear), türü (PolicyType) ve sigortalanan nesnesi (InsuredItem) vardır
- Document'lar Chunk'lara bölünür ve sayfa numaraları ile organize edilir

## 🔧 API Endpoints

### Core Endpoints
- `GET /health` - Health check
- `POST /chat_bot` - Chat streaming API
- `POST /upload` - Document upload
- `GET /documents` - Document listing
- `POST /extract_graph` - Graph extraction

### Search Modes
- **VECTOR_GRAPH_SEARCH_QUERY** (varsayılan)
- **VECTOR_SEARCH_QUERY**
- **GRAPH_SEARCH_QUERY**

## 📈 Monitoring & Logging

### Log Labels (Grafana Dashboard)
```json
{
  "level": "info|error|warning",
  "service": "backend|frontend", 
  "operation": "file_upload|chat|graph_extract",
  "status": "success|error|pending"
}
```

### OpenTelemetry Integration
- **Traces:** Request tracking
- **Metrics:** Performance monitoring  
- **Logs:** Structured logging ile correlation

## ⚠️ Önemli Kurallar

### 0. MCP Tools ve Bilgi Kaynakları
- **ÖNCE basic-memory:** Her proje sorusunda önce basic-memory MCP'yi kontrol edin
- **Neo4j operations:** Database işlemleri için Neo4j Aura MCP tools kullanın
- **Grafana monitoring:** Sistem durumu için Grafana MCP tools kullanın
- **Bilgi güncellemesi:** Yeni bilgileri basic-memory'ye kaydetmeyi unutmayın

### 1. Environment Setup
- Backend için `conda activate graph-builder` zorunlu
- `.env` dosyaları mutlaka `load_dotenv()` ile yüklenmeli
- Her environment variable kullanımından önce load_dotenv() kontrolü

### 2. Hot Reload
- Backend ve frontend otomatik reload çalışır
- Manual restart gereksiz
- Değişiklikler otomatik yansır

### 3. Test Standards
- Mevcut test kodlarına bakarak pattern'leri takip et
- Given-When-Then yapısını kullan
- Test class ve method naming convention'larını koru

### 4. Database Operations
- Neo4j Aura connection strings environment'tan al
- Graph query'lerde performans optimizasyonu yap
- Chunk relationships'leri doğru şekilde kur

### 5. File Processing
- Docling kullanarak PDF/DOCX → Markdown dönüştürme
- S3/GCS integration için credentials check
- File upload size limits kontrol et

## 🔍 Troubleshooting

### Common Issues
1. **Backend başlamazsa:** conda environment check
2. **Frontend hot reload çalışmazsa:** node_modules clear + yarn install
3. **Database connection:** Neo4j Aura credentials check
4. **Logging görünmezse:** OpenTelemetry configuration check

### Debug Commands
```bash
# Backend health check
curl http://localhost:8000/health

# Frontend build check  
cd frontend && yarn build

# Neo4j connection test
python -c "from src.graph_service import test_connection; test_connection()"
```

## 📚 Key Files

### Backend Entry Points
- `backend/score.py` - Main FastAPI app
- `src/main.py` - Core backend logic
- `src/QA_integration.py` - Chat streaming

### Frontend Entry Points  
- `frontend/src/main.tsx` - React entry point
- `frontend/src/App.tsx` - Main app component

### Configuration
- `backend/.env` - Backend environment variables
- `frontend/.env` - Frontend environment variables
- `monitoring/` - Grafana/Loki configs

Bu döküman sürekli güncellenmektedir. Proje standardlarına uygun development için referans alınmalıdır.
