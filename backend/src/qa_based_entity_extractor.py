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
        
        # Her chunk için guidance-based çıkarma yap
        all_graph_documents = []
        
        for chunk_index, chunk_text in enumerate(document_chunks):
            logging.info(f"Chunk {chunk_index + 1}/{len(document_chunks)} işleniyor")
            
            # Soruları guidance olarak kullanarak entity çıkar
            graph_doc = await self._extract_entities_with_guidance(
                chunk_text=chunk_text,
                guidance_questions=self._format_guidance_questions(questions_to_use),
                file_name=file_name,
                chunk_index=chunk_index
            )
            
            if graph_doc:
                all_graph_documents.append(graph_doc)
                
        logging.info(f"QA tabanlı çıkarma tamamlandı: {len(all_graph_documents)} GraphDocument oluşturuldu")
        return all_graph_documents
    
    def _format_guidance_questions(self, questions: Dict[str, List[str]]) -> str:
        """
        Soruları guidance formatında düzenler ve benzer soruları da düşünmesi için yönlendirme yapar
        """
        formatted_questions = ""
        for category, question_list in questions.items():
            formatted_questions += f"\n{category.upper().replace('_', ' ')} BİLGİLERİ:\n"
            for i, question in enumerate(question_list, 1):
                # Soruları guidance formuna çevir
                guidance_point = question.replace("?", " türünde bilgileri")
                formatted_questions += f"{i}. {guidance_point} arayın ve çıkarın\n"
            
            # Her kategori için ek yönlendirme ekle
            formatted_questions += f"   → Bu kategorideki sorulara benzer, ilgili diğer {category.replace('_', ' ')} bilgilerini de arayın\n"
        
        # Genel yaratıcı yönlendirme ekle
        formatted_questions += f"\n💡 YARATICI YAKLAŞIM:\n"
        formatted_questions += f"- Yukarıdaki sorulara benzer, ilişkili soruları da düşünün\n"
        formatted_questions += f"- Mümkün olduğunca çok alakalı entity ve relationship çıkarın\n"
        formatted_questions += f"- Metindeki her önemli bilgiyi entity olarak değerlendirin\n"
        
        return formatted_questions
    
    def _format_relationship_type(self, rel_type: str) -> str:
        """
        Relationship type'ını standart formata çevirir (BÜYÜK_HARF_UNDERSCORE)
        """
        # Boşlukları ve tire işaretlerini underscore ile değiştir
        formatted = rel_type.replace(" ", "_").replace("-", "_")
        # Büyük harfe çevir
        formatted = formatted.upper()
        # Türkçe karakterleri İngilizce karşılıkları ile değiştir
        tr_chars = {
            'Ç': 'C', 'Ğ': 'G', 'İ': 'I', 'Ö': 'O', 'Ş': 'S', 'Ü': 'U',
            'ç': 'C', 'ğ': 'G', 'ı': 'I', 'ö': 'O', 'ş': 'S', 'ü': 'U'
        }
        for tr_char, en_char in tr_chars.items():
            formatted = formatted.replace(tr_char, en_char)
        return formatted
    
    async def _extract_entities_with_guidance(self, 
                                             chunk_text: str, 
                                             guidance_questions: str,
                                             file_name: str, 
                                             chunk_index: int) -> Optional[GraphDocument]:
        """
        Guidance sorularını kullanarak doğrudan entity ve relation çıkarımı yapar.
        Q&A üretmez, sadece soruların odaklandığı konularda entity arar.
        """
        
        # Guidance-based entity extraction prompt'u
        guidance_extraction_prompt = ChatPromptTemplate.from_template("""
Sen bir belge entity ve relationship çıkarma uzmanısın. Aşağıdaki metin parçasından entity ve relationship çıkaracaksın.

METIN PARÇASI:
{chunk_text}

REHBER KONULAR:
{guidance_questions}

GÖREVİN:
1. Yukarıdaki konuları SADECE REHBER olarak kullan - sorulara cevap verme
2. Bu konular ve benzer türdeki entity'lerin varlığını tespit et
3. Örneğin "Poliçe numarası nedir?" sorusu varsa -> PolicyNumber entity'si oluştur buna benzer bilgileride kullan
4. "Sigortalı kimdir?" sorusu varsa -> metinde kişi ismi geçiyorsa Person entity'si oluştur
5. Mümkün olduğunca çok entity türü tespit et ve relationship kur. REHBER KONULAR sana yol göstericidir.
6. Entity'ler arasında mantıklı ilişkiler kur
7. properties veya attributes bilgileri çıkarmanı istemiyorum. Sadece Entities ve Relationships çıkar.

RELATIONSHIP KURALLARI:
- BÜYÜK_HARF_UNDERSCORE formatı kullan
- İngilizce entity ve relationship terimleri kullan.

JSON FORMAT:
{{
    "entities": [
        {{
            "id": "entity_1",
            "type": "Person", 
        }},
        {{
            "id": "entity_2",
            "type": "PolicyNumber", 
        }}
    ],
    "relationships": [
        {{
            "source": "entity_1",
            "target": "entity_2", 
            "type": "HAS_POLICY_NUMBER"
        }}
    ]
}}

""")
        
        try:
            # Prompt'u loglayalım
            formatted_prompt = guidance_extraction_prompt.format(
                chunk_text=chunk_text,
                guidance_questions=guidance_questions
            )
            
            logging.info(f"Chunk {chunk_index} - LLM'e Gönderilen Prompt:")
            logging.info(f"{'*'*30} PROMPT BAŞLANGICI {'*'*30}")
            logging.info(formatted_prompt)
            logging.info(f"{'*'*30} PROMPT BİTİŞİ {'*'*30}")
            
            # LLM'den entity'leri çıkar
            response = await self.llm.ainvoke(formatted_prompt)
            
            # JSON parse et
            response_text = response.content.strip()
            
            # LLM çıktısını logla
            logging.info(f"Chunk {chunk_index} - LLM Ham Çıktı:")
            logging.info(f"{'='*50}")
            logging.info(response_text)
            logging.info(f"{'='*50}")
            
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split("```")[1].strip()
            
            # Temizlenmiş JSON'u da logla
            logging.info(f"Chunk {chunk_index} - Temizlenmiş JSON:")
            logging.info(response_text)
            
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
                    # Relationship type'ını formatla (büyük harf, underscore)
                    formatted_rel_type = self._format_relationship_type(rel["type"])
                    relationship = Relationship(
                        source=source_node,
                        target=target_node,
                        type=formatted_rel_type
                    )
                    relationships.append(relationship)
            
            # Document source oluştur
            source_doc = Document(
                page_content=chunk_text,
                metadata={
                    "file_name": file_name,
                    "chunk_index": chunk_index,
                    "extraction_method": "guidance_based"
                }
            )
            
            if nodes:  # Sadece node varsa GraphDocument oluştur
                graph_doc = GraphDocument(
                    nodes=nodes,
                    relationships=relationships,
                    source=source_doc
                )
                
                logging.info(f"Chunk {chunk_index}: {len(nodes)} entity, {len(relationships)} relationship çıkarıldı (guidance-based)")
                return graph_doc
            else:
                logging.warning(f"Chunk {chunk_index} için entity çıkarılamadı")
                return None
                
        except Exception as e:
            logging.error(f"Guidance-based entity çıkarma hatası: {e}")
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

