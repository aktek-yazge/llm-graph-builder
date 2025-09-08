#!/usr/bin/env python3
"""
Chunk analizi test scripti
"""

import os
import sys
from pathlib import Path

# Backend path'ini ekle
backend_path = Path(__file__).parent / "backend"
sys.path.insert(0, str(backend_path))

from src.intelligent_agent import IntelligentAgent

def test_chunk_analysis():
    """Chunk analizini test et"""
    print("🧪 Chunk Analysis Test")
    
    # Mock chunk data (senin örneğinden)
    mock_results = [
        {
            'node.text': """Önemli Uyarı: Adres ve diğer iletişim bilgileri sigortalının beyanı doğrultusunda fiyat çalışması/ poliçeye yazılmış olup sigortacı tüm yazışma ve diğer iletişiminde bu bilgileri kullanacaktır. Sigortacının sorumluluğu; sigorta priminin peşin ödenmesi kararlaştırılmış ise tamamının, taksitle ödenmesi kararlaştırılmış ise ilk taksitinin ödenmesi ile başlar. Prim ödeme planında belirtilen vadeler kesin olup prim taksitlerinden birinin ödenmemesi temerrüdü doğurur.

Temerrüt halinde TTK'nin 1434.maddesi hükümleri geçerlidir. Sigortalı yada menfaat sahibi olan kişiler Sigortacılık kanunu kapsamında, 14.02.2011 tarih ve (2011/5) sayılı genelgede belirtilen usuller çerçevesinde talep etmeleri halinde -Eksper Atama ve Takip Sistemi - üzerinden eksper atayabilirler.

---""",
            'score': 0.9269256591796875,
            'node.chunkId': 'chunk_001',
            'node.page_number': 1,
            'node.position': 5
        },
        {
            'node.text': """| Teminat Adı               | Sigorta Bedeli TL   | Prim Bilgileri   | Tutar TL   |
|---------------------------|---------------------|------------------|------------|
| BİNA                      | 350,000.00          | Net Prim         | 559.08     |
| YANGIN MALİ SORUMLULUK    | 350,000.00          | YSV              | 7.35       |
| ENKAZ KALDIRMA MASRAFLARI | 14,000.00           | Gider Vergisi    | 27.95      |
| YER KAYMASI               | 350,000.00          | Brüt Prim        | 594.38     |
| KAR AĞIRLIĞI & FIRTINA    | 350,000.00          |                  |            |
| EK TEMİNATLAR             | 350,000.00          |                  |            |
| SEL / SU BASKINI          | 350,000.00          | Taksit Tarih     | Tutar TL   |
| DOLU                      | 5,000.00            |                  |            |
| YAKIT SIZINTISI           | 5,000.00            | P 12.02.2020     | 149.38     |""",
            'score': 0.921234130859375,
            'node.chunkId': 'chunk_002',
            'node.page_number': 1,
            'node.position': 6
        }
    ]
    
    print("\n📊 Mock Cypher Results Analysis:")
    for i, row in enumerate(mock_results):
        text_content = row.get('node.text', '')
        score = row.get('score', 'N/A')
        chunk_id = row.get('node.chunkId', 'N/A')
        page_number = row.get('node.page_number', 'N/A')
        position = row.get('node.position', 'N/A')
        
        print(f"📄 Chunk {i+1}:")
        print(f"   📊 Chunk ID: {chunk_id}")
        print(f"   📄 Sayfa: {page_number}")
        print(f"   🎯 Position: {position}")
        print(f"   📈 Score: {score}")
        print(f"   💬 Text: {text_content[:100]}...")
        
        # Eksik bilgi analizi
        if text_content.endswith('|') or text_content.endswith('-') or 'P 12.02.2020' in text_content:
            print(f"   ⚠️  Bu chunk'ta bilgi eksik kalmış olabilir - sonraki chunk'lara bakılmalı")
            print(f"   🔍 Önerilen action: position {position+1}, {position+2} chunk'larını getir")
        
        print("   " + "="*60)
    
    # LLM'in yapması gereken analiz
    print("\n🤖 LLM Analysis Simulation:")
    print("Observation: 2 chunk bulundu. 2. chunk'ta taksit tablosu var ama 'P 12.02.2020 | 149.38' ile bitiyor.")
    print("Thought: Taksit tablosu yarıda kesmış, devamında daha fazla taksit bilgisi olabilir.")
    print("Action: cypher_query")
    print("Content: MATCH (c:Chunk)-[:PART_OF]->(d:Document)")
    print("         WHERE d.fileName = 'Ayça Dinçkök Galata Residance D4 Konut 2020.pdf'")
    print("         AND c.position IN [7, 8]")
    print("         RETURN c.text, c.chunkId, c.page_number, c.position")

if __name__ == "__main__":
    print("🚀 Chunk Analysis Test")
    print("=" * 50)
    
    test_chunk_analysis()
    
    print("\n✅ Test completed!")
