# Neo4j Schema Extractor

Neo4j veritabanından schema bilgilerini çeken ve formatlı string olarak döndüren Python modülü.

## Kullanım Şekilleri

### 1. Komut Satırından Kullanım

```bash
python schema_extractor.py
```

**Çıktı:**

```
Nodes: Chunk(fileName:string, ...); Customer(...); ...
Rels: DOCUMENTED_IN(created_at:datetime, source:string); FIRST_CHUNK; ...
Patterns: (Chunk)-[NEXT_CHUNK]->(Chunk); (Chunk)-[PART_OF]->(Document); ...
```

### 2. Import ile Basit Kullanım

```python
from schema_extractor import get_compact_schema

# En basit kullanım
schema = get_compact_schema()
print(schema)
```

### 3. Detaylı Schema Bilgileri

```python
from schema_extractor import get_detailed_schema

# Detaylı bilgi dict olarak
schema_info = get_detailed_schema()

print(f"Node sayısı: {len(schema_info['nodes'])}")
print(f"Relationship sayısı: {len(schema_info['relationships'])}")
print(f"Pattern sayısı: {len(schema_info['patterns'])}")

# Compact string'e erişim
print(schema_info['compact_string'])

# Spesifik node bilgilerine erişim
for node_name, properties in schema_info['nodes'].items():
    print(f"{node_name}: {properties}")
```

### 4. Class Instance Kullanımı

```python
from schema_extractor import Neo4jSchemaExtractor

# Advanced kullanım için class instance
extractor = Neo4jSchemaExtractor()

# Sadece node'ları al
nodes = extractor.get_node_schemas()

# Sadece relationship'leri al
relationships = extractor.get_relationship_schemas()

# Sadece pattern'leri al
patterns = extractor.get_relationship_patterns()

# Compact format
schema_string = extractor.format_schema_string()
```

## Gereksinimler

- Python 3.7+
- langchain-neo4j
- Neo4j veritabanı bağlantısı

## Çevre Değişkenleri

```bash
export NEO4J_URI="bolt://localhost:7687"
export NEO4J_USERNAME="neo4j"
export NEO4J_PASSWORD="your_password"
export NEO4J_DATABASE="neo4j"
```

## Export Edilen Fonksiyonlar

- `Neo4jSchemaExtractor`: Ana class
- `get_compact_schema()`: Basit kullanım için wrapper
- `get_detailed_schema()`: Detaylı bilgiler için wrapper

## Örnek Projede Kullanım

```python
# intelligent_agent.py dosyasında schema güncelleme
from schema_extractor import get_compact_schema

def update_system_prompt():
    current_schema = get_compact_schema()
    # Schema'yı system prompt'a ekle
    return f"Current schema: {current_schema}"
```

## Token Optimizasyonu

Bu modül token verimliliği için optimize edilmiştir:

- Minimum log çıktısı
- Kompakt format
- Gereksiz bilgiler filtrelenir
- 3 satır halinde özet çıktı

