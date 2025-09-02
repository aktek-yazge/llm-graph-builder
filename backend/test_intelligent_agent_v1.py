#!/usr/bin/env python3
"""
Intelligent Agent v1 Test Script
Bu script intelligent_agent.py'nin performansını test eder ve analiz eder.
"""

import time
import logging
from datetime import datetime
import json
import sys
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add the backend directory to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))

# Import the intelligent agent
from intelligent_agent import IntelligentAgent
from langchain_neo4j import Neo4jGraph

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

class IntelligentAgentV1Tester:
    """Test suite for Intelligent Agent v1"""
    
    def __init__(self):
        self.agent = None
        self.test_results = []
        self.total_tokens = 0
        self.total_time = 0
        
    def setup_agent(self):
        """Initialize the intelligent agent"""
        try:
            logging.info("🚀 Intelligent Agent v1 başlatılıyor...")
            
            # Neo4j Graph connection
            graph = Neo4jGraph(
                url=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
                username=os.getenv("NEO4J_USERNAME", "neo4j"),
                password=os.getenv("NEO4J_PASSWORD", "password")
            )
            
            # Initialize agent with graph
            self.agent = IntelligentAgent(graph)
            logging.info("✅ Agent başarıyla başlatıldı!")
            return True
        except Exception as e:
            logging.error(f"❌ Agent başlatılamadı: {e}")
            return False
    
    def run_test_question(self, question: str, test_name: str) -> dict:
        """Run a single test question and collect metrics"""
        print(f"\n{'='*80}")
        print(f"TEST: {test_name}")
        print(f"SORU: {question}")
        print(f"{'='*80}")
        
        start_time = time.time()
        
        try:
            # Run the question
            result = self.agent.solve_question(question)
            
            end_time = time.time()
            duration = end_time - start_time
            
            # Extract metrics from result if available
            iterations = result.get('iterations', 0)
            total_chunks = result.get('discovered_chunks', 0)
            total_entities = result.get('discovered_entities', 0)
            token_usage = result.get('token_usage', {}).get('total_tokens', 0)
            chunk_details = result.get('chunk_details', [])
            entity_details = result.get('entity_details', [])
            successful_findings = result.get('successful_findings', [])
            llm_prompt_structure = result.get('llm_prompt_structure', '')
            
            # Analyze answer quality
            answer_analysis = self.analyze_answer_quality(question, chunk_details, entity_details, successful_findings)
            
            # Create comprehensive answer text
            answer_text = self.create_comprehensive_answer(result, answer_analysis)
            
            # Create test result
            test_result = {
                'test_name': test_name,
                'question': question,
                'success': True,
                'duration': duration,
                'iterations': iterations,
                'total_chunks': total_chunks,
                'total_entities': total_entities,
                'token_usage': token_usage,
                'chunk_details': chunk_details,
                'entity_details': entity_details,
                'successful_findings': successful_findings,
                'answer_analysis': answer_analysis,
                'answer': answer_text,
                'llm_prompt_structure': llm_prompt_structure,
                'timestamp': datetime.now().isoformat()
            }
            
            # Print results
            print(f"\n📊 SONUÇLAR:")
            print(f"✅ Başarılı: Evet")
            print(f"⏱️  Süre: {duration:.2f} saniye")
            print(f"🔄 İterasyon: {iterations}")
            print(f"📝 Chunk sayısı: {total_chunks}")
            print(f"🏷️  Entity sayısı: {total_entities}")
            print(f"🎯 Token kullanımı: {token_usage}")
            
            # Print detailed findings
            if successful_findings:
                print(f"\n🔍 BAŞARILI BULGULAR ({len(successful_findings)}):")
                for i, finding in enumerate(successful_findings[:3], 1):  # İlk 3 bulguyu göster
                    print(f"   {i}. {finding.get('description', 'N/A')} (Relevance: {finding.get('relevance', 0):.3f})")
            
            # Print chunk details
            if chunk_details:
                print(f"\n📄 BULUNAN CHUNK'LAR:")
                for i, chunk in enumerate(chunk_details[:3], 1):  # İlk 3 chunk'ı göster
                    print(f"   {i}. Dokuman: {chunk.get('document', 'N/A')}")
                    print(f"      Sayfa: {chunk.get('page', 'N/A')}, Relevance: {chunk.get('relevance', 0):.3f}")
                    print(f"      Özet: {chunk.get('preview', 'N/A')[:100]}...")
            
            # Print entity details  
            if entity_details:
                print(f"\n🏷️  BULUNAN ENTİTY'LER:")
                for i, entity in enumerate(entity_details[:3], 1):  # İlk 3 entity'yi göster
                    print(f"   {i}. ID: {entity.get('id', 'N/A')}")
                    print(f"      Type: {entity.get('type', 'N/A')}")
                    print(f"      Labels: {entity.get('labels', [])}")
            
            # Print answer analysis
            print(f"\n📋 CEVAP ANALİZİ:")
            print(f"   📊 Kalite Skoru: {answer_analysis['quality_score']:.2f}/10")
            print(f"   🎯 Relevance: {answer_analysis['relevance_score']:.2f}/10") 
            print(f"   📝 Completeness: {answer_analysis['completeness_score']:.2f}/10")
            print(f"   💡 Bulgular: {answer_analysis['findings_summary']}")
            
            print(f"\n📄 DETAY CEVAP:\n{test_result['answer']}")
            
            # Update totals
            self.total_tokens += token_usage
            self.total_time += duration
            
        except Exception as e:
            end_time = time.time()
            duration = end_time - start_time
            
            test_result = {
                'test_name': test_name,
                'question': question,
                'success': False,
                'duration': duration,
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }
            
            print(f"\n❌ HATA:")
            print(f"⏱️  Süre: {duration:.2f} saniye")
            print(f"🚨 Hata mesajı: {str(e)}")
            
            self.total_time += duration
        
        self.test_results.append(test_result)
        return test_result
    
    def analyze_answer_quality(self, question: str, chunk_details: list, entity_details: list, successful_findings: list) -> dict:
        """Analyze the quality and relevance of the answer"""
        
        # Quality score based on findings
        quality_score = 0
        relevance_score = 0
        completeness_score = 0
        
        # Base score calculation
        if chunk_details:
            quality_score += min(len(chunk_details) * 2, 6)  # Max 6 points for chunks
            avg_relevance = sum(c.get('relevance', 0) for c in chunk_details) / len(chunk_details)
            relevance_score += avg_relevance * 10  # Convert to 0-10 scale
        
        if entity_details:
            quality_score += min(len(entity_details) * 1, 4)  # Max 4 points for entities
        
        # Question-specific scoring
        question_lower = question.lower()
        findings_summary = ""
        
        if "kaç" in question_lower and ("poliç" in question_lower):
            # Count-based questions
            policy_chunks = [c for c in chunk_details if "poliç" in c.get('document', '').lower()]
            if policy_chunks:
                completeness_score = min(len(policy_chunks) * 3, 10)
                findings_summary = f"{len(policy_chunks)} poliçe belgesi bulundu"
            else:
                completeness_score = 1
                findings_summary = "Poliçe sayısı belirlenemedi"
                
        elif "hangi" in question_lower and "tür" in question_lower:
            # Type-based questions  
            doc_types = set()
            for chunk in chunk_details:
                doc_name = chunk.get('document', '').lower()
                if 'dask' in doc_name:
                    doc_types.add('DASK')
                if 'tekne' in doc_name:
                    doc_types.add('Tekne Sigortası')
                if 'konut' in doc_name:
                    doc_types.add('Konut Sigortası')
            
            if doc_types:
                completeness_score = min(len(doc_types) * 3, 10)
                findings_summary = f"Bulunan türler: {', '.join(doc_types)}"
            else:
                completeness_score = 1
                findings_summary = "Poliçe türleri belirlenemedi"
                
        elif "prim" in question_lower or "kadar" in question_lower:
            # Premium amount questions
            premium_chunks = [c for c in chunk_details if any(keyword in c.get('preview', '').lower() 
                            for keyword in ['prim', 'ücret', 'tutar', 'tl', '₺'])]
            if premium_chunks:
                completeness_score = min(len(premium_chunks) * 4, 10)
                findings_summary = f"{len(premium_chunks)} prim bilgisi bulundu"
            else:
                completeness_score = 1
                findings_summary = "Prim bilgisi bulunamadı"
                
        elif "taksit" in question_lower:
            # Installment questions
            installment_chunks = [c for c in chunk_details if any(keyword in c.get('preview', '').lower() 
                                for keyword in ['taksit', 'ödeme', 'plan'])]
            if installment_chunks:
                completeness_score = min(len(installment_chunks) * 4, 10)
                findings_summary = f"{len(installment_chunks)} taksit bilgisi bulundu"
            else:
                completeness_score = 1
                findings_summary = "Taksit bilgisi bulunamadı"
                
        elif "sayfa" in question_lower or "link" in question_lower:
            # Document info questions
            doc_chunks = [c for c in chunk_details if c.get('page') is not None]
            if doc_chunks:
                max_page = max(c.get('page', 0) for c in doc_chunks)
                completeness_score = min(max_page, 10) if max_page else 1
                findings_summary = f"Maksimum sayfa: {max_page}" if max_page else "Sayfa bilgisi bulunamadı"
            else:
                completeness_score = 1
                findings_summary = "Belge bilgisi bulunamadı"
        else:
            # Generic scoring
            completeness_score = min(len(chunk_details) + len(entity_details), 10)
            findings_summary = f"{len(chunk_details)} chunk, {len(entity_details)} entity bulundu"
        
        return {
            'quality_score': min(quality_score, 10),
            'relevance_score': min(relevance_score, 10), 
            'completeness_score': min(completeness_score, 10),
            'findings_summary': findings_summary,
            'total_findings': len(successful_findings),
            'has_relevant_data': len(chunk_details) > 0 or len(entity_details) > 0
        }
    
    def create_comprehensive_answer(self, result: dict, answer_analysis: dict) -> str:
        """Create a comprehensive answer text based on findings"""
        
        chunk_details = result.get('chunk_details', [])
        entity_details = result.get('entity_details', [])
        successful_findings = result.get('successful_findings', [])
        
        if not chunk_details and not entity_details:
            return f"❌ Soru için yeterli veri bulunamadı. {answer_analysis['findings_summary']}"
        
        answer_parts = []
        
        # Main findings summary
        answer_parts.append(f"🔍 {answer_analysis['findings_summary']}")
        
        # Chunk-based information
        if chunk_details:
            answer_parts.append(f"\n📄 BELGE BİLGİLERİ:")
            for i, chunk in enumerate(chunk_details[:3], 1):
                doc_name = chunk.get('document', 'Bilinmeyen')
                page = chunk.get('page', 'N/A')
                relevance = chunk.get('relevance', 0)
                preview = chunk.get('preview', '')[:150]
                
                answer_parts.append(f"   {i}. Belge: {doc_name}")
                answer_parts.append(f"      Sayfa: {page}, Relevance: {relevance:.3f}")
                answer_parts.append(f"      İçerik: {preview}...")
        
        # Entity-based information
        if entity_details:
            answer_parts.append(f"\n🏷️ ENTİTY BİLGİLERİ:")
            for i, entity in enumerate(entity_details[:3], 1):
                entity_id = entity.get('id', 'N/A')
                entity_type = entity.get('type', 'N/A')
                labels = entity.get('labels', [])
                
                answer_parts.append(f"   {i}. ID: {entity_id}")
                answer_parts.append(f"      Type: {entity_type}, Labels: {labels}")
        
        # Quality assessment
        quality = answer_analysis['quality_score']
        if quality >= 7:
            confidence = "Yüksek güvenilirlik"
        elif quality >= 4:
            confidence = "Orta güvenilirlik"
        else:
            confidence = "Düşük güvenilirlik"
            
        answer_parts.append(f"\n📊 CEVAP KALİTESİ: {confidence} ({quality:.1f}/10)")
        
        return "\n".join(answer_parts)
    
    def run_all_tests(self):
        """Run all predefined test questions"""
        test_questions = [
            {
                'question': 'Ahmet beyin kaç poliçesi var?',
                'name': 'Ahmet Poliçe Sayısı'
            },
            {
                'question': 'Hangi poliçe türleri var?',
                'name': 'Poliçe Türleri Listesi'
            },
            {
                'question': 'Ahmet beyin 2020 kiraz tekne poliçesi primi ne kadar?',
                'name': 'Kiraz Tekne Poliçe Primi'
            },
            {
                'question': 'Ahmet beyin kiraz tekne poliçesinin taksitleri ne kadar?',
                'name': 'Kiraz Tekne Taksit Bilgisi'
            },
            {
                'question': 'Bu poliçe kaç sayfadan oluşuyor? Belgenin linkini alabilir miyim?',
                'name': 'Belge Sayfa Sayısı ve Link'
            }
        ]
        
        print(f"\n🧪 INTELLIGENT AGENT V1 TEST BAŞLIYOR")
        print(f"📅 Test Tarihi: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"🔢 Toplam Test Sayısı: {len(test_questions)}")
        
        # Run each test
        for i, test in enumerate(test_questions, 1):
            print(f"\n🔄 Test {i}/{len(test_questions)} başlıyor...")
            self.run_test_question(test['question'], test['name'])
            
            # Small delay between tests
            if i < len(test_questions):
                print(f"\n⏳ Sonraki test için 2 saniye bekleniyor...")
                time.sleep(2)
    
    def print_summary(self):
        """Print test summary and analytics"""
        print(f"\n{'='*80}")
        print(f"📊 INTELLIGENT AGENT V1 TEST RAPORU")
        print(f"{'='*80}")
        
        successful_tests = [t for t in self.test_results if t.get('success', False)]
        failed_tests = [t for t in self.test_results if not t.get('success', False)]
        
        print(f"\n📈 GENEL İSTATİSTİKLER:")
        print(f"✅ Başarılı Test: {len(successful_tests)}/{len(self.test_results)}")
        print(f"❌ Başarısız Test: {len(failed_tests)}/{len(self.test_results)}")
        print(f"⏱️  Toplam Süre: {self.total_time:.2f} saniye")
        print(f"🎯 Toplam Token: {self.total_tokens}")
        print(f"📊 Ortalama Test Süresi: {self.total_time/len(self.test_results):.2f} saniye")
        
        if successful_tests:
            avg_iterations = sum(t.get('iterations', 0) for t in successful_tests) / len(successful_tests)
            avg_chunks = sum(t.get('total_chunks', 0) for t in successful_tests) / len(successful_tests)
            avg_entities = sum(t.get('total_entities', 0) for t in successful_tests) / len(successful_tests)
            avg_tokens = sum(t.get('token_usage', 0) for t in successful_tests) / len(successful_tests)
            avg_quality = sum(t.get('answer_analysis', {}).get('quality_score', 0) for t in successful_tests) / len(successful_tests)
            avg_relevance = sum(t.get('answer_analysis', {}).get('relevance_score', 0) for t in successful_tests) / len(successful_tests)
            avg_completeness = sum(t.get('answer_analysis', {}).get('completeness_score', 0) for t in successful_tests) / len(successful_tests)
            
            print(f"\n📊 BAŞARILI TESTLER İÇİN ORTALAMALAR:")
            print(f"🔄 Ortalama İterasyon: {avg_iterations:.1f}")
            print(f"📝 Ortalama Chunk: {avg_chunks:.1f}")
            print(f"🏷️  Ortalama Entity: {avg_entities:.1f}")
            print(f"🎯 Ortalama Token: {avg_tokens:.1f}")
            print(f"📊 Ortalama Kalite Skoru: {avg_quality:.1f}/10")
            print(f"🎯 Ortalama Relevance: {avg_relevance:.1f}/10")
            print(f"📝 Ortalama Completeness: {avg_completeness:.1f}/10")
        
        print(f"\n📋 TEST DETAYLARI:")
        for i, test in enumerate(self.test_results, 1):
            status = "✅" if test.get('success', False) else "❌"
            duration = test.get('duration', 0)
            quality = test.get('answer_analysis', {}).get('quality_score', 0)
            chunks = test.get('total_chunks', 0)
            entities = test.get('total_entities', 0)
            print(f"{status} Test {i}: {test['test_name']} ({duration:.2f}s)")
            print(f"   📊 Kalite: {quality:.1f}/10, Chunk: {chunks}, Entity: {entities}")
            
        # Detailed answer analysis
        print(f"\n🔍 CEVAP KALİTE ANALİZİ:")
        high_quality_tests = [t for t in successful_tests if t.get('answer_analysis', {}).get('quality_score', 0) >= 7]
        medium_quality_tests = [t for t in successful_tests if 4 <= t.get('answer_analysis', {}).get('quality_score', 0) < 7]
        low_quality_tests = [t for t in successful_tests if t.get('answer_analysis', {}).get('quality_score', 0) < 4]
        
        print(f"🟢 Yüksek Kalite (≥7): {len(high_quality_tests)} test")
        print(f"🟡 Orta Kalite (4-6.9): {len(medium_quality_tests)} test")
        print(f"🔴 Düşük Kalite (<4): {len(low_quality_tests)} test")
        
        if failed_tests:
            print(f"\n🚨 BAŞARISIZ TESTLER:")
            for test in failed_tests:
                print(f"❌ {test['test_name']}: {test.get('error', 'Bilinmeyen hata')}")
    
    def save_results(self, filename: str = None):
        """Save test results to JSON file"""
        if filename is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"intelligent_agent_v1_test_results_{timestamp}.json"
        
        filepath = os.path.join(os.path.dirname(__file__), filename)
        
        test_report = {
            'test_timestamp': datetime.now().isoformat(),
            'agent_version': 'intelligent_agent_v1',
            'total_tests': len(self.test_results),
            'successful_tests': len([t for t in self.test_results if t.get('success', False)]),
            'total_duration': self.total_time,
            'total_tokens': self.total_tokens,
            'test_results': self.test_results
        }
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(test_report, f, ensure_ascii=False, indent=2)
            print(f"\n💾 Test sonuçları kaydedildi: {filepath}")
        except Exception as e:
            print(f"\n❌ Test sonuçları kaydedilemedi: {e}")

def main():
    """Main test function"""
    print("🧪 INTELLIGENT AGENT V1 TEST SUITE")
    print("=" * 80)
    
    tester = IntelligentAgentV1Tester()
    
    # Setup agent
    if not tester.setup_agent():
        print("❌ Agent başlatılamadığı için testler durduruluyor.")
        return
    
    try:
        # Run all tests
        tester.run_all_tests()
        
        # Print summary
        tester.print_summary()
        
        # Save results
        tester.save_results()
        
    except KeyboardInterrupt:
        print("\n\n⚠️ Testler kullanıcı tarafından durduruldu.")
        tester.print_summary()
    except Exception as e:
        print(f"\n❌ Test sürecinde beklenmeyen hata: {e}")
        tester.print_summary()
    
    print(f"\n🏁 Test tamamlandı!")

if __name__ == "__main__":
    main()
