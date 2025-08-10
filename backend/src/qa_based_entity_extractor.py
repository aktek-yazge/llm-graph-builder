"""
Soru-Cevap Tabanlı Entity Çıkarma Modülü
Bu modül, LLM'ye belgeden çıkarılabilecek soru ve cevapları vererek
daha odaklı ve alakalı entity'ler ile ilişkiler çıkarmasını sağlar.
"""

import logging
import json
from typing import List, Dict, Any, Optional, Tuple
from langchain_core.prompts import ChatPromptTemplate
from langchain_experimental.graph_transformers.llm import GraphDocument, Node, Relationship
from langchain_community.graphs.graph_document import Document
from src.llm import get_llm

logging.basicConfig(format='%(asctime)s - %(message)s', level='INFO')


class QABasedEntityExtractor:
    """
    Soru-cevap tabanlı entity çıkarma sınıfı
    """
    
    def __init__(self, model_name: str = "gpt-4o-mini"):
        self.model_name = model_name
        self.llm, _ = get_llm(model_name)
        
        # Genel soru kategorileri
        self.default_questions = {
            "identity_questions": [
                "Bu belgede hangi kişiler, firmalar veya kuruluşlar geçmektedir?",
                "Kimlik numaraları, telefon numaraları, e-mail adresleri nelerdir?",
                "Hangi adresler belirtilmiştir?",
                "Acente bilgileri nelerdir?",
                "Sigorta şirketi hangisidir?"
            ],
            "temporal_questions": [
                "Hangi tarihler, yıllar veya zaman aralıkları belirtilmiştir?",
                "Başlangıç ve bitiş tarihleri nelerdir?",
                "Tanzim tarihi nedir?",
                "Poliçe geçerlilik süresi ne kadardır?",
                "Taksit ödemeleri hangi tarihlerde yapılacaktır?"
            ],
            "financial_questions": [
                "Hangi miktarlar, ücretler, primler veya finansal değerler belirtilmiştir?",
                "Para birimi bilgileri nelerdir?",
                "Sigorta bedelleri nelerdir?",
                "Teminat tutarları nelerdir?",
                "Prim bilgileri (net prim, brüt prim, vergiler) nelerdir?",
                "Taksit tutarları nelerdir?"
            ],
            "location_questions": [
                "Hangi yerler, şehirler, ilçeler veya lokasyonlar geçmektedir?",
                "Riziko adresi nerededir?",
                "Bina özellikleri nelerdir (m², kat, daire vb.)?",
                "UAVT kodları nelerdir?"
            ],
            "technical_questions": [
                "Hangi teknik özellikler, kodlar veya numaralar bulunmaktadır?",
                "Poliçe numaraları nelerdir?",
                "Acente kodları nelerdir?",
                "Yapı tarzı nasıldır?",
                "Deprem ve diğer risk oranları nelerdir?"
            ],
            "coverage_questions": [
                "Hangi teminatlar sağlanmaktadır?",
                "Teminat kapsamları ve limitleri nelerdir?",
                "Muafiyet miktarları nelerdir?",
                "Hariç tutulan durumlar nelerdir?",
                "Ek hizmetler nelerdir?"
            ]
        }
        
    async def extract_entities_from_qa(self, 
                                document_chunks: List[str], 
                                file_name: str,
                                custom_questions: Optional[Dict[str, List[str]]] = None) -> List[GraphDocument]:
        """
        Soru-cevap tabanlı entity çıkarma ana fonksiyonu
        
        Args:
            document_chunks: Belge parçaları
            file_name: Dosya adı
            custom_questions: Özel sorular (opsiyonel)
            
        Returns:
            GraphDocument listesi
        """
        logging.info(f"QA tabanlı entity çıkarma başlıyor: {file_name}")
        
        # Soruları belirle
        questions_to_use = custom_questions if custom_questions else self.default_questions
        
        # Her chunk için QA tabanlı çıkarma yap
        all_graph_documents = []
        
        for chunk_index, chunk_text in enumerate(document_chunks):
            logging.info(f"Chunk {chunk_index + 1}/{len(document_chunks)} işleniyor")
            
            # Chunk için soru-cevap çiftleri oluştur
            qa_pairs = await self._generate_qa_pairs(chunk_text, questions_to_use)
            
            # QA çiftlerinden entity'leri çıkar
            graph_doc = await self._extract_entities_from_qa_pairs(
                chunk_text, qa_pairs, file_name, chunk_index
            )
            
            if graph_doc:
                all_graph_documents.append(graph_doc)
                
        logging.info(f"QA tabanlı çıkarma tamamlandı: {len(all_graph_documents)} GraphDocument oluşturuldu")
        return all_graph_documents
    
    async def _generate_qa_pairs(self, chunk_text: str, questions: Dict[str, List[str]]) -> List[Dict[str, str]]:
        """
        Verilen chunk için soru-cevap çiftleri oluşturur
        """
        
        # Soru-cevap oluşturma prompt'u
        qa_generation_prompt = ChatPromptTemplate.from_template("""
Sen bir sigorta belgesi analiz uzmanısın. Aşağıdaki metin parçasını analiz ederek, verilen sorulara kısa ve net cevaplar ver.

METIN:
{chunk_text}

SORULAR:
{formatted_questions}

ÖNEMLİ KURALLAR:
1. Sadece metinde açıkça geçen bilgileri kullan
2. Eğer bir sorunun cevabı metinde yoksa "Bilgi yok" yaz
3. Her cevabı kısa ve öz tut (maksimum 2-3 cümle)
4. Tarih, numara, isim, tutar gibi spesifik bilgileri aynen yaz
5. Para birimi olan tutarlarda birimi de belirt (TL, USD vb.)
6. Yüzde oranları varsa % işareti ile birlikte yaz
7. Adres bilgilerini tam olarak yaz
8. Telefon numaralarını tam format ile yaz
9. Poliçe, TC kimlik, UAVT gibi özel kodları tam olarak yaz
10. Cevapları JSON formatında ver

ÖRNEK CEVAPLAR:
- Poliçe numarası: "65789885"
- Sigorta bedeli: "350.000,00 TL"
- Başlangıç tarihi: "12.02.2020"
- Adres: "Firuza Ağa Apt: Galata Residence (17-19) Apt No: 17-19 Daire No: 3"
- Telefon: "0532****112"

JSON FORMAT:
{{
    "qa_pairs": [
        {{
            "question": "soru metni",
            "answer": "cevap metni",
            "category": "kategori_adı"
        }}
    ]
}}
""")                # Soruları formatla
        formatted_questions = ""
        for category, question_list in questions.items():
            formatted_questions += f"\n{category.upper().replace('_', ' ')}:\n"
            for i, question in enumerate(question_list, 1):
                formatted_questions += f"{i}. {question}\n"
        
        try:
            # LLM'den soru-cevap çiftlerini al
            response = await self.llm.ainvoke(
                qa_generation_prompt.format(
                    chunk_text=chunk_text,
                    formatted_questions=formatted_questions
                )
            )
            
            # JSON parse et
            response_text = response.content.strip()
            
            # JSON'u temizle
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split("```")[1].strip()
                
            qa_data = json.loads(response_text)
            return qa_data.get("qa_pairs", [])
            
        except Exception as e:
            logging.error(f"Soru-cevap oluşturma hatası: {e}")
            return []
    
    async def _extract_entities_from_qa_pairs(self, 
                                            chunk_text: str, 
                                            qa_pairs: List[Dict[str, str]], 
                                            file_name: str, 
                                            chunk_index: int) -> Optional[GraphDocument]:
        """
        Soru-cevap çiftlerinden entity'leri ve ilişkileri çıkarır
        """
        
        if not qa_pairs:
            logging.warning(f"Chunk {chunk_index} için QA pair bulunamadı")
            return None
            
        # Relevantlı QA çiftlerini filtrele
        relevant_qa_pairs = [
            qa for qa in qa_pairs 
            if qa.get("answer", "").lower() not in ["bilgi yok", "yok", "bulunmuyor", "geçmiyor"]
        ]
        
        if not relevant_qa_pairs:
            logging.warning(f"Chunk {chunk_index} için relevant QA pair bulunamadı")
            return None
        
        # Entity çıkarma prompt'u
        entity_extraction_prompt = ChatPromptTemplate.from_template("""
Sen bir sigorta belgesi entity çıkarma uzmanısın. Aşağıdaki soru-cevap çiftlerini kullanarak sadece önemli ve spesifik entity'leri çıkar.

METIN PARÇASI:
{chunk_text}

SORU-CEVAP ÇİFTLERİ:
{qa_pairs_text}

GÖREVİN:
1. Sadece soru-cevaplarda geçen önemli ve spesifik bilgileri entity olarak çıkar
2. Gereksiz, genel veya belirsiz bilgileri atla  
3. Entity'ler arası mantıklı ilişkiler kur
4. Tarih, numara, isim, tutar gibi spesifik değerleri koru
5. Sigorta belgelerine özel entity'ler için özen göster

DESTEKLENEN ENTITY TÜRLERİ:
- Person (kişi isimleri: "Ayça Dinçkök")
- Company (firma/kuruluş: "Doğa Sigorta A.Ş.", "Dinkal Sigorta Acenteliği")
- PolicyNumber (poliçe no: "65789885") 
- IdentityNumber (TC kimlik: "415*****480")
- PhoneNumber (telefon: "0532****112", "0212 393 01 11")
- Address (adres: "Galata Residence Daire No:3")
- Date (tarih: "12.02.2020")
- Amount (tutar: "350.000,00 TL", "559,08 TL")
- Percentage (oran: "10%", "0.00%")
- BuildingInfo (bina bilgisi: "112 m²", "Tam Kagir")
- CoverageType (teminat türü: "Bina", "Yangın Mali Sorumluluk")
- AgentCode (acente kodu: "302113")
- UAVTCode (UAVT kodu: "2321812485")
- InstallmentInfo (taksit bilgisi: "12.03.2020 - 89,00 TL")

SİGORTA BELGESI ÖZEL KURALLARI:
- Poliçe numaralarını mutlaka PolicyNumber olarak çıkar
- Tüm tutarları Amount olarak çıkar (TL, para birimi ile)
- Sigorta şirketi ve acente isimlerini Company olarak çıkar
- Tarihleri Date olarak çıkar
- Yüzde değerlerini Percentage olarak çıkar
- Bina özelliklerini BuildingInfo olarak çıkar
- Teminat adlarını CoverageType olarak çıkar

JSON FORMAT:
{{
    "entities": [
        {{
            "id": "benzersiz_id",
            "type": "entity_türü", 
            "properties": {{
                "name": "entity_değeri",
                "source_qa": "hangi sorudan geldi",
                "confidence": "high/medium/low"
            }}
        }}
    ],
    "relationships": [
        {{
            "source": "kaynak_entity_id",
            "target": "hedef_entity_id", 
            "type": "ilişki_türü"
        }}
    ]
}}

NOT: Sadece gerçekten değerli ve spesifik bilgileri çıkar. Genel açıklamalar veya belirsiz bilgileri entity yapma.
ÖRNEK: "Ayça Dinçkök" -> Person, "65789885" -> PolicyNumber, "350.000,00 TL" -> Amount
""")
        
        # QA çiftlerini formatla
        qa_pairs_text = ""
        for qa in relevant_qa_pairs:
            qa_pairs_text += f"S: {qa.get('question', '')}\n"
            qa_pairs_text += f"C: {qa.get('answer', '')}\n"
            qa_pairs_text += f"Kategori: {qa.get('category', '')}\n\n"
        
        try:
            # LLM'den entity'leri çıkar
            response = await self.llm.ainvoke(
                entity_extraction_prompt.format(
                    chunk_text=chunk_text[:1000],  # Uzun metinleri kısalt
                    qa_pairs_text=qa_pairs_text
                )
            )
            
            # JSON parse et
            response_text = response.content.strip()
            
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split("```")[1].strip()
            
            entity_data = json.loads(response_text)
            
            # GraphDocument oluştur
            nodes = []
            relationships = []
            
            # Node'ları oluştur
            for entity in entity_data.get("entities", []):
                node = Node(
                    id=entity["id"],
                    type=entity["type"],
                    properties=entity.get("properties", {})
                )
                nodes.append(node)
            
            # Relationship'leri oluştur
            for rel in entity_data.get("relationships", []):
                source_node = next((n for n in nodes if n.id == rel["source"]), None)
                target_node = next((n for n in nodes if n.id == rel["target"]), None)
                
                if source_node and target_node:
                    relationship = Relationship(
                        source=source_node,
                        target=target_node,
                        type=rel["type"]
                    )
                    relationships.append(relationship)
            
            # Document source oluştur
            source_doc = Document(
                page_content=chunk_text,
                metadata={
                    "file_name": file_name,
                    "chunk_index": chunk_index,
                    "extraction_method": "qa_based"
                }
            )
            
            if nodes:  # Sadece node varsa GraphDocument oluştur
                graph_doc = GraphDocument(
                    nodes=nodes,
                    relationships=relationships,
                    source=source_doc
                )
                
                logging.info(f"Chunk {chunk_index}: {len(nodes)} entity, {len(relationships)} relationship çıkarıldı")
                return graph_doc
            else:
                logging.warning(f"Chunk {chunk_index}: Hiç entity çıkarılamadı")
                return None
                
        except Exception as e:
            logging.error(f"Entity çıkarma hatası (chunk {chunk_index}): {e}")
            return None


def create_domain_specific_questions(domain: str) -> Dict[str, List[str]]:
    """
    Belirli bir domain için özel sorular oluşturur
    """
    
    domain_questions = {
        "insurance": {
            "policy_basic_questions": [
                "Poliçe numarası nedir?",
                "Sigorta şirketi hangisidir?", 
                "Poliçe sahibi/sigortalı kimdir?",
                "Poliçe türü nedir (kasko, trafik, konut, işyeri)?",
                "Tanzim tarihi ve yeri nedir?",
                "Poliçe başlangıç ve bitiş tarihleri nelerdir?",
                "Poliçe süresi ne kadardır?"
            ],
            "policyholder_questions": [
                "Sigortalının adı ve soyadı nedir?",
                "TC kimlik numarası nedir?",
                "İletişim bilgileri (telefon, email, adres) nelerdir?",
                "Vergi numarası var mı?",
                "Meslek bilgisi nedir?"
            ],
            "coverage_questions": [
                "Hangi teminatlar sağlanmaktadır?",
                "Sigorta bedelleri nelerdir?",
                "Teminat tutarları ne kadardır?",
                "Muafiyet miktarları nelerdir?",
                "Koasürans oranı nedir?",
                "Hangi riskler kapsanmaktadır?",
                "Hariç tutulan durumlar nelerdir?",
                "Deprem teminatı var mı, oranı nedir?"
            ],
            "financial_questions": [
                "Net prim tutarı nedir?",
                "Brüt prim tutarı nedir?",
                "Vergiler (YSV, gider vergisi) ne kadardır?",
                "Taksit sayısı kaçtır?",
                "Taksit tutarları ve tarihleri nelerdir?",
                "Peşinat miktarı nedir?",
                "İndirimler uygulanmış mı?"
            ],
            "property_questions": [
                "Riziko adresi nerededir?",
                "Bina özellikleri nelerdir (m², kat, daire)?",
                "Yapı tarzı nasıldır (kagir, çelik vb.)?",
                "UAVT kodu nedir?",
                "Apartman adı nedir?",
                "Emlak değeri ne kadardır?"
            ],
            "agent_questions": [
                "Acente unvanı nedir?",
                "Acente kodu nedir?",
                "Acente levha numarası nedir?",
                "Acente iletişim bilgileri nelerdir?",
                "Bölge müdürlüğü hangisidir?"
            ],
            "vehicle_questions": [
                "Araç plakası nedir?",
                "Araç markası ve modeli nedir?",
                "Araç yılı nedir?",
                "Motor numarası nedir?",
                "Şasi numarası nedir?",
                "Araç değeri ne kadardır?"
            ],
            "additional_services_questions": [
                "Ek hizmetler nelerdir?",
                "Asistans hizmetleri var mı?",
                "7/24 hizmet telefonu nedir?",
                "Hukuksal koruma var mı?",
                "Ev yardım hizmetleri nelerdir?"
            ]
        },
        "legal": {
            "party_questions": [
                "Taraflar kimlerdir?",
                "Avukat veya hukuki temsilci kimdir?",
                "Mahkeme veya kurum hangisidir?"
            ],
            "case_questions": [
                "Dava numarası nedir?",
                "Dava konusu nedir?",
                "Önemli tarihler nelerdir?"
            ]
        },
        "financial": {
            "transaction_questions": [
                "İşlem tutarları nelerdir?",
                "Para birimler nelerdir?",
                "Hesap numaraları nelerdir?"
            ],
            "party_questions": [
                "Bankalar veya finansal kuruluşlar nelerdir?",
                "Müşteri bilgileri nelerdir?"
            ]
        }
    }
    
    return domain_questions.get(domain, {})


# Örnek kullanım fonksiyonu
async def example_usage():
    """
    QA tabanlı entity çıkarmanın örnek kullanımı
    """
    
    # Extractor'ı oluştur
    extractor = QABasedEntityExtractor("gpt-4o-mini")
    
    # Örnek metin chunks
    sample_chunks = [
        """
        Ahmet Yılmaz'ın sahip olduğu 34ABC123 plakalı aracı için 
        2024 yılında başlayan kasko poliçesi PS123456 numaralı 
        poliçe ile Güven Sigorta A.Ş. tarafından teminata alınmıştır.
        Poliçe 01.01.2024 - 31.12.2024 tarihleri arasında geçerlidir.
        """
    ]
    
    # Sigorta domain'i için özel sorular
    insurance_questions = create_domain_specific_questions("insurance")
    
    # Entity'leri çıkar
    graph_documents = await extractor.extract_entities_from_qa(
        document_chunks=sample_chunks,
        file_name="örnek_poliçe.pdf",
        custom_questions=insurance_questions
    )
    
    # Sonuçları yazdır
    for doc in graph_documents:
        print(f"Entities: {len(doc.nodes)}")
        print(f"Relationships: {len(doc.relationships)}")
        for node in doc.nodes:
            print(f"  - {node.type}: {node.id}")
