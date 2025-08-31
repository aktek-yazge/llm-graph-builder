#!/usr/bin/env python3
"""
Test endpoint logları üretmek için basit script
"""
import json
import os
from datetime import datetime, timezone

def generate_test_logs():
    """Test endpoint logları üret"""
    
    # Test logları
    log_entries = [
        {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'level': 'INFO', 
            'message': 'PRINT: 🌐 HTTP POST /extract_entities başladı',
            'component': 'backend',
            'operation': 'endpoint_test',
            'logger_name': 'print_capture',
            'service_name': 'llm-graph-builder'
        },
        {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'level': 'INFO',
            'message': 'PRINT: 📤 Upload started: test_policy.pdf', 
            'component': 'backend',
            'operation': 'endpoint_test',
            'logger_name': 'print_capture',
            'service_name': 'llm-graph-builder'
        },
        {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'level': 'INFO',
            'message': 'PRINT: 🚀 LLM Graph Transformer process_response başlıyor',
            'component': 'backend', 
            'operation': 'endpoint_test',
            'logger_name': 'print_capture',
            'service_name': 'llm-graph-builder'
        },
        {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'level': 'INFO',
            'message': 'PRINT: 📝 LLM entity extraction input text uzunluğu: 2500 karakter',
            'component': 'backend',
            'operation': 'endpoint_test', 
            'logger_name': 'print_capture',
            'service_name': 'llm-graph-builder'
        },
        {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'level': 'INFO',
            'message': 'PRINT: 🎯 LLM entity extraction allowed nodes: [Policy, Customer, InsuredItem]',
            'component': 'backend',
            'operation': 'endpoint_test',
            'logger_name': 'print_capture', 
            'service_name': 'llm-graph-builder'
        },
        {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'level': 'INFO',
            'message': 'PRINT: 🔗 LLM entity extraction final relationship tipleri: [HAS_POLICY, COVERS]',
            'component': 'backend',
            'operation': 'endpoint_test',
            'logger_name': 'print_capture',
            'service_name': 'llm-graph-builder'
        },
        {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'level': 'INFO',
            'message': 'PRINT: ✅ LLM entity extraction final sonuç - Entityler: 8, Relationshipler: 5',
            'component': 'backend',
            'operation': 'endpoint_test',
            'logger_name': 'print_capture',
            'service_name': 'llm-graph-builder'
        },
        {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'level': 'INFO',
            'message': 'PRINT: 💾 Saving chunk başarılı: chunk_001',
            'component': 'backend',
            'operation': 'endpoint_test',
            'logger_name': 'print_capture',
            'service_name': 'llm-graph-builder'
        },
        {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'level': 'INFO',
            'message': 'PRINT: 🔄 Graph extraction tamamlandı: 3.2 saniye',
            'component': 'backend',
            'operation': 'endpoint_test',
            'logger_name': 'print_capture',
            'service_name': 'llm-graph-builder'
        },
        {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'level': 'INFO',
            'message': 'PRINT: 🌐 HTTP POST /extract_entities - 200 OK (3.5s)',
            'component': 'backend',
            'operation': 'endpoint_test',
            'logger_name': 'print_capture',
            'service_name': 'llm-graph-builder'
        },
        {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'level': 'WARNING',
            'message': '⚠️ Test warning: High token usage detected',
            'component': 'backend',
            'operation': 'endpoint_test',
            'logger_name': 'test_logger',
            'service_name': 'llm-graph-builder'
        },
        {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'level': 'ERROR',
            'message': '❌ Test error: Connection timeout - retrying',
            'component': 'backend',
            'operation': 'endpoint_test',
            'logger_name': 'test_logger',
            'service_name': 'llm-graph-builder'
        }
    ]
    
    # JSONL dosyasına yaz
    log_file = 'logs/simple-logs.jsonl'
    
    with open(log_file, 'a') as f:
        for entry in log_entries:
            f.write(json.dumps(entry) + '\n')
    
    print(f'✅ Test endpoint logları {log_file} dosyasına eklendi!')
    print(f'📝 {len(log_entries)} adet log eklendi')
    return len(log_entries)

if __name__ == "__main__":
    generate_test_logs()
