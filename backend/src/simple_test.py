import json

def to_minimal_data_format(data):
    """
    Cypher sorgu sonuçlarını şema benzeri minimal formata çevirir
    """
    if isinstance(data, str):
        data = json.loads(data)
    
    if not isinstance(data, list):
        return "Error: Veri list formatında olmalı"
    
    if not data:
        return "[]"
    
    # Her kaydı şema formatında minimal hale çevir
    minimal_records = []
    for i, record in enumerate(data):
        # Key-value çiftlerini oluştur
        props = []
        for key, value in record.items():
            props.append(f"{key}:{value}")
        
        # (Record:index){key1:value1,key2:value2} formatında
        minimal_records.append(f"(Record:{i}){{{','.join(props)}}}")
    
    return '\n'.join(minimal_records)

def to_minimal_cypher_format(schema_json):
    """
    Cypher benzeri minimal format - sadece şema alır
    """
    if isinstance(schema_json, str):
        schema = json.loads(schema_json)
    else:
        schema = schema_json
    
    lines = []
    
    # Tip kısaltmaları
    type_mapping = {
        "STRING": "str",
        "INTEGER": "int", 
        "DATE_TIME": "dt",
        "LOCAL_DATE_TIME": "ldt",
        "BOOLEAN": "bool",
        "LIST": "list",
        "FLOAT": "float"
    }
    
    # Tüm node'ları işle - şemadan otomatik çıkar
    for node_name, node_data in schema.items():
        if node_data.get("type") == "node":
            count = node_data.get("count", 0)
            # İlk 6 property'yi kısa tip bilgisiyle al
            properties = node_data.get("properties", {})
            props_with_types = []
            for prop_name, prop_info in list(properties.items())[:6]:
                prop_type = prop_info.get("type", "?")
                short_type = type_mapping.get(prop_type, prop_type.lower()[:3])
                props_with_types.append(f"{prop_name}:{short_type}")
            lines.append(f"({node_name}:{count}){{{','.join(props_with_types)}}}")
    
    return '\n'.join(lines)

if __name__ == "__main__":
    print("=== FONKSİYONLAR HAZIR ===")
    print("1. to_minimal_cypher_format(schema_json) - Şema için")
    print("2. to_minimal_data_format(data_list) - Cypher sonuçları için")
    print()
    
    # Test cypher sonuçları
    test_data = [
  {
    "policyNumber": "65790193",
    "name": "Ayça Dinçkök Galata Residance D6 Konut_2020",
    "type": "Konut Sigortası",
    "id": "Ayça Dinçkök Galata Residance D6 Konut_2020"
  },
  {
    "policyNumber": "81045975",
    "name": "Sernur Çiftçi Büyükhanlı 1A D6 Konut_2020",
    "type": "Konut Sigortası",
    "id": "Sernur Çiftçi Büyükhanlı 1A D6 Konut_2020"
  },
  {
    "policyNumber": "81033531",
    "name": "Sernur Çiftçi Barclay 19 D6 Konut_2020",
    "type": "Konut Sigortası",
    "id": "Sernur Çiftçi Barclay 19 D6 Konut_2020"
  },
  {
    "policyNumber": "81032009",
    "name": "Samim Çiftçi CARLTON 17 D6 Konut_2020",
    "type": "Konut Sigortası",
    "id": "Samim Çiftçi CARLTON 17 D6 Konut_2020"
  },
  {
    "policyNumber": "81052885",
    "name": "Satvet Çiftçi Büyükhanlı 1B D6 Konut_2020",
    "type": "Konut Sigortası",
    "id": "Satvet Çiftçi Büyükhanlı 1B D6 Konut_2020"
  },
  {
    "policyNumber": "81061634",
    "name": "Satvet Çiftçi CARLTON8 D6 Konut_2020",
    "type": "Konut Sigortası",
    "id": "Satvet Çiftçi CARLTON8 D6 Konut_2020"
  },
  {
    "policyNumber": "81067545",
    "name": "Satvet Çiftçi Çiftçiler Apt. D6 Konut_2020",
    "type": "Konut Sigortası",
    "id": "Satvet Çiftçi Çiftçiler Apt. D6 Konut_2020"
  },
  {
    "policyNumber": "81026115",
    "name": "Sernur Çiftçi Barclay 5 D6 Konut_2020",
    "type": "Konut Sigortası",
    "id": "Sernur Çiftçi Barclay 5 D6 Konut_2020"
  }
]

    
    print("=== CYPHER SONUÇLARI TEST ===")
    data_result = to_minimal_data_format(test_data)
    print(data_result)
    print(f"Orijinal: {len(json.dumps(test_data))} karakter")
    print(f"Minimal: {len(data_result)} karakter")
    print(f"Kazanç: %{100 - (len(data_result)/len(json.dumps(test_data))*100):.1f}")
    print()
    
    # Test için boş bir çalışma alanı  
    your_schema = {"InsuredItem": {"type": "node", "count": 1098, "properties": {"updatedAt": {"indexed": 
"false", "type": "DATE_TIME"}, "policyCount": {"indexed": "false", "type": "INTEGER"}, 
"description": {"indexed": "false", "type": "STRING"}, "createdAt": {"indexed": "false", "type": 
"DATE_TIME"}, "name": {"indexed": "false", "type": "STRING"}}, "relationships": 
{"HAS_INSURED_ITEM": {"direction": "in", "labels": ["Policy"], "properties": {"created_at": 
{"indexed": "false", "type": "DATE_TIME"}}}}}, "SavedAnswer": {"type": "node", "count": 6, 
"properties": {"createdAt": {"indexed": "false", "type": "DATE_TIME"}, "answer": {"indexed": 
"false", "type": "STRING"}, "policyNumber": {"indexed": "false", "type": "STRING"}, "question": 
{"indexed": "false", "type": "STRING"}}}, "PART_OF": {"type": "relationship", "count": 99838}, 
"HAS_YEAR": {"type": "relationship", "count": 1765, "properties": {"created_at": {"indexed": 
"false", "type": "DATE_TIME"}}}, "HAS_TYPE": {"type": "relationship", "count": 1922, 
"properties": {"created_at": {"indexed": "false", "type": "DATE_TIME"}}}, "PolicyYear": 
{"type": "node", "count": 17, "properties": {"updatedAt": {"indexed": "false", "type": 
"DATE_TIME"}, "policyCount": {"indexed": "false", "type": "INTEGER"}, "createdAt": {"indexed": 
"false", "type": "DATE_TIME"}, "name": {"indexed": "false", "type": "STRING"}, "year": 
{"indexed": "false", "type": "INTEGER"}}, "relationships": {"HAS_YEAR": {"direction": "in", 
"labels": ["Policy"], "properties": {"created_at": {"indexed": "false", "type": 
"DATE_TIME"}}}}}, "Memory": {"type": "node", "count": 6, "properties": {"name": {"indexed": 
"true", "type": "STRING"}, "type": {"indexed": "true", "type": "STRING"}, "observations": 
{"indexed": "true", "type": "LIST"}}}, "DOCUMENTED_IN": {"type": "relationship", "count": 1912,
"properties": {"source": {"indexed": "false", "type": "STRING"}, "created_at": {"indexed": 
"false", "type": "DATE_TIME"}}}, "LAST_MESSAGE": {"type": "relationship", "count": 88}, 
"Session": {"type": "node", "count": 148, "properties": {"id": {"indexed": "false", "type": 
"STRING"}, "createdAt": {"indexed": "false", "type": "DATE_TIME"}}, "relationships": 
{"LAST_MESSAGE": {"direction": "out", "labels": ["Message"]}}}, "PolicyType": {"type": 
"node", "count": 9, "properties": {"typeName": {"indexed": "false", "type": "STRING"}, 
"updatedAt": {"indexed": "false", "type": "DATE_TIME"}, "policyCount": {"indexed": "false", 
"type": "INTEGER"}, "createdAt": {"indexed": "false", "type": "DATE_TIME"}, "name": {"indexed":
"false", "type": "STRING"}}, "relationships": {"HAS_TYPE": {"direction": "in", "labels": 
["Policy"], "properties": {"created_at": {"indexed": "false", "type": "DATE_TIME"}}}}}, 
"Customer": {"type": "node", "count": 309, "properties": {"updatedAt": {"indexed": "false", 
"type": "DATE_TIME"}, "policyCount": {"indexed": "false", "type": "INTEGER"}, "createdAt": 
{"indexed": "false", "type": "DATE_TIME"}, "name": {"indexed": "false", "type": "STRING"}, 
"fullName": {"indexed": "false", "type": "STRING"}}, "relationships": {"HAS_DOC": {"direction":
"out", "labels": ["Document"], "properties": {"created_at": {"indexed": "false", "type": 
"DATE_TIME"}}}, "HAS_POLICY": {"direction": "out", "labels": ["Policy"], "properties": 
{"created_at": {"indexed": "false", "type": "DATE_TIME"}}}}}, "Document": {"type": "node", 
"count": 1945, "properties": {"errorMessage": {"indexed": "false", "type": "STRING"}, "model": 
{"indexed": "false", "type": "STRING"}, "communityNodeCount": {"indexed": "false", "type": 
"INTEGER"}, "docType": {"indexed": "false", "type": "STRING"}, "fileSource": {"indexed": "false",
"type": "STRING"}, "linkedMainPolicy": {"indexed": "false", "type": "STRING"}, "chunkRelCount":
{"indexed": "false", "type": "INTEGER"}, "fileSize": {"indexed": "false", "type": "INTEGER"}, 
"doc_link": {"indexed": "false", "type": "STRING"}, "relationshipCount": {"indexed": "false", 
"type": "INTEGER"}, "createdAt": {"indexed": "false", "type": "LOCAL_DATE_TIME"}, 
"processed_chunk": {"indexed": "false", "type": "INTEGER"}, "year": {"indexed": "false", "type": 
"STRING"}, "nodeCount": {"indexed": "false", "type": "INTEGER"}, "fileType": {"indexed": "false",
"type": "STRING"}, "status": {"indexed": "false", "type": "STRING"}, "page_images": {"indexed":
"false", "type": "LIST"}, "processingTime": {"indexed": "false", "type": "INTEGER"}, 
"total_chunks": {"indexed": "false", "type": "INTEGER"}, "entityNodeCount": {"indexed": "false", 
"type": "INTEGER"}, "is_cancelled": {"indexed": "false", "type": "BOOLEAN"}, "updatedAt": 
{"indexed": "false", "type": "DATE_TIME"}, "entityEntityRelCount": {"indexed": "false", "type": 
"INTEGER"}, "chunkNodeCount": {"indexed": "false", "type": "INTEGER"}, "fileName": {"indexed": 
"false", "type": "STRING"}, "communityRelCount": {"indexed": "false", "type": "INTEGER"}}, 
"relationships": {"DOCUMENTED_IN": {"direction": "in", "labels": ["Policy"], "properties": 
{"source": {"indexed": "false", "type": "STRING"}, "created_at": {"indexed": "false", "type": 
"DATE_TIME"}}}, "PART_OF": {"direction": "in", "labels": ["Chunk"]}, "HAS_CANCELLATION": 
{"direction": "in", "labels": ["Policy"], "properties": {"source": {"indexed": "false", "type":
"STRING"}, "created_at": {"indexed": "false", "type": "DATE_TIME"}}}, "HAS_ENDORSEMENT": 
{"direction": "in", "labels": ["Policy"], "properties": {"endorsement_type": {"indexed": 
"false", "type": "STRING"}, "source": {"indexed": "false", "type": "STRING"}, "created_at": 
{"indexed": "false", "type": "DATE_TIME"}}}, "HAS_DOC": {"direction": "in", "labels": 
["Customer"], "properties": {"created_at": {"indexed": "false", "type": "DATE_TIME"}}}, 
"FIRST_CHUNK": {"direction": "out", "labels": ["Chunk"]}}}, "HAS_INSURED_ITEM": {"type": 
"relationship", "count": 1782, "properties": {"created_at": {"indexed": "false", "type": 
"DATE_TIME"}}}, "Message": {"type": "node", "count": 320, "properties": {"content": 
{"indexed": "false", "type": "STRING"}, "createdAt": {"indexed": "false", "type": "DATE_TIME"}, 
"role": {"indexed": "false", "type": "STRING"}}, "relationships": {"NEXT": {"direction": "out",
"labels": ["Message", "Message"]}, "LAST_MESSAGE": {"direction": "in", "labels": 
["Session"]}}}, "NEXT_CHUNK": {"type": "relationship", "count": 99110}, "HAS_CANCELLATION": 
{"type": "relationship", "count": 7, "properties": {"source": {"indexed": "false", "type": 
"STRING"}, "created_at": {"indexed": "false", "type": "DATE_TIME"}}}, "HAS_ENDORSEMENT": 
{"type": "relationship", "count": 27, "properties": {"endorsement_type": {"indexed": "false", 
"type": "STRING"}, "source": {"indexed": "false", "type": "STRING"}, "created_at": {"indexed": 
"false", "type": "DATE_TIME"}}}, "HAS_DOC": {"type": "relationship", "count": 1926, 
"properties": {"created_at": {"indexed": "false", "type": "DATE_TIME"}}}, "FIRST_CHUNK": 
{"type": "relationship", "count": 2005}, "NEXT": {"type": "relationship", "count": 232}, 
"Policy": {"type": "node", "count": 1914, "properties": {"id": {"indexed": "false", "type": 
"STRING"}, "updatedAt": {"indexed": "false", "type": "DATE_TIME"}, "insuredItem": {"indexed": 
"false", "type": "STRING"}, "source_file": {"indexed": "false", "type": "STRING"}, "createdAt": 
{"indexed": "false", "type": "DATE_TIME"}, "name": {"indexed": "false", "type": "STRING"}, 
"year": {"indexed": "false", "type": "INTEGER"}, "policyNumber": {"indexed": "false", "type": 
"STRING"}, "extraction_method": {"indexed": "false", "type": "STRING"}, "type": {"indexed": 
"false", "type": "STRING"}, "customer": {"indexed": "false", "type": "STRING"}}, "relationships":
{"DOCUMENTED_IN": {"direction": "out", "labels": ["Document"], "properties": {"source": 
{"indexed": "false", "type": "STRING"}, "created_at": {"indexed": "false", "type": 
"DATE_TIME"}}}, "HAS_INSURED_ITEM": {"direction": "out", "labels": ["InsuredItem"], 
"properties": {"created_at": {"indexed": "false", "type": "DATE_TIME"}}}, "HAS_YEAR": 
{"direction": "out", "labels": ["PolicyYear"], "properties": {"created_at": {"indexed": 
"false", "type": "DATE_TIME"}}}, "HAS_CANCELLATION": {"direction": "out", "labels": 
["Document"], "properties": {"source": {"indexed": "false", "type": "STRING"}, "created_at": 
{"indexed": "false", "type": "DATE_TIME"}}}, "HAS_ENDORSEMENT": {"direction": "out", "labels": 
["Document"], "properties": {"endorsement_type": {"indexed": "false", "type": "STRING"}, 
"source": {"indexed": "false", "type": "STRING"}, "created_at": {"indexed": "false", "type": 
"DATE_TIME"}}}, "HAS_POLICY": {"direction": "in", "labels": ["Customer"], "properties": 
{"created_at": {"indexed": "false", "type": "DATE_TIME"}}}, "HAS_TYPE": {"direction": "out", 
"labels": ["PolicyType"], "properties": {"created_at": {"indexed": "false", "type": 
"DATE_TIME"}}}}}, "policy": {"type": "node", "count": 6, "properties": {"name": {"indexed": 
"false", "type": "STRING"}, "type": {"indexed": "false", "type": "STRING"}, "observations": 
{"indexed": "false", "type": "LIST"}}}, "HAS_POLICY": {"type": "relationship", "count": 1921, 
"properties": {"created_at": {"indexed": "false", "type": "DATE_TIME"}}}, "Chunk": {"type": 
"node", "count": 99838, "properties": {"position": {"indexed": "false", "type": "INTEGER"}, 
"id": {"indexed": "false", "type": "STRING"}, "text": {"indexed": "false", "type": "STRING"}, 
"content_offset": {"indexed": "false", "type": "INTEGER"}, "fileName": {"indexed": "false", 
"type": "STRING"}, "page_number": {"indexed": "false", "type": "INTEGER"}, "length": 
{"indexed": "false", "type": "INTEGER"}, "chunkId": {"indexed": "false", "type": "STRING"}, 
"embedding": {"indexed": "false", "type": "LIST"}, "page_link": {"indexed": "false", "type": 
"STRING"}}, "relationships": {"PART_OF": {"direction": "out", "labels": ["Document"]}, 
"NEXT_CHUNK": {"direction": "out", "labels": ["Chunk", "Chunk"]}, "FIRST_CHUNK": 
{"direction": "in", "labels": ["Document"]}}}}
    your_schema = [
  {
    "policyNumber": "65790193",
    "name": "Ayça Dinçkök Galata Residance D6 Konut_2020",
    "type": "Konut Sigortası",
    "id": "Ayça Dinçkök Galata Residance D6 Konut_2020"
  },
  {
    "policyNumber": "81045975",
    "name": "Sernur Çiftçi Büyükhanlı 1A D6 Konut_2020",
    "type": "Konut Sigortası",
    "id": "Sernur Çiftçi Büyükhanlı 1A D6 Konut_2020"
  },
  {
    "policyNumber": "81033531",
    "name": "Sernur Çiftçi Barclay 19 D6 Konut_2020",
    "type": "Konut Sigortası",
    "id": "Sernur Çiftçi Barclay 19 D6 Konut_2020"
  },
  {
    "policyNumber": "81032009",
    "name": "Samim Çiftçi CARLTON 17 D6 Konut_2020",
    "type": "Konut Sigortası",
    "id": "Samim Çiftçi CARLTON 17 D6 Konut_2020"
  },
  {
    "policyNumber": "81052885",
    "name": "Satvet Çiftçi Büyükhanlı 1B D6 Konut_2020",
    "type": "Konut Sigortası",
    "id": "Satvet Çiftçi Büyükhanlı 1B D6 Konut_2020"
  },
  {
    "policyNumber": "81061634",
    "name": "Satvet Çiftçi CARLTON8 D6 Konut_2020",
    "type": "Konut Sigortası",
    "id": "Satvet Çiftçi CARLTON8 D6 Konut_2020"
  },
  {
    "policyNumber": "81067545",
    "name": "Satvet Çiftçi Çiftçiler Apt. D6 Konut_2020",
    "type": "Konut Sigortası",
    "id": "Satvet Çiftçi Çiftçiler Apt. D6 Konut_2020"
  },
  {
    "policyNumber": "81026115",
    "name": "Sernur Çiftçi Barclay 5 D6 Konut_2020",
    "type": "Konut Sigortası",
    "id": "Sernur Çiftçi Barclay 5 D6 Konut_2020"
  }
]

    if your_schema and isinstance(your_schema, dict):
        # Orijinal şema boyutunu hesapla
        original_json = json.dumps(your_schema)
        original_size = len(original_json)
        
        # Minimal format'a çevir
        result = to_minimal_cypher_format(your_schema)
        
        print("=== KARŞILAŞTIRMA ===")
        print(f"Orijinal JSON: {original_size:,} karakter (~{original_size // 4:,} token)")
        print(f"Minimal Format: {len(result):,} karakter (~{len(result) // 4:,} token)")
        print(f"Token kazancı: %{100 - (len(result)/original_size*100):.1f}")
        print()
        print("=== MİNİMAL FORMAT SONUCU ===")
        print(result)
    else:
        print("your_schema değişkenine JSON şemanızı yazın ve tekrar çalıştırın.")