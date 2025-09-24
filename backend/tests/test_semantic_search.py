#!/usr/bin/env python3
"""
🧠 Enhanced Intelligent Test Agent for Semantic Search Testing

Bu script semantic search özelliğini test etmek için özel tasarlanmıştır.
"""

import os
import sys
import json
import logging
from typing import Dict, List, Tuple, Optional, Any
from datetime import datetime
import neo4j
from neo4j import GraphDatabase
from dotenv import load_dotenv
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
import openai
from openai import OpenAI

# Load environment variables
load_dotenv()

# Import project modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from src.graph_query import get_graphDB_driver

# Import the enhanced intelligent agent
from intelligent_test_agent import IntelligentTestAgent, serialize_neo4j_objects

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='🤖 %(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

def test_semantic_search():
    """Test the Semantic Search functionality specifically"""
    logger.info("🧪 Semantic Search Test başlıyor...")
    
    # Test questions designed to trigger semantic search (after keyword fails)
    semantic_test_questions = [
        "Tekne ile ilgili kapsamlı bilgi ver",
        "Sigorta kapsamında neler var?", 
        "Bu veritabanında hangi bilgiler bulunuyor?",
        "Finansal koruma nedir?",
        "Deniz araçları hakkında neler biliyorsun?"
    ]
    
    agent = IntelligentTestAgent()
    
    try:
        for i, question in enumerate(semantic_test_questions, 1):
            logger.info(f"\n" + "="*60)
            logger.info(f"🔍 Semantic Test {i}: {question}")
            logger.info("="*60)
            
            result = agent.search_for_answer(question)
            
            # Check which step found the answer
            steps_used = len(result.get('steps', []))
            final_answer = result.get('final_answer', 'Cevap yok')
            confidence = result.get('confidence', 0.0)
            
            # Analyze which step was successful
            successful_step = None
            for step in result.get('steps', []):
                if step.get('answer_found'):
                    successful_step = step.get('step')
                    break
            
            logger.info(f"✅ Cevap bulundu: {final_answer[:100]}...")
            logger.info(f"📊 Güven skoru: {confidence:.2f}")
            logger.info(f"🔧 Toplam adım sayısı: {steps_used}")
            logger.info(f"🎯 Başarılı adım: {successful_step}")
            
            # Specifically check if semantic search (step 3) was used
            if steps_used >= 3:
                step3_result = result['steps'][2]  # 0-indexed, so step 3 is index 2
                if step3_result.get('name') == 'Semantic Search':
                    semantic_matches = step3_result.get('data', {}).get('semantic_matches', [])
                    logger.info(f"🔮 Semantic matches bulundu: {len(semantic_matches)}")
                    if semantic_matches:
                        top_match = semantic_matches[0]
                        logger.info(f"🏆 En iyi semantic match: {top_match.get('similarity_score', 0):.3f}")
            
            # Save detailed results 
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            result_file = f"semantic_test_{i}_{timestamp}.json"
            
            # Serialize Neo4j objects before saving
            serialized_result = serialize_neo4j_objects(result)
            
            with open(result_file, 'w', encoding='utf-8') as f:
                json.dump(serialized_result, f, ensure_ascii=False, indent=2)
            logger.info(f"📁 Detaylı sonuç kaydedildi: {result_file}")
            
    finally:
        agent.close()
    
    logger.info("🎉 Semantic Search Test tamamlandı!")

if __name__ == "__main__":
    test_semantic_search()
