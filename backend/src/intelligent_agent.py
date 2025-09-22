#!/usr/bin/env python3
"""
Intelligent Neo4j ReAct Agent
Bu agent kullanıcı sorularını analiz eder, Neo4j schema'sını kullanarak
Cypher sorguları oluşturur ve veritabanını sorgular.
"""

import logging
import json
import re
import os
import sys
import time
import asyncio
import concurrent.futures
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from langchain.schema import HumanMessage, SystemMessage
from langchain_core.messages import ToolMessage
from langchain_neo4j import Neo4jGraph
import neo4j.time
# from mem0 import Memory  # Mem0 özelliği devre dışı bırakıldı

# Path ayarla
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from src.llm import get_llm
from src.shared.common_fn import load_embedding_model
from src.utf8_utils import normalize_unicode_text
from src.schema_extractor import get_compact_schema
from dotenv import load_dotenv
from dataclasses import dataclass, field
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

# Load environment variables
load_dotenv()

# Base URL for reference links
BASE_URL = os.getenv("BASE_URL", "http://localhost:8001")

# Mem0 configuration for query strategy memory - Qdrant vector store kullanarak
mem0_config = {
    "embedder": {
        "provider": "openai",
        "config": {
            "model": "text-embedding-3-large",
            "embedding_dims": 1536,  # text-embedding-3-large için standart boyut
        },
    },
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "host": "qdrant",  # Container hostname kullan
            "port": 6333,
            "collection_name": "strategy_memories",
            "embedding_model_dims": 1536,
        },
    },
}


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Mem0 instance - güvenli initialization (DEVRE DIŞI)
# try:
#     mem0 = Memory.from_config(config_dict=mem0_config)
#     logger.info("✅ Mem0 başarıyla initialize edildi")
# except Exception as e:
#     logger.warning(f"⚠️ Mem0 initialization hatası: {e}")
#     logger.info("ℹ️ Mem0 devre dışı - agent normal çalışmaya devam edecek")
#     mem0 = None
mem0 = None  # Mem0 özelliği tamamen devre dışı bırakıldı


def serialize_neo4j_data(obj):
    """Neo4j özel tiplerini JSON serializable hale getir"""
    if isinstance(obj, neo4j.time.DateTime):
        return obj.iso_format()
    elif isinstance(obj, (neo4j.time.Date, neo4j.time.Time)):
        return str(obj)
    elif isinstance(obj, dict):
        return {k: serialize_neo4j_data(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [serialize_neo4j_data(item) for item in obj]
    else:
        return obj


@dataclass
class ChunkInfo:
    """Chunk bilgilerini tutan data class"""

    chunk_id: str
    text: str
    page_number: Optional[int] = None
    document_name: Optional[str] = None
    document_metadata: Dict[str, Any] = field(default_factory=dict)
    relevance_score: float = 0.0
    split_texts: List[str] = field(default_factory=list)
    split_scores: List[float] = field(default_factory=list)
    page_link: Optional[str] = None


@dataclass
class SuccessfulFinding:
    """Başarılı bulguları tutan data class"""

    iteration: int
    action_type: str  # cypher_query
    summary: str
    relevance_score: float
    raw_data: Any = None


@dataclass
class AgentState:
    """Agent'ın mevcut durumu ve topladığı bilgileri tutan state"""

    question: str  # Bu tek non-default field, en başta olmalı
    original_question: str = (
        ""  # Orijinal kullanıcı sorusu (referans tipi analizi için)
    )
    iteration_count: int = 0
    max_chunks_limit: int = 20
    similarity_threshold: float = 0.3
    discovered_chunks: List[ChunkInfo] = field(default_factory=list)
    discovered_entities: List[Dict[str, Any]] = field(default_factory=list)
    discovered_relationships: List[Dict[str, Any]] = field(default_factory=list)
    query_attempts: List[str] = field(default_factory=list)
    successful_findings: List["SuccessfulFinding"] = field(
        default_factory=list
    )  # Başarılı bulgular
    # YENİ: Bulunan document filenames - policy source_file'larından
    discovered_document_filenames: List[str] = field(default_factory=list)
    # YENİ: Başarısız entity query deneme sayacı
    failed_entity_query_count: int = 0
    max_entity_query_attempts: int = 3
    # YENİ: Başarısız Cypher sorgularını takip et
    failed_queries: List[Dict[str, Any]] = field(default_factory=list)


class ResourceManager:
    """LLM'in bulduğu chunk kaynaklarını yöneten basit sistem"""

    def __init__(self):
        self.page_resources = []  # Sayfa görselleri için (chunk'lar)

    def add_page_resource(self, page_link: str):
        """Sayfa görseli kaynağı ekle"""
        if not any(r["page_link"] == page_link for r in self.page_resources):
            self.page_resources.append({"page_link": page_link, "type": "page"})
            return f"✅ Page resource eklendi: {page_link}"
        return f"⚠️ Page resource zaten mevcut: {page_link}"

    def get_all_resources(self):
        """Tüm kaynakları döndür"""
        return {"pages": self.page_resources, "total_count": len(self.page_resources)}

    def clear_resources(self):
        """Kaynakları temizle"""
        self.page_resources = []


class IntelligentAgent:
    """
    ReAct pattern kullanan Neo4j intelligent agent
    LLM kendi arama stratejisini belirler ve iteratif olarak doğru veriye ulaşır
    """

    def __init__(
        self,
        graph: Neo4jGraph,
        model_name: str = "openai_gpt_4.1",
        enable_llm_interpretation: bool = False,
    ):
        self.graph = graph
        self.llm, _ = get_llm(model_name)
        self.master_llm, _ = get_llm("openai_gpt_4.1")
        self.embedding_model, _ = load_embedding_model("openai")
        self.max_iterations = 10  # Derinlemesine araştırma için
        # self.max_iterations = 5
        self.schema_cache = None
        self.system_prompt_cache = None  # Schema-based system prompt cache - PROMPT güncellendi: Genel analiz kuralı eklendi
        self.enable_llm_interpretation = (
            enable_llm_interpretation  # LLM yorumlama açık/kapalı
        )

        # Schema'yı initialize et
        self._initialize_schema_cache()
        
        # System prompt'u da başlangıçta oluştur (eager loading)
        self._initialize_system_prompt_cache()

        # Progress tracking ve context memory
        self.context_memory = ""  # Birikimli context prompt
        self.token_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self.detailed_token_usage = []  # Action bazında detaylı token tracking

        # Session tracking
        self.current_session_id = "unknown"  # Mevcut session ID

        # Resource Manager - YENİ!
        self.resource_manager = ResourceManager()

        # Mem0 Memory Manager - DEVRE DIŞI!
        # self.memory = mem0 if mem0 is not None else None
        self.memory = None  # Mem0 özelliği devre dışı bırakıldı
        self.query_strategies = []  # Bu session'da denenen stratejiler

        # Thread Pool Executor for background memory operations (mem0 devre dışı olduğu için isteğe bağlı)
        # self._memory_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    def _initialize_system_prompt_cache(self):
        """System prompt'u başlangıçta bir kez oluştur ve cache'le"""
        try:
            logger.info("📋 System prompt başlangıçta oluşturuluyor (agent yaratılırken)...")
            self.system_prompt_cache = self.create_enhanced_system_prompt({})
            logger.info(f"✅ System prompt cache'lendi: {len(self.system_prompt_cache)} karakter")
            logger.info("🎯 System prompt agent oluşturulurken hazırlandı - ilk mesajda cache'den alınacak!")
        except Exception as e:
            logger.error(f"❌ System prompt cache'leme hatası: {e}")
            self.system_prompt_cache = "⚠️ System prompt oluşturulamadı - LLM bağlantısını kontrol edin"

    def _initialize_schema_cache(self):
        """Schema bilgisini başlangıçta bir kez al ve cache'le"""
        try:
            logger.info("📋 Neo4j schema bilgisi başlangıçta alınıyor (agent yaratılırken)...")
            self.schema_cache = get_compact_schema(self.graph)
            logger.info(f"✅ Schema cache'lendi: {len(self.schema_cache)} karakter")
            logger.info("🎯 Schema agent oluşturulurken hazırlandı - ilk mesajda cache'den alınacak!")
        except Exception as e:
            logger.error(f"❌ Schema cache'leme hatası: {e}")
            self.schema_cache = "⚠️ Schema bilgisi alınamadı - Graph database bağlantısını kontrol edin"

    def get_cached_schema(self) -> str:
        """Cache'lenmiş schema bilgisini döndür"""
        if self.schema_cache is None or not self.schema_cache:
            logger.warning("⚠️ Schema cache boş, yeniden alınıyor...")
            self._initialize_schema_cache()
        return self.schema_cache

    def interpret_final_answer_with_llm(
        self,
        raw_answer: str,
        user_question: str,
        chunks: List[ChunkInfo],
        cypher_results: Any = None,
    ) -> str:
        """LLM ile final answer'ı yorumla ve zenginleştir"""
        try:
            if not self.enable_llm_interpretation:
                logger.info("LLM yorumlama kapalı, raw data döndürülüyor")
                return raw_answer

            logger.info("LLM ile final answer yorumlanıyor...")

            # Chunk'lardan context oluştur
            context_sources = []
            if chunks:
                for i, chunk in enumerate(chunks[:5], 1):  # En iyi 5 chunk
                    context_sources.append(
                        f"Kaynak {i} ({chunk.document_name}, Sayfa {chunk.page_number}):\n{chunk.text[:300]}..."
                    )

            context_text = (
                "\n\n".join(context_sources) if context_sources else "Kaynak bilgi yok"
            )

            # Cypher sonuçlarını da ekle
            raw_data_detail = raw_answer
            if cypher_results:
                raw_data_detail += f"\n\nCYPHER SORGU SONUÇLARI:\n{json.dumps(cypher_results, ensure_ascii=False, indent=2)}"

            interpretation_prompt = f"""Sen bir uzman analist olarak kullanıcı sorusunu yanıtlayacaksın.

KULLANICI SORUSU: {user_question}

HAM VERİ/BULGULAR:
{raw_data_detail}

KAYNAK BİLGİLER:
{context_text}

GÖREV:
1. Ham veriyi AYNEN analiz et ve yorumla - veri kaybetme!
2. Sayısal veriler ve isimler varsa MUTLAKA belirt
3. Kullanıcının sorusuna kapsamlı bir cevap ver
4. Kaynak bilgileri referans göster
5. Sonucu net ve anlaşılır şekilde özetle

ÇIKTI FORMAT:

## Bulgular
[Bulunan sonuçlar]

## Kaynaklar
[Kaynak bilgilerin referansları]
"""

            messages = [
                SystemMessage(
                    content="Sen uzman bir veri analisti ve raporlama uzmanısın. Verilen bilgileri analiz ederek kullanıcı dostu, kapsamlı raporlar hazırlarsın."
                ),
                HumanMessage(content=interpretation_prompt),
            ]

            response = self.llm.invoke(messages)

            # Response content'i al - GPT-5-mini için list kontrolü
            raw_content = response.content
            if isinstance(raw_content, list):
                interpreted_answer = " ".join(str(item) for item in raw_content).strip()
            else:
                interpreted_answer = raw_content.strip()

            # Token kullanımını logla
            self.log_token_usage(
                response, -1, "llm_interpretation"
            )  # -1 iteration: extra step

            logger.info(f"LLM yorumlama tamamlandı: {len(interpreted_answer)} karakter")

            # Resource Manager'dan page resource'ları ekle
            if self.resource_manager:
                resources = self.resource_manager.get_all_resources()
                if resources["total_count"] > 0:
                    logger.info(f"📝 LLM yorumlamasına {resources['total_count']} page resource ekleniyor")

                    # Page resource'ları ekle
                    page_refs = []
                    for page_resource in resources["pages"]:
                        page_link = page_resource["page_link"]
                        try:
                            import urllib.parse
                            encoded_page_link = urllib.parse.quote(
                                page_link, safe="", encoding="utf-8"
                            )
                            image_link = f"{BASE_URL}/images/{encoded_page_link}"

                            # Sayfa bilgilerini parse et
                            page_info = "Sayfa Görseli"
                            if "_page_" in page_link:
                                try:
                                    page_num = page_link.split("_page_")[1].split(".")[0]
                                    page_info = f"Sayfa {page_num}"
                                except:
                                    page_info = "Sayfa Görseli"

                            page_refs.append(f"![{page_info}]({image_link})")
                        except Exception as e:
                            logger.error(f"Page resource ekleme hatası ({page_link}): {e}")

                    if page_refs:
                        interpreted_answer += "\n\n**📋 Sayfa Görselleri:**\n"
                        for page_ref in page_refs:
                            interpreted_answer += f"- {page_ref}\n"

            return interpreted_answer

        except Exception as e:
            logger.error(f"LLM yorumlama hatası: {e}")
            return raw_answer  # Fallback: raw answer'ı döndür

    def format_final_answer_with_references_simple(self, clean_answer: str) -> str:
        """
        YENİ VE BASİT: Resource Manager'dan kaynakları alıp cevabın sonuna ekler
        """
        try:
            logger.info("📝 Basit referans sistemi ile final answer formatlanıyor...")

            # Ana cevabı başlat
            formatted_answer = clean_answer.strip()

            # Resource Manager'dan kaynakları al
            resources = self.resource_manager.get_all_resources()
            logger.info(f"📝 Resource Manager'dan alınan kaynaklar: {resources}")

            if resources["total_count"] == 0:
                logger.info("📝 Hiç kaynak bulunamadı, sadece cevap döndürülüyor")
                return formatted_answer

            # Referans listelerini ayrı tut
            pdf_references = []
            image_references = []

            # Document Resources (PDF Dosyaları)
            document_files = set()
            for page_resource in resources["pages"]:
                # Sayfa kaynaklarından dosya adlarını çıkar
                page_link = page_resource.get("page_link", "")
                if page_link and "_page_" in page_link:
                    # "filename_page_001.png" formatından "filename.pdf" çıkar
                    file_base = page_link.split("_page_")[0]
                    if file_base:
                        # PDF uzantısı ekle
                        pdf_filename = f"{file_base}.pdf"
                        document_files.add(pdf_filename)

            # PDF dosya linklerini ekle
            for pdf_filename in sorted(document_files):
                try:
                    import urllib.parse

                    encoded_filename = urllib.parse.quote(
                        pdf_filename, safe="", encoding="utf-8"
                    )
                    pdf_link = f"{BASE_URL}/files/{encoded_filename}"
                    doc_ref = f"📄 [{pdf_filename}]({pdf_link})"
                    if doc_ref not in pdf_references:
                        pdf_references.append(doc_ref)
                except Exception as e:
                    logger.error(f"Document resource hatası ({pdf_filename}): {e}")

            # NOT: Document Resources kaldırıldı - LLM zaten hangi kaynaktan faydalandıysa
            # onu page_link ile page_resource olarak ekleyecek

            # Page Resources (Sayfa görselleri)
            for page_resource in resources["pages"]:
                page_link = page_resource["page_link"]

                try:
                    # page_link'i direkt kullan - zaten tam image linki olacak
                    if page_link:
                        import urllib.parse

                        encoded_page_link = urllib.parse.quote(
                            page_link, safe="", encoding="utf-8"
                        )
                        image_link = f"{BASE_URL}/images/{encoded_page_link}"

                        # Sayfa bilgilerini parse et (dosya adından sayfa numarasını çıkar)
                        page_info = "Sayfa Görseli"
                        if "_page_" in page_link:
                            try:
                                page_num = page_link.split("_page_")[1].split(".")[0]
                                page_info = f"Sayfa {page_num}"
                            except:
                                page_info = "Sayfa Görseli"

                        # Markdown image thumbnail formatı (resim olarak gösterir)
                        page_ref = f"![{page_info}]({image_link})"
                        # Alternatif: Hem thumbnail hem link istiyorsanız:
                        # page_ref = f"![{page_info}]({image_link}) - [Büyük Görüntüle]({image_link})"

                        if page_ref not in image_references:
                            image_references.append(page_ref)
                except Exception as e:
                    logger.error(f"Page resource hatası ({page_link}): {e}")

            # Referansları cevaba ekle - önce PDF'ler, sonra image'ler
            if pdf_references or image_references:
                formatted_answer += "\n\n**📋 Kaynaklar:**\n"

                # PDF belgeler - her biri ayrı liste elemanı
                for pdf_ref in pdf_references:
                    formatted_answer += f"- {pdf_ref}\n"

                # Image'ler - ilk image liste elemanı, diğerleri girinti ile
                if image_references:
                    for i, img_ref in enumerate(image_references):
                        if i == 0:
                            formatted_answer += f"- {img_ref}\n"
                        else:
                            formatted_answer += f"  {img_ref}\n"

                total_refs = len(pdf_references) + len(image_references)
                logger.info(
                    f"📝 Basit referans ekleme tamamlandı: {total_refs} referans ({len(pdf_references)} PDF + {len(image_references)} image)"
                )
            else:
                logger.info("📝 Basit referans ekleme tamamlandı: 0 referans")

            return formatted_answer

        except Exception as e:
            logger.error(f"Basit referans ekleme hatası: {e}")
            return clean_answer  # Fallback: sadece temiz cevabı döndür

    def execute_cypher_query(self, query: str) -> Tuple[bool, Any]:
        """Cypher sorgusunu çalıştır - Saklanan embedding'leri parametrelere ekle"""
        try:
            # logger.info(f"Cypher sorgusu çalıştırılıyor: {query}")

            # Query parametrelerini hazırla
            query_params = {}

            # Eğer query'de $embedding_vector parametresi varsa, saklanan embedding'i kullan
            if "$embedding_vector" in query:
                available_embeddings = getattr(self, "_cypher_embeddings", {})

                if available_embeddings:
                    # Son oluşturulan embedding'i kullan
                    latest_embedding_key = list(available_embeddings.keys())[-1]
                    embedding_vector = available_embeddings[latest_embedding_key]
                    query_params["embedding_vector"] = embedding_vector

                    logger.info(
                        f"✅ Cypher query'ye embedding eklendi: {latest_embedding_key}"
                    )
                    logger.info(f"📊 Embedding dimensions: {len(embedding_vector)}")
                else:
                    logger.warning(
                        "❌ Query'de $embedding_vector var ama saklanan embedding bulunamadı"
                    )
                    return (
                        False,
                        "Query'de $embedding_vector parametresi var ama embedding oluşturulmamış. Önce generate_embeddings_for_cypher tool'unu çağır.",
                    )

            # Cypher sorgusunu çalıştır
            result = self.graph.query(query, query_params)
            logger.info(f"Sonuç: {len(result) if result else 0} kayıt")

            # Sonuç detaylarını logla - özellikle chunk bilgileri için
            if result:
                logger.info(f"🔍 CYPHER QUERY SONUÇLARI:")
                for i, row in enumerate(result[:5]):  # İlk 5 satırı göster
                    logger.info(f"📄 Satır {i+1}:")

                    # Chunk bilgileri varsa detaylı logla
                    if "node.text" in row or "text" in row:
                        text_content = row.get("node.text") or row.get("text", "")
                        score = row.get("score", "N/A")

                        # Chunk metadata'sını bul
                        chunk_id = row.get("node.chunkId") or row.get("chunkId", "N/A")
                        page_number = row.get("node.page_number") or row.get(
                            "page_number", "N/A"
                        )
                        position = row.get("node.position") or row.get(
                            "position", "N/A"
                        )

                        logger.info(f"   📊 Chunk ID: {chunk_id}")
                        logger.info(f"   📄 Sayfa: {page_number}")
                        logger.info(f"   🎯 Position: {position}")
                        logger.info(f"   📈 Score: {score}")
                        logger.info(f"   💬 Text: {text_content[:150]}...")

                        # Sonunda kesik mi diye kontrol et
                        if text_content and (
                            text_content.endswith("|")
                            or text_content.endswith("-")
                            or text_content.endswith(",")
                            or len(text_content) > 800
                        ):  # Uzun chunk ise kesilmiş olabilir
                            logger.info(
                                f"   ⚠️  Bu chunk'ta bilgi eksik kalmiş olabilir - sonraki chunk'lara bakılmalı"
                            )
                    else:
                        logger.info(f"   📋 Row data: {row}")

                    logger.info("   " + "=" * 60)

            return True, result
        except Exception as e:
            logger.error(f"Cypher sorgu hatası: {e}")
            return False, str(e)

    def generate_embeddings_for_cypher(self, text: str) -> Tuple[bool, Any]:
        """Cypher sorgularında kullanmak üzere text'ten embedding oluşturur

        Bu fonksiyon LLM'in tool olarak çağırdığı ve embedding'leri sakladığı fonksiyondur.
        LLM bu embedding'leri Cypher'da $embedding_vector değişkeni olarak kullanır.

        Args:
            text: Embedding oluşturulacak text

        Returns:
            Tuple[bool, list]: Başarı durumu ve embedding vektörü
        """
        try:
            logger.info(f"🧠 Cypher için embedding oluşturuluyor: {text}")

            # Text'i normalize et
            normalized_text = normalize_unicode_text(text)
            logger.info(f"🧹 Normalize edilmiş text: {normalized_text}")

            # OpenAI embedding oluştur
            embedding_vector = self.embedding_model.embed_query(normalized_text)

            logger.info(
                f"✅ Cypher embedding oluşturuldu: {len(embedding_vector)} boyutlu vektör"
            )
            return True, embedding_vector

        except Exception as e:
            logger.error(f"❌ Cypher embedding oluşturma hatası: {e}")
            return False, str(e)

    def log_detailed_token_report(self):
        """Detaylı token kullanım raporunu logla"""
        try:
            if not self.detailed_token_usage:
                logger.info("📊 Henüz token kullanım verisi yok")
                return

            logger.info("\n" + "=" * 80)
            logger.info("📊 DETAYLI TOKEN KULLANIM RAPORU")
            logger.info("=" * 80)

            # Action tipine göre grupla
            action_totals = {}
            for usage in self.detailed_token_usage:
                action_type = usage["action_type"]
                if action_type not in action_totals:
                    action_totals[action_type] = {
                        "count": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                    }
                action_totals[action_type]["count"] += 1
                action_totals[action_type]["input_tokens"] += usage["input_tokens"]
                action_totals[action_type]["output_tokens"] += usage["output_tokens"]
                action_totals[action_type]["total_tokens"] += usage["total_tokens"]

            # Action bazında özet
            logger.info("🎯 ACTION BAZINDA TOKEN KULLANIMI:")
            for action_type, totals in sorted(action_totals.items()):
                avg_total = (
                    totals["total_tokens"] / totals["count"]
                    if totals["count"] > 0
                    else 0
                )
                logger.info(f"   {action_type.upper()}:")
                logger.info(f"     - Çağrı Sayısı: {totals['count']}")
                logger.info(f"     - Toplam Token: {totals['total_tokens']:,}")
                logger.info(
                    f"     - Input: {totals['input_tokens']:,} | Output: {totals['output_tokens']:,}"
                )
                logger.info(f"     - Ortalama/Çağrı: {avg_total:.1f}")
                logger.info("")

            # İterasyon bazında detay
            logger.info("🔄 İTERASYON BAZINDA DETAY:")
            for usage in self.detailed_token_usage:
                logger.info(
                    f"   İterasyon {usage['iteration']} ({usage['action_type']}):"
                )
                logger.info(
                    f"     Input: {usage['input_tokens']:,} | Output: {usage['output_tokens']:,} | Total: {usage['total_tokens']:,}"
                )

            # En yüksek/en düşük token kullananlar
            if len(self.detailed_token_usage) > 1:
                max_usage = max(
                    self.detailed_token_usage, key=lambda x: x["total_tokens"]
                )
                min_usage = min(
                    self.detailed_token_usage, key=lambda x: x["total_tokens"]
                )

                logger.info("📈 EN YÜKSEK TOKEN KULLANAN:")
                logger.info(
                    f"   İterasyon {max_usage['iteration']} ({max_usage['action_type']}): {max_usage['total_tokens']:,} token"
                )

                logger.info("📉 EN DÜŞÜK TOKEN KULLANAN:")
                logger.info(
                    f"   İterasyon {min_usage['iteration']} ({min_usage['action_type']}): {min_usage['total_tokens']:,} token"
                )

            # Genel istatistikler
            total_calls = len(self.detailed_token_usage)
            avg_per_call = (
                self.token_usage["total_tokens"] / total_calls if total_calls > 0 else 0
            )

            logger.info("📊 GENEL İSTATİSTİKLER:")
            logger.info(f"   Toplam LLM Çağrısı: {total_calls}")
            logger.info(f"   Toplam Token: {self.token_usage['total_tokens']:,}")
            logger.info(f"   Ortalama Token/Çağrı: {avg_per_call:.1f}")
            logger.info(
                f"   Input/Output Oranı: {self.token_usage['input_tokens'] / max(self.token_usage['output_tokens'], 1):.2f}"
            )

            logger.info("=" * 80)

        except Exception as e:
            logger.error(f"Token raporu oluşturma hatası: {e}")

    def log_token_usage(self, response, iteration: int, action_type: str = "unknown"):
        """Token kullanımını logla - action bazında detaylarla"""
        try:
            # LangChain OpenAI response structure
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                usage = response.usage_metadata
                # usage_metadata dict olarak geliyor
                input_tokens = usage.get("input_tokens", 0)
                output_tokens = usage.get("output_tokens", 0)
                total_tokens = usage.get("total_tokens", 0)
            elif (
                hasattr(response, "response_metadata")
                and "token_usage" in response.response_metadata
            ):
                # Alternatif structure
                usage = response.response_metadata["token_usage"]
                input_tokens = usage.get("prompt_tokens", 0)
                output_tokens = usage.get("completion_tokens", 0)
                total_tokens = usage.get("total_tokens", 0)
            else:
                # Manuel token sayımı (yaklaşık)
                input_text = (
                    str(response.content) if hasattr(response, "content") else ""
                )
                input_tokens = len(input_text.split()) * 1.3  # Yaklaşık token hesabı
                output_tokens = len(input_text.split()) * 0.7
                total_tokens = input_tokens + output_tokens

                logger.warning(
                    f"Token usage metadata bulunamadı, yaklaşık hesaplama yapıldı"
                )

            self.token_usage["input_tokens"] += int(input_tokens)
            self.token_usage["output_tokens"] += int(output_tokens)
            self.token_usage["total_tokens"] += int(total_tokens)

            # Detaylı token tracking kaydet
            action_token_info = {
                "iteration": iteration,
                "action_type": action_type,
                "input_tokens": int(input_tokens),
                "output_tokens": int(output_tokens),
                "total_tokens": int(total_tokens),
                "timestamp": time.time(),
            }
            self.detailed_token_usage.append(action_token_info)

            logger.info(
                f"🎯 İterasyon {iteration} ({action_type}) - Token: Input: {int(input_tokens)}, Output: {int(output_tokens)}, Total: {int(total_tokens)}"
            )
            logger.info(
                f"📊 Toplam Token: Input: {self.token_usage['input_tokens']}, Output: {self.token_usage['output_tokens']}, Total: {self.token_usage['total_tokens']}"
            )

        except Exception as e:
            logger.error(f"Token logging hatası: {e}")
            # Debug için response structure'ını logla
            logger.debug(f"Response attributes: {dir(response)}")
            if hasattr(response, "__dict__"):
                logger.debug(f"Response dict: {response.__dict__}")

    def log_llm_prompt(
        self,
        system_prompt: str,
        user_prompt: str,
        iteration: int,
        full_messages: list = None,
    ):
        """LLM'e gönderilen prompt'u RAW formatında hiç filtreleme yapmadan dosyaya kaydet"""
        try:
            # Dosya adı - session id ve timestamp ile
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_info = getattr(self, "current_session_id", "unknown")
            log_file = f"llm_prompts_RAW_{session_info}_{timestamp}_iter_{iteration}.txt"
            log_dir = os.path.abspath("context_memory_logs")
            log_path = os.path.join(log_dir, log_file)

            # Dizin yoksa oluştur
            os.makedirs(log_dir, exist_ok=True)

            # Sadece basit log - analiz yok
            logger.info(f"📁 RAW LLM PROMPT LOGGING - İterasyon {iteration}")
            logger.info(f"   System Prompt: {len(system_prompt):,} karakter")
            logger.info(f"   User Prompt: {len(user_prompt):,} karakter")
            if full_messages:
                logger.info(f"   Full Messages: {len(full_messages)} mesaj")

            # RAW içerik - hiçbir filtre veya formatlama olmadan
            file_content = f"""{'='*100}
RAW LLM PROMPT LOGGING - İterasyon {iteration}
Tarih: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
Session ID: {session_info}
System Prompt Length: {len(system_prompt):,} karakter
User Prompt Length: {len(user_prompt):,} karakter
Full Messages Count: {len(full_messages) if full_messages else 0}
{'='*100}

"""

            # Eğer full_messages varsa, önce onları RAW olarak yaz
            if full_messages:
                file_content += f"""{'='*100}
RAW FULL MESSAGES SENT TO LLM (AS RECEIVED):
{'='*100}

"""
                for i, msg in enumerate(full_messages):
                    msg_type = type(msg).__name__
                    msg_content = msg.content if hasattr(msg, "content") else str(msg)
                    file_content += f"""[MESSAGE {i+1} - {msg_type}]
{msg_content}

{'='*50}

"""

            # System ve User Prompt'ları TAM RAW HALDE yaz - hiçbir kısaltma veya filtreleme olmadan
#             file_content += f"""{'='*100}
# RAW SYSTEM PROMPT (FULL CONTENT - NO FILTERING):
# {'='*100}
# {system_prompt}

# {'='*100}
# RAW USER PROMPT (FULL CONTENT - NO FILTERING):
# {'='*100}
# {user_prompt}

# {'='*100}
# END OF RAW PROMPT LOGGING
# {'='*100}
# """

            # Dosyaya yaz
            try:
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(file_content)
                logger.info(f"📁 RAW LLM prompt dosyaya kaydedildi: {log_path}")
            except Exception as file_error:
                logger.error(f"RAW prompt dosya yazma hatası: {file_error}")

        except Exception as e:
            logger.error(f"RAW LLM prompt logging hatası: {e}")

    def log_llm_response(
        self,
        response_content: str,
        iteration: int,
        action: str,
        action_content: str,
        will_write: bool = True,
    ):
        """LLM response'unu dosyaya kaydet"""
        try:
            # Dosya adı
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_info = getattr(self, "current_session_id", "unknown")
            log_file = f"llm_response_{session_info}_{timestamp}_iter_{iteration}.txt"
            log_dir = os.path.abspath("context_memory_logs")
            log_path = os.path.join(log_dir, log_file)

            # Dizin yoksa oluştur (sadece yazacaksak)
            if will_write:
                os.makedirs(log_dir, exist_ok=True)

            # Response içeriğini hazırla
            file_content = f"""{'='*80}
LLM RESPONSE LOGGING - İterasyon {iteration}
Tarih: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
Session ID: {session_info}
{'='*80}

🎯 ACTION SUMMARY:
- Action Type: {action}
- Content Length: {len(action_content)} karakter
- Response Length: {len(response_content)} karakter

{'='*80}
RAW LLM RESPONSE:
{'='*80}
{response_content}

{'='*80}

"""

            # Dosyaya yaz (sadece will_write True ise)
            if will_write:
                try:
                    with open(log_path, "w", encoding="utf-8") as f:
                        f.write(file_content)
                    logger.info(f"📁 LLM response dosyaya kaydedildi: {log_path}")
                except Exception as file_error:
                    logger.error(f"Response dosya yazma hatası: {file_error}")
            else:
                logger.info(
                    f"📁 LLM response loglama atlandı (will_write=False): {log_file}"
                )

        except Exception as e:
            logger.error(f"LLM response logging hatası: {e}")

    def summarize_finding(
        self, action_description: str, result, user_question: str, thought: str = "", action: str = ""
    ) -> str:
        """Soru-cevap karşılaştırması: Sonuçlar arasından en uygun bulguyu seç ve yanıt olarak döndür"""
        try:
            # Sonuç kontrolü
            if isinstance(result, list) and len(result) > 0:
                # İlk 5 sonucu analiz için al
                sample_data = result[:5] if len(result) > 5 else result
                sample_text = str(sample_data)
                
                # Master LLM'den en uygun bulguyu seçmesini iste
                selection_prompt = f"""KULLANICI SORUSU: {user_question}

YAPILAN İŞLEM: {action_description}
CYPHER SORGUSU: {action}

BULUNAN SONUÇLAR ({len(result)} adet):
{sample_text}

🎯 GÖREV: Bu sonuçlar arasından kullanıcı sorusuna EN UYGUN olanı seç ve tam yanıt olarak döndür.

KURALLAR:
1. Eğer sonuçlar kullanıcı sorusunu cevaplayabiliyorsa → En uygun sonucu seç
2. Eğer birden fazla seçenek varsa → Kullanıcı sorusuna en yakın/uygun olanı seç
3. Aynı isimde birden fazla müşteri varsa → Final answer ile kullanıcıya bunlardan hangisini seçmesi gerektiğini tavsiye et
4. BAŞARILI BULGU ise → Kullanılan Cypher sorgusu ve bulunan node/değerleri belirt ki sonraki iterasyonlarda kullanılabilsin

***KRİTİK***
Eğer elde edilen sonuçlar dan sorudaki içerik bilgisi elde edilememişse vector, semantic arama yapılması gerektiğini belirt.
Metadata bilgileri ile bir sonuç elde edilmiş ama istenen detaylar yoksa (örn: müşteri adı var ama adres yok) → yetersiz olduğunu ve vector, semantic arama yapılması gerektiğini belirt.

YANIT FORMATINI BELİRLE:
- BAŞARILI ise: "✅ [Seçilen sonuç ve detaylar] | NODE/DEĞERLER: [bulunan soru ile ilişkili node,type ve data ilişkisi, sonraki querylerde kullanılabilir]"
- YETERSİZ ise: "⚠️ Bu sonuçlar yetersiz: [neden yetersiz] | NODE/DEĞERLER: [bulunan soru ile ilişkili node, type ve data ilişkisi]"
- KISMEN YETERLİ ise: "⚠️ Bu sonuçlar kısmen yeterli: [neden kısmen yeterli] | NODE/DEĞERLER: [bulunan soru ile ilişkili node type ve data ilişkisi]"
"""

                # Master LLM'den en iyi yanıtı al
                response = self.master_llm.invoke([
                    SystemMessage(content="Sen sonuç analizi uzmanısın. Verilen sonuçlar arasından kullanıcı sorusuna en uygun olanı seçip detaylı yanıt olarak döndürürsün. Bulunan verileri kullanarak kullanıcının sorusunu mümkün olduğunca eksiksiz yanıtlarsın."),
                    HumanMessage(content=selection_prompt)
                ])
                
                # Token logla
                self.log_token_usage(response, -1, "summarize_finding_selection")
                
                # Response'u normalize et
                answer = response.content.strip()
                if isinstance(answer, list):
                    answer = " ".join(str(item) for item in answer).strip()
                
                return answer
                
            else:
                return f"❌ Sonuç yok - '{user_question}' için veri bulunamadı"

        except Exception as e:
            logger.error(f"Sonuç seçimi hatası: {e}")
            # Fallback: basit özet
            return (
                f"✅ {len(result)} kayıt bulundu: {str(result[:1])}..."
                if result
                else f"❌ Sonuç yok - '{user_question}' için veri bulunamadı"
            )

    def analyze_empty_result(
        self, user_question: str, cypher_query: str, query_type: str = "entity", thought: str = "", action: str = ""
    ) -> str:
        """Boş sonuç dönen sorguları ve syntax hatalarını analiz edip alternatif strateji önerir"""
        try:
            # Schema bilgisini al
            try:
                compact_schema = self.get_cached_schema()
                schema_info = f"""NEO4J GRAPH DATABASE SCHEMA:
{compact_schema}

🎯 DOMAIN CONTEXT:
Bu graph database, belge tabanlı bir bilgi sistemidir:
- **Document nodes**: Kaynak belgeler (PDF'ler ve diğer dosyalar)
- **Chunk nodes**: Belgelerin semantic search için parçalanmış içerikleri
- **Entity nodes**: Belgelerden çıkarılmış yapılandırılmış varlıklar
- **Relationship'ler**: Varlıklar arasındaki bağlantılar ve hiyerarşik ilişkiler"""
            except Exception as e:
                logger.warning(f"Schema bilgisi alınamadı: {e}")
                schema_info = "⚠️ Schema bilgisi alınamadı - Graph database bağlantısını kontrol edin"

            # Query type'a göre prompt oluştur
            if query_type == "cypher_error":
                analysis_prompt = f"""Neo4j veritabanında SYNTAX HATASI analizi:

{schema_info}

KULLANICI SORUSU: {user_question}
LLM'İN DÜŞÜNCESI: {thought}
LLM'İN KARARI: {action}

{cypher_query}

🎯 GÖREV: Bu Cypher sorgusu neden hata verdi? SYNTAX HATASINI tespit et ve SADECE TEK CÜMLE ile düzeltilmiş çözümü öner.

SYNTAX HATASI ANALİZİ:
1. Property isimleri schema'ya uygun mu? (örn: fileName vs filename)
3. String normalizasyon fonksiyonları doğru mu? (apoc.text.clean vs)
4. APOC fonksiyonları varsa syntax'ı doğru mu?

SADECE TEK CÜMLE ile çözümü söyle."""
            else:
                analysis_prompt = f"""Neo4j veritabanında boş sorgu analizi:

{schema_info}

KULLANICI SORUSU: {user_question}
LLM'İN DÜŞÜNCESI: {thought}
LLM'İN KARARI: {action}
BOŞ SONUÇ VERDİ: {cypher_query}

🎯 GÖREV: Bu sorgu neden boş döndü? SADECE TEK CÜMLE ile neden ve çözümü söyle. 

ARAMA STRATEJİSİ:
1. Eğer birden fazla kelimeden oluşan bir arama başarısız olursa ayrı ayrı aramayı denemesi için yönlendir
2. Sonuç yoksa → Document node fileName aramaya geç - **ZORUNLU: kelimeler tek başlarında arandıktan sonra bile Entity node'larında bulunmayan bilgiler için Document.fileName'de ara!**
3. **KRİTİK: Vector/Semantic arama gerekiyorsa vector embeddingleri bulması için LLM tool calling yapmasını öner**

Öneriler: string alanlar için mutlaka CONTAINS - apoc.text.clean() öner.

SADECE TEK CÜMLE ile cevap ver."""

            # SystemMessage'ı query type'a göre ayarla
            if query_type == "cypher_error":
                system_message = "Sen Cypher syntax uzmanısın. Cypher syntax hatalarını tespit edip kısa ve net çözüm önerirsin. Schema'ya uygun property/node isimlerini kontrol edersin. SADECE TEK CÜMLE ile çözümü açıklarsın."
            else:
                system_message = "Sen kısa ve net cevap veren Neo4j uzmanısın. Boş sorgu nedenini ve çözümünü SADECE TEK CÜMLE ile açıklarsın. Uzun açıklama yapma!"

            # LLM ile analiz et
            response = self.master_llm.invoke(
                [
                    SystemMessage(content=system_message),
                    HumanMessage(content=analysis_prompt),
                ]
            )

            # Token kullanımını logla
            self.log_token_usage(response, -1, "analyze_empty_result")

            # Response content'i al
            raw_content = response.content
            print("analyze_empty_result response:", raw_content)
            if isinstance(raw_content, list):
                return " ".join(str(item) for item in raw_content).strip()
            else:
                return raw_content.strip()

        except Exception as e:
            logger.error(f"Boş sonuç analizi hatası: {e}")
            # Fallback: basit analiz
            return f"Sorgu '{cypher_query}...' boş sonuç verdi. İsim normalizasyonu ve CONTAINS filtreleri dene."

    def _is_meaningful_result(self, result: List[Dict], user_question: str) -> bool:
        """Sonucun kullanıcı sorusu için anlamlı olup olmadığını kontrol eder - domain agnostic"""
        if not result or len(result) == 0:
            return False
        
        # Tek satır sonuç için özel kontrol (COUNT, SUM vb. aggregate sorguları)
        if len(result) == 1:
            first_row = result[0]
            
            # Tüm değerlerin sayısal ve 0 olup olmadığını kontrol et
            numeric_zero_values = []
            non_zero_values = []
            
            for key, value in first_row.items():
                if isinstance(value, (int, float)):
                    if value == 0:
                        numeric_zero_values.append(key)
                    else:
                        non_zero_values.append((key, value))
                elif value and str(value).strip():  # Non-empty string/object
                    non_zero_values.append((key, value))
            
            # Eğer tüm sayısal değerler 0 ise ve hiç anlamlı değer yoksa
            if numeric_zero_values and not non_zero_values:
                return False  # Anlamsız sonuç - analyze_empty_result'a git
        
        return True  # Anlamlı sonuç

    def add_successful_finding(
        self,
        state: "AgentState",
        iteration: int,
        action: str,
        finding: str,
        relevance_score: float = 0.0,
        raw_data: Any = None,
    ):
        """Başarılı bulguyu context memory ve state'e ekle"""
        # State'e SuccessfulFinding objesi olarak ekle (ham veri ile)
        state_finding = SuccessfulFinding(
            iteration=iteration,
            action_type=action,
            summary=finding,
            relevance_score=relevance_score,
            raw_data=raw_data,
        )
        state.successful_findings.append(state_finding)

        # Context memory'i güncelle
        self.update_context_memory(state)

        logger.info(
            f"Başarılı bulgu eklendi - İterasyon {iteration}: {action} -> {finding}..."
        )

    def add_failed_query(
        self,
        state: "AgentState",
        iteration: int,
        query: str,
        error_message: str,
        query_type: str = "cypher",
    ):
        """Başarısız sorguyu state'e ekle"""
        failed_query = {
            "iteration": iteration,
            "query": query,
            "error": error_message,
            "type": query_type,
            "timestamp": datetime.now().isoformat(),
        }
        state.failed_queries.append(failed_query)

        logger.info(
            f"Başarısız sorgu eklendi - İterasyon {iteration}: {query_type} -> {error_message}..."
        )

    def update_context_memory(self, state: "AgentState"):
        """Başarılı bulgulardan context prompt oluştur - AgentState ile uyumlu"""
        if (
            not state.successful_findings
            and not state.discovered_document_filenames
            and state.failed_entity_query_count == 0
        ):
            self.context_memory = ""
            return

        context_prompt = "## DAHA ÖNCE BULUNAN BAŞARILI BİLGİLER:\n\n"

        # Başarısız entity query denemelerini ekle
        if state.failed_entity_query_count > 0:
            context_prompt += f"**⚠️ ENTITY QUERY DENEMELERİ:**\n"
            context_prompt += f"- Başarısız deneme sayısı: {state.failed_entity_query_count}/{state.max_entity_query_attempts}\n"

            if state.failed_entity_query_count < state.max_entity_query_attempts:
                remaining = (
                    state.max_entity_query_attempts - state.failed_entity_query_count
                )
                context_prompt += f"- Kalan deneme hakkı: {remaining}\n"
                context_prompt += f"- **STRATEJİ**: Filtreleri sadeleştir, daha basit WHERE koşulları kullan\n"
                context_prompt += f"- **ÖNERİ**: Müşteri adının bir kısmını, yılı daha gevşek aramayı dene\n\n"
            else:
                context_prompt += (
                    f"- **SONUÇ**: Entity query limiti aşıldı, vector search'e geç!\n"
                )
                context_prompt += f"- **ZORUNLU**: generate_embeddings_for_cypher TOOL'UNU ÇAĞIR + GDS similarity kullan\n\n"

        # Bulunan document filename'lerini ekle
        if state.discovered_document_filenames:
            context_prompt += "**🔍 KEŞFEDİLEN DOCUMENT FILENAME'LERİ:**\n"
            for i, filename in enumerate(state.discovered_document_filenames, 1):
                context_prompt += f'  {i}. "{filename}"\n'

            context_prompt += (
                f"\n**⚠️ SONRAKİ CHUNK SORGUSUNDA BU FILENAME'LERİ KULLAN:**\n"
            )
            context_prompt += f"```cypher\n"
            context_prompt += (
                f"WHERE d.fileName IN {state.discovered_document_filenames}\n"
            )
            context_prompt += f"```\n"
            context_prompt += f"**TEKRAR filename contains araması yapma!**\n\n"

        for finding in state.successful_findings[-5:]:  # Son 5 başarılı bulguyu al
            context_prompt += (
                f"**Adım {finding.iteration} - {finding.action_type.upper()}:**\n"
            )
            context_prompt += f"{finding.summary}\n"

            # Önemli ham veri örnekleri ekle (özellikle cypher_query için)
            if finding.action_type == "cypher_query" and finding.raw_data:
                context_prompt += f"\n**HAM VERİ ÖRNEKLERİ (İLK 3 SATIR):**\n"
                raw_data = finding.raw_data
                if isinstance(raw_data, list) and len(raw_data) > 0:
                    for i, row in enumerate(raw_data[:3], 1):  # İlk 3 satır
                        if isinstance(row, dict):
                            # Policy bilgilerini özel olarak çıkar
                            if "p" in row and isinstance(row["p"], dict):
                                policy_name = row["p"].get("name", "Bilinmeyen")
                                context_prompt += (
                                    f"  Satır {i}: Poliçe adı: '{policy_name}'\n"
                                )
                            elif "c" in row and isinstance(row["c"], dict):
                                customer_name = row["c"].get(
                                    "fullName", row["c"].get("name", "Bilinmeyen")
                                )
                                context_prompt += (
                                    f"  Satır {i}: Müşteri adı: '{customer_name}'\n"
                                )
                            else:
                                # Genel dict gösterimi
                                context_prompt += f"  Satır {i}: {str(row)}...\n"
                context_prompt += "\n"

            context_prompt += "---\n"

        context_prompt += "\n**🚀 BU BİLGİLERİ KULLANARAK SONRAKI ADIMI BELİRLE**\n\n"
        self.context_memory = context_prompt

    def parse_agent_response(self, response: str) -> Tuple[str, str, Tuple[str, str]]:
        """Agent cevabını parse et - yıldızlı formatları da destekle"""
        
        # Tool call sonrası response'da JSON blokları varsa temizle
        import re
        # JSON formatındaki tool_uses bloklarını temizle
        tool_uses_pattern = r'\{\s*"tool_uses"\s*:\s*\[.*?\]\s*\}'
        response = re.sub(tool_uses_pattern, '', response, flags=re.DOTALL)
        
        # Başka tool call pattern'leri de temizle
        tool_array_pattern = r'\[\s*\{\s*"recipient_name"\s*:\s*"functions\.[^"]+"\s*,.*?\}\s*\]'
        response = re.sub(tool_array_pattern, '', response, flags=re.DOTALL)
        
        # Tek tool call objelerini temizle  
        single_tool_pattern = r'\{\s*"recipient_name"\s*:\s*"functions\.[^"]+"\s*,.*?\}'
        response = re.sub(single_tool_pattern, '', response, flags=re.DOTALL)
        
        # Fazla boşlukları ve newline'ları temizle
        response = re.sub(r'\n\s*\n\s*\n+', '\n\n', response).strip()
        
        # Observation, Thought, Action'ı ayır
        observation = ""
        thought = ""
        action = ""
        action_content = ""
        
        lines = response.strip().split('\n')
        current_section = None
        
        for line in lines:
            line = line.strip()
            # Yıldızlı formatları da destekle
            if line.startswith('Observation:') or line.startswith('**Observation:**'):
                current_section = 'observation'
                observation = line.replace('**Observation:**', '').replace('Observation:', '').strip()
            elif line.startswith('Thought:') or line.startswith('**Thought:**'):
                current_section = 'thought'
                thought = line.replace('**Thought:**', '').replace('Thought:', '').strip()
            elif line.startswith('Action:') or line.startswith('**Action:**'):
                current_section = 'action'
                action = line.replace('**Action:**', '').replace('Action:', '').strip()
            elif line.startswith('Query:'):
                action_content = line.replace('Query:', '').strip()
            elif line.startswith('Answer:'):
                action_content = line.replace('Answer:', '').strip()
            elif line.startswith('Content:') or line.startswith('**Content:**'):
                # "Content:" prefix'ini kaldır ve action_content'e ekle
                content_on_same_line = line.replace('**Content:**', '').replace('Content:', '').strip()
                if content_on_same_line:
                    action_content = content_on_same_line
                current_section = 'content'  # Content section'a geç
            elif current_section and line and not line.startswith('```'):
                # Kod blokları hariç
                if current_section == 'observation':
                    observation += ' ' + line
                elif current_section == 'thought':
                    thought += ' ' + line
                elif current_section == 'action':
                    if not action:
                        action = line
                    else:
                        # Eğer line "Content:" ile başlamıyorsa ve cypher/query değilse action_content'e ekle
                        if not line.startswith('Content:') and not line.startswith('**Content:**'):
                            # Kod blokları action_content'e git
                            if 'MATCH' in line or 'RETURN' in line or 'WHERE' in line:
                                action_content += ' ' + line
                            elif action_content == "":  # İlk content satırı
                                action_content = line
                            else:
                                action_content += ' ' + line
                elif current_section == 'content':
                    # Content section'dayken tüm satırları action_content'e ekle
                    if action_content:
                        action_content += '\n' + line  # Çok satırlı content için yeni satır ekle
                    else:
                        action_content = line  # İlk content satırı
        
        # Kod bloklarını ve gereksiz karakterleri temizle
        if action_content:
            action_content = action_content.replace('```cypher', '').replace('```', '').strip()
            # Başında pipe (|) karakteri varsa kaldır (YAML multiline format)
            if action_content.startswith('|'):
                action_content = action_content[1:].strip()
            # Başında newline varsa kaldır
            action_content = action_content.lstrip('\n').strip()
        
        # ReAct formatı bulunamadıysa fallback: Düz text'i final_answer olarak kabul et
        if not action and not action_content and response.strip():
            # JSON temizlenmiş response'da eğer ReAct formatı yoksa, direk final_answer kabul et
            logger.info("🔍 ReAct formatı bulunamadı, response'u final_answer olarak parse ediliyor")
            action = "final_answer"
            action_content = response.strip()
            thought = "Tool call sonrası direkt final answer alındı"
        
        return observation.strip(), thought.strip(), (action.strip(), action_content.strip())

    def get_available_tools(self) -> List[Dict[str, Any]]:
        """LLM için kullanılabilir tool'ların tanımını döndürür (OpenAI Function Calling formatında)"""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "generate_embeddings_for_cypher",
                    "description": "Cypher sorgusunda kullanmak üzere SADECE İÇERİK KELİMELERİNDEN embedding oluşturur. METADATA (müşteri adı, yıl, poliçe türü) ekleme! Örnek: 'taksit tablosu ödeme planı' ✅, 'ayça hanım 2020 taksit'",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "text": {
                                "type": "string",
                                "description": "SADECE aranacak içerik kavramları - müşteri adı/yıl/tip EKLEMEYÜN! Örnek: 'taksit tutarları ödeme planı', 'prim bilgileri'",
                            }
                        },
                        "required": ["text"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "add_page_resource",
                    "description": "ZORUNLU: Faydalanılan chunk'ların sayfa görsellerini resource listesine ekler. Final answer'da manuel sayfa referansı ekleme, sadece bu tool'u kullan!",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "page_link": {
                                "type": "string",
                                "description": "Sayfa görseli linki - Cypher sonucundan gelen page_link değeri",
                            }
                        },
                        "required": ["page_link"],
                    },
                },
            },
        ]
        return tools

    def handle_tool_calls(self, tool_calls: List[Any]) -> List[Dict[str, Any]]:
        """LLM'den gelen tool call'ları işler ve sonuçları döndürür"""
        tool_results = []

        for tool_call in tool_calls:
            try:
                # Debug: tool_call structure'ını logla
                logger.info(f"🔍 DEBUG Tool Call Structure: {type(tool_call)}")
                logger.info(f"🔍 DEBUG Tool Call Dir: {dir(tool_call)}")

                # OpenAI tool call format'ını handle et (object, dict, veya LangChain format)
                if hasattr(tool_call, "function"):
                    # Object format (LangChain wrapper)
                    function_name = tool_call.function.name
                    function_args = json.loads(tool_call.function.arguments)
                    tool_call_id = tool_call.id
                elif isinstance(tool_call, dict) and "function" in tool_call:
                    # Dict format (direct OpenAI response)
                    function_name = tool_call["function"]["name"]
                    # Arguments might be string or already parsed
                    args = tool_call["function"]["arguments"]
                    if isinstance(args, str):
                        function_args = json.loads(args)
                    else:
                        function_args = args
                    tool_call_id = tool_call["id"]
                elif (
                    isinstance(tool_call, dict)
                    and "name" in tool_call
                    and "args" in tool_call
                ):
                    # LangChain format
                    function_name = tool_call["name"]
                    function_args = tool_call["args"]
                    tool_call_id = tool_call["id"]
                else:
                    logger.error(
                        f"❌ Bilinmeyen tool call format'ı: {type(tool_call)}, content: {tool_call}"
                    )
                    continue

                logger.info(f"🔧 Tool çağrısı: {function_name} - Args: {function_args}")

                if function_name == "generate_embeddings_for_cypher":
                    # Parameter mapping - 'text' veya 'query' parametrelerini destekle
                    if "text" in function_args:
                        text = function_args["text"]
                    elif "query" in function_args:
                        text = function_args["query"]
                    else:
                        logger.error(
                            f"❌ generate_embeddings_for_cypher için gerekli parameter bulunamadı: {function_args}"
                        )
                        tool_result = {
                            "tool_call_id": tool_call_id,
                            "role": "tool",
                            "name": function_name,
                            "content": f"Error: Required parameter 'text' or 'query' not found in {function_args}",
                        }
                        tool_results.append(tool_result)
                        continue

                    logger.info(f"🧠 Embedding oluşturulacak text: {text}")
                    success, result = self.generate_embeddings_for_cypher(text)

                    if success:
                        # Embedding'i saklama - LLM'e SADECE referans ver
                        embedding_id = f"embedding_{tool_call_id}"
                        if not hasattr(self, "_cypher_embeddings"):
                            self._cypher_embeddings = {}
                        self._cypher_embeddings[embedding_id] = result

                        # LLM'e sadece referans bilgisi gönder
                        tool_result_content = f"Embedding başarıyla oluşturuldu. Cypher sorgunda '$embedding_vector' değişkeni olarak kullanabilirsin. Text: '{text}'"
                        tool_result = {
                            "tool_call_id": tool_call_id,
                            "role": "tool",
                            "name": function_name,
                            "content": tool_result_content,
                        }

                        # Embedding vektörünün ilk 20 karakterini logla
                        embedding_str = str(result)
                        logger.info(
                            f"🔍 LLM'e dönen tool result: {tool_result_content}"
                        )
                        logger.info(
                            f"🔍 Embedding vektörü (ilk 20 karakter): {embedding_str[:20]}..."
                        )
                        logger.info(
                            f"✅ Embedding oluşturuldu ve saklandı: {embedding_id}"
                        )

                    else:
                        tool_result = {
                            "tool_call_id": tool_call_id,
                            "role": "tool",
                            "name": function_name,
                            "content": f"Error: {result}",
                        }

                elif function_name == "add_page_resource":
                    page_link = function_args.get("page_link")

                    if not page_link:
                        tool_result = {
                            "tool_call_id": tool_call_id,
                            "role": "tool",
                            "name": function_name,
                            "content": "Error: page_link parametresi gerekli",
                        }
                    else:
                        result_msg = self.resource_manager.add_page_resource(page_link)
                        logger.info(f"📄 Page resource: {result_msg}")

                        tool_result = {
                            "tool_call_id": tool_call_id,
                            "role": "tool",
                            "name": function_name,
                            "content": result_msg,
                        }

                else:
                    tool_result = {
                        "tool_call_id": tool_call_id,
                        "role": "tool",
                        "name": function_name,
                        "content": f"Error: Unknown function {function_name}",
                    }

                tool_results.append(tool_result)
                logger.info(
                    f"✅ Tool sonucu: {function_name} - Success: {success if 'success' in locals() else 'N/A'}"
                )

            except Exception as e:
                logger.error(f"❌ Tool çalıştırma hatası: {e}")
                logger.error(f"❌ Tool call debug info: {tool_call}")
                # Güvenli bir tool_call_id al
                try:
                    if hasattr(tool_call, "id"):
                        tool_call_id = tool_call.id
                    elif isinstance(tool_call, dict) and "id" in tool_call:
                        tool_call_id = tool_call["id"]
                    else:
                        tool_call_id = f"error_{len(tool_results)}"

                    tool_result = {
                        "tool_call_id": tool_call_id,
                        "role": "tool",
                        "name": "error",
                        "content": f"Error: {str(e)}",
                    }
                    tool_results.append(tool_result)
                except Exception as inner_e:
                    logger.error(f"❌ Tool result oluşturma hatası: {inner_e}")

        return tool_results

        return tool_results

    def is_openai_model(self) -> bool:
        """Kullanılan modelin OpenAI modeli olup olmadığını kontrol eder"""
        # LLM tipini kontrol et
        if hasattr(self.llm, "__class__"):
            class_name = self.llm.__class__.__name__
            if "OpenAI" in class_name or "ChatOpenAI" in class_name:
                return True

        # Model adını kontrol et
        if hasattr(self.llm, "model_name"):
            model_name = str(self.llm.model_name).lower()
            if "gpt" in model_name or "openai" in model_name:
                return True

        # Model property'sini kontrol etß
        if hasattr(self.llm, "model"):
            model = str(self.llm.model).lower()
            if "gpt" in model or "openai" in model:
                return True

        return False

    async def search_query_memory_async(self, question: str) -> str:
        """Mem0'dan benzer sorular ve başarılı stratejileri asenkron ara"""
        try:
            if self.memory is None:
                return ""  # Mem0 mevcut değilse boş string döndür

            # Mem0 işlemini thread pool'da çalıştır
            loop = asyncio.get_event_loop()
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(self._search_memory_sync, question)
                result = await loop.run_in_executor(None, lambda: future.result())
                return result

        except Exception as e:
            logger.warning(f"Mem0 async arama hatası: {e}")
            return ""

    def _search_memory_sync(self, question: str) -> str:
        """Mem0 arama işleminin senkron versiyonu"""
        try:
            # Direkt soruyu kullan, kategorize etme
            search_results = self.memory.search(
                f"Benzer soru: {question} - hangi stratejiler başarılı oldu?",
                user_id="query_strategies",
            )

            if not search_results or "results" not in search_results:
                return ""

            # En yüksek skorlu anıları topla
            relevant_memories = []
            for result in search_results["results"][:3]:  # Top 3
                if result.get("score", 0) > 0.7:  # Yüksek benzerlik
                    relevant_memories.append(result["memory"])

            if relevant_memories:
                memory_context = "\n".join(
                    [f"- {memory}" for memory in relevant_memories]
                )
                return f"""
## 🧠 ÖNCEKİ DENEYİMLER (Mem0):
{memory_context}

Bu deneyimleri dikkate alarak strateji belirle."""

            return ""

        except Exception as e:
            logger.warning(f"Mem0 senkron arama hatası: {e}")
            return ""

    def search_query_memory(self, question: str) -> str:
        """Mem0'dan benzer sorular ve başarılı stratejileri ara (senkron wrapper)"""
        try:
            if self.memory is None:
                return ""  # Mem0 mevcut değilse boş string döndür

            # Direkt soruyu kullan, kategorize etme
            search_results = self.memory.search(
                f"Benzer soru: {question} - hangi stratejiler başarılı oldu?",
                user_id="strategy_memory",
            )

            if not search_results or "results" not in search_results:
                return ""

            # En yüksek skorlu anıları topla
            relevant_memories = []
            for result in search_results["results"][:3]:  # Top 3
                if result.get("score", 0) > 0.7:  # Yüksek benzerlik
                    relevant_memories.append(result["memory"])

            if relevant_memories:
                memory_context = "\n".join(
                    [f"- {memory}" for memory in relevant_memories]
                )
                return f"""
## 🧠 ÖNCEKİ DENEYİMLER (Mem0):
{memory_context}

Bu deneyimleri dikkate alarak strateji belirle."""

            return ""

        except Exception as e:
            logger.warning(f"Mem0 arama hatası: {e}")
            return ""

    async def store_query_strategy_async(
        self, question: str, strategies: List[Dict], final_success: bool
    ):
        """Denenen stratejileri ve sonuçları Mem0'a asenkron kaydet"""
        try:
            if self.memory is None:
                return  # Mem0 mevcut değilse fonksiyonu atla

            # Mem0 işlemini thread pool'da çalıştır
            loop = asyncio.get_event_loop()
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    self._store_strategy_sync, question, strategies, final_success
                )
                await loop.run_in_executor(None, lambda: future.result())

        except Exception as e:
            logger.warning(f"Mem0 async kaydetme hatası: {e}")

    def _store_strategy_sync(
        self, question: str, strategies: List[Dict], final_success: bool
    ):
        """Mem0 kaydetme işleminin senkron versiyonu - SADECE STRATEJİ BİLGİLERİ"""
        try:
            # Başarılı ve başarısız stratejileri ayır
            successful_strategies = [s for s in strategies if s.get("success", False)]
            failed_strategies = [s for s in strategies if not s.get("success", False)]

            # Memory mesajları oluştur - SADECE STRATEJİ BİLGİLERİ, KULLANICI SORULARI YOK
            messages = []

            # Soru tipini kategorize et (kişi adı, tarih vs metadata'yı kaldır)
            question_type = self._categorize_question_type(question)

            # Başarısız denemeler
            if failed_strategies:
                failed_list = [
                    f"{s.get('strategy', 'unknown')}: {s.get('reason', 'belirsiz')}"
                    for s in failed_strategies
                ]
                messages.append(
                    {
                        "role": "system",
                        "content": f"Soru tipi: '{question_type}' - Başarısız stratejiler: {', '.join(failed_list)}",
                    }
                )

            # Başarılı strateji
            if successful_strategies and final_success:
                successful_strategy = successful_strategies[-1]  # Son başarılı
                strategy_detail = f"{successful_strategy.get('strategy', 'unknown')}"
                if successful_strategy.get("details"):
                    strategy_detail += f" - {successful_strategy['details']}"

                messages.append(
                    {
                        "role": "system",
                        "content": f"Soru tipi: '{question_type}' - BAŞARILI strateji: {strategy_detail}. Bu strateji çalıştı.",
                    }
                )

            # Mem0'a kaydet
            if messages:
                self.memory.add(messages, user_id="strategy_memory")
                logger.info(f"Mem0'a kaydedilen strateji: {len(messages)} mesaj")

        except Exception as e:
            logger.warning(f"Mem0 senkron kaydetme hatası: {e}")

    def _categorize_question_type(self, question: str) -> str:
        """Soruyu kategorize et, kişi adlarını ve spesifik detayları kaldır"""
        question_lower = question.lower()

        # Soru tiplerini belirle
        if any(word in question_lower for word in ["taksit", "ödeme", "prim"]):
            return "taksit_ödeme_sorgusu"
        elif any(word in question_lower for word in ["poliçe", "sigorta"]):
            return "poliçe_bilgi_sorgusu"
        elif any(word in question_lower for word in ["listele", "list", "göster"]):
            return "listeleme_sorgusu"
        elif any(word in question_lower for word in ["ara", "bul", "search"]):
            return "arama_sorgusu"
        else:
            return "genel_sorgu"

    def store_query_strategy(
        self, question: str, strategies: List[Dict], final_success: bool
    ):
        """Denenen stratejileri ve sonuçları Mem0'a kaydet (senkron wrapper)"""
        try:
            if self.memory is None:
                return  # Mem0 mevcut değilse fonksiyonu atla

            # Başarılı ve başarısız stratejileri ayır
            successful_strategies = [s for s in strategies if s.get("success", False)]
            failed_strategies = [s for s in strategies if not s.get("success", False)]

            # Memory mesajları oluştur
            messages = []

            # Soru tipini kategorize et (kişi adı, tarih vs metadata'yı kaldır)
            question_type = self._categorize_question_type(question)

            # Başarısız denemeler
            if failed_strategies:
                failed_list = [
                    f"{s.get('strategy', 'unknown')}: {s.get('reason', 'belirsiz')}"
                    for s in failed_strategies
                ]
                messages.append(
                    {
                        "role": "system",
                        "content": f"Soru tipi: '{question_type}' - Başarısız stratejiler: {', '.join(failed_list)}",
                    }
                )

            # Başarılı strateji
            if successful_strategies and final_success:
                successful_strategy = successful_strategies[-1]  # Son başarılı
                strategy_detail = f"{successful_strategy.get('strategy', 'unknown')}"
                if successful_strategy.get("details"):
                    strategy_detail += f" - {successful_strategy['details']}"

                messages.append(
                    {
                        "role": "system",
                        "content": f"Soru tipi: '{question_type}' - BAŞARILI strateji: {strategy_detail}. Bu strateji çalıştı.",
                    }
                )

            # Mem0'a kaydet
            if messages:
                self.memory.add(messages, user_id="strategy_memory")
                logger.info(f"Mem0'a kaydedilen strateji: {len(messages)} mesaj")

        except Exception as e:
            logger.warning(f"Mem0 kaydetme hatası: {e}")

    def track_strategy_attempt(
        self, strategy: str, success: bool, reason: str = "", details: str = ""
    ):
        """Strateji denemesini takip et"""
        self.query_strategies.append(
            {
                "strategy": strategy,
                "success": success,
                "reason": reason,
                "details": details,
                "timestamp": datetime.now().isoformat(),
            }
        )

    def solve_question(
        self, user_question: str, session_id: str = None
    ) -> Dict[str, Any]:
        """Ana problem çözme fonksiyonu - ReAct pattern ile Chunk-based arama"""

        # Session ID'yi set et
        self.current_session_id = session_id or "unknown"

        # Her soru için cache'i temizle
        self.context_memory = ""
        self.token_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self.detailed_token_usage = []  # Detaylı token tracking'i temizle
        self.query_strategies = []  # Strateji takibini temizle

        # YENİ: Resource Manager'ı temizle
        self.resource_manager.clear_resources()
        logger.info("🧹 Resource Manager temizlendi")

        # 🧠 MEM0: Önceki deneyimleri ara (background thread'de) - DEVRE DIŞI
        memory_context_future = None
        # try:
        #     # Shared thread pool executor ile background'da memory aramayı başlat
        #     memory_context_future = self._memory_executor.submit(
        #         self.search_query_memory, user_question
        #     )
        #     logger.info("🧠 Mem0 aramasi background thread'de başlatıldı")
        # except Exception as e:
        #     logger.warning(f"🧠 Mem0 background thread oluşturulamadı: {e}")
        logger.info("🧠 Mem0 özelliği devre dışı bırakıldı")

        logger.info(f"Soru çözülüyor: {user_question}")
        if session_id:
            logger.info(f"Session ID: {session_id}")

        # Conversation history al (eğer session_id varsa)
        conversation_context = ""
        previous_messages = []
        if session_id:
            from src.QA_integration import get_history_by_session_id

            conversation_context = get_history_by_session_id(
                session_id, self.graph, write_access=True
            )
            if conversation_context and hasattr(conversation_context, "messages"):
                logger.info(
                    f"Conversation history alındı: {len(conversation_context.messages)} mesaj"
                )
                
                # DEBUG: Mesajları detaylı logla
                for i, msg in enumerate(conversation_context.messages):
                    logger.info(f"DEBUG Mesaj {i}: type={type(msg)}, content_length={len(msg.content) if hasattr(msg, 'content') else 'N/A'}, role={getattr(msg, 'role', 'N/A')}")

                # Son mesajı hariç tut (henüz işlenen soruyu dahil etme)
                all_messages = (
                    conversation_context.messages[:-1]
                    if conversation_context.messages
                    else []
                )

                # Son 40 mesajı al (son mesaj hariç)
                recent_messages = (
                    all_messages[-40:] if len(all_messages) > 40 else all_messages
                )

                # İlk mesajın Human olduğundan emin ol
                if (
                    recent_messages
                    and recent_messages[0].type != "human"
                    and "Human" not in str(type(recent_messages[0]))
                ):
                    # Eğer ilk mesaj AI ise, bir önceki Human mesajından başla
                    for i in range(len(recent_messages)):
                        if recent_messages[i].type == "human" or "Human" in str(
                            type(recent_messages[i])
                        ):
                            recent_messages = recent_messages[i:]
                            break

                # Mesajları format et
                for msg in recent_messages:
                    if hasattr(msg, "content"):
                        msg_type = (
                            "Human"
                            if hasattr(msg, "type") and msg.type == "human"
                            else "AI"
                        )
                        if hasattr(msg, "type"):
                            if msg.type == "human" or "Human" in str(type(msg)):
                                msg_type = "Human"
                            else:
                                msg_type = "AI"
                        previous_messages.append(f"{msg_type}: {msg.content}")

                # Conversation context string oluştur
                if previous_messages:
                    conversation_context = f"""
## 📝 ÖNCEKI KONUŞMA:
{chr(10).join(previous_messages)}

"""
                    logger.info(
                        f"Conversation context oluşturuldu: {len(previous_messages)} mesaj (son mesaj hariç, son 40'tan kesilen)"
                    )
                else:
                    conversation_context = ""

            elif conversation_context:
                logger.info(
                    f"Conversation history alındı: {type(conversation_context)}"
                )
                conversation_context = ""
            else:
                conversation_context = ""
        # Agent state'i başlat
        state = AgentState(question=user_question, original_question=user_question)

        # Embedding storage için
        self._current_embeddings = {}
        
        # Cypher embedding storage
        self._cypher_embeddings = {}


        # Schema-based system prompt'u al (cache'den veya oluştur)
        system_prompt = self.get_system_prompt()

        conversation_history = []
        final_answer = None

        # İlk durum bilgisi
        current_observation = f"Kullanıcı sorusu: '{user_question}'"

        while state.iteration_count < self.max_iterations and not final_answer:
            state.iteration_count += 1
            logger.info(f"İterasyon {state.iteration_count}")

            # 🧠 MEM0: Background memory aramayı kontrol et (non-blocking) - DEVRE DIŞI
            # if memory_context_future and state.iteration_count == 1:
            #     try:
            #         # Memory sonucunu kontrol et (timeout olmadan, sadece hazırsa al)
            #         if memory_context_future.done():
            #             memory_context = memory_context_future.result()
            #             if memory_context and memory_context not in self.context_memory:
            #                 self.context_memory += memory_context + "\n"
            #                 logger.info(
            #                     "🧠 Mem0'dan önceki deneyimler ilk iterasyonda eklendi"
            #                 )
            #     except Exception as e:
            #         logger.warning(f"🧠 Memory check hatası: {e}")

            # LLM'e gönderilecek mesaj - mevcut state bilgileriyle zenginleştir
            context_info = ""

            # İterasyon bilgisini ekle
            context_info += f"\n\n**İTERASYON DURUMU: {state.iteration_count}/{self.max_iterations}**"
            context_info += f"\n**SORU ANALİZİ:** {user_question}"

            # Successful findings'i ekle
            if state.successful_findings:
                logger.info(
                    f"🔍 Successful findings mevcut: {len(state.successful_findings)} adet"
                )
                context_info += f"\n\n**ÖNCEKİ BAŞARILI BULGULAR:**\n"
                for finding in state.successful_findings:
                    logger.info(
                        f"🔍 Adding finding: İterasyon {finding.iteration} ({finding.action_type})"
                    )
                    context_info += f"- İterasyon {finding.iteration} ({finding.action_type}): {finding.summary}\n"
                    # Eğer cypher_query ise, ham sonuçları da göster
                    if finding.action_type == "cypher_query" and finding.raw_data:
                        sample_results = finding.raw_data[:3]  # İlk 3 sonucu göster
                        context_info += f"  📊 Sonuç Örnekleri: {sample_results}\n"
                        context_info += (
                            f"  ⚠️ Bu sorgu zaten başarılı! Aynı sorguyu tekrarlama.\n"
                        )

                # Başarılı bulgular varsa mevcut durum bilgisini de ekle
                context_info += f"\n\n**Mevcut Durum:**\n- {len(state.successful_findings)} başarılı bulgu toplandı\n- İterasyon: {state.iteration_count}/{self.max_iterations}\n"
            else:
                logger.info(f"🔍 Successful findings boş!")

            # Başarısız sorguları ekle
            if state.failed_queries:
                logger.info(
                    f"🔍 Failed queries mevcut: {len(state.failed_queries)} adet"
                )
                context_info += f"\n\n**BAŞARISIZ SORGULAR (TEKRARLAMA!):**\n"
                for failed in state.failed_queries[
                    -5:
                ]:  # Son 5 başarısız sorguyu göster
                    context_info += f"- İterasyon {failed['iteration']}: {failed['query'][:100]}... -> HATA: {failed['error'][:100]}...\n"
                context_info += "⚠️ Bu sorguları tekrarlama! Farklı yaklaşım dene.\n"
            else:
                logger.info(f"🔍 Failed queries boş!")

            if state.discovered_chunks:
                context_info += f"\n\nMevcut Durum:\n- {len(state.discovered_chunks)} chunk keşfedildi\n- En yüksek relevance: {max([c.relevance_score for c in state.discovered_chunks]):.3f}\n- Toplanan dokümolar: {list(set([c.document_name for c in state.discovered_chunks]))}"

                # CRITICAL: Chunk içeriklerini de LLM'e ver ki analiz edebilsin!
                sorted_chunks = sorted(
                    state.discovered_chunks,
                    key=lambda x: x.relevance_score,
                    reverse=True,
                )
                chunk_contents = (
                    "\n\n**KEŞFEDİLEN CHUNK İÇERİKLERİ (MUTLAKA ANALİZ ET!):**\n"
                )
                for i, chunk in enumerate(sorted_chunks[:15], 1):  # Top 15 chunk
                    chunk_contents += f"\n--- Chunk {i} (Relevance: {chunk.relevance_score:.3f}, Sayfa: {chunk.page_number}) ---\n"
                    chunk_contents += f"{chunk.text}\n"

                context_info += chunk_contents

            # Context memory, conversation context ve mevcut durum bilgilerini birleştir
            # Final answer yönlendirmesi
            if state.successful_findings:
                final_answer_hint = "\n\n🎯 **ÖNEMLI:** Yukarıdaki başarılı bulgular soruyu cevaplamak için yeterli olabilir! Aynı sorguları tekrarlama, bunun yerine final_answer ver!"
            else:
                final_answer_hint = ""

            prompt = f"{conversation_context}{self.context_memory}{current_observation}{context_info}{final_answer_hint}\n\nBu duruma göre next action'ını belirle:"

            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=prompt),
            ]

            # LLM prompt'unu logla - tam mesajlar ile birlikte
            self.log_llm_prompt(system_prompt, prompt, state.iteration_count, messages)

            try:
                # OpenAI model kontrolü ve tool calling desteği
                if self.is_openai_model():
                    # OpenAI tool calling desteği - model'i tool'larla bind et
                    tools = self.get_available_tools()
                    model_with_tools = self.llm.bind_tools(tools)
                    response = model_with_tools.invoke(messages)
                    print("TOOLCALL RESPONSE", response)
                    # Tool call var mı kontrol et
                    if hasattr(response, "tool_calls") and response.tool_calls:
                        logger.info(
                            f"🔧 {len(response.tool_calls)} tool call algılandı"
                        )

                        # Tool call'ları işle
                        tool_results = self.handle_tool_calls(response.tool_calls)

                        # Tool sonuçlarını conversation'a ekle ve tekrar LLM'e gönder
                        messages.append(response)  # Assistant response'u ekle

                        # Tool result'larını ekle
                        for tool_result in tool_results:
                            from langchain_core.messages import ToolMessage

                            messages.append(
                                ToolMessage(
                                    content=tool_result["content"],
                                    tool_call_id=tool_result["tool_call_id"],
                                )
                            )

                        # Tool call'lardan ham veriyi state'e ekle - Şimdilik yok
                        # Çünkü tool sadece embedding oluşturuyor, sonuç Cypher'da kullanılıyor

                        # print("LLMCALL", messages)
                        # Tool sonuçları ile tekrar LLM'e sor - bind edilmiş model'i kullan
                        response = model_with_tools.invoke(messages)

                        # Response content'i al - GPT-5-mini için list kontrolü
                        raw_content = response.content
                        if isinstance(raw_content, list):
                            agent_response = " ".join(str(item) for item in raw_content)
                        else:
                            agent_response = raw_content

                        # Tool call'lar tamamlandı durumu
                        embedding_count = len(getattr(self, "_cypher_embeddings", {}))
                        current_observation = f"Tool calls tamamlandı: {len(response.tool_calls)} tool çağrısı, {embedding_count} embedding oluşturuldu. Cypher'da $embedding_vector kullanabilirsin."

                        logger.info("✅ Tool call'lar işlendi ve final response alındı")
                    else:
                        logger.info("⚠️ Tool call bulunamadı, text parsing ile devam ediliyor")
                        # Response content'i al - GPT-5-mini için list kontrolü
                        raw_content = response.content
                        if isinstance(raw_content, list):
                            agent_response = " ".join(str(item) for item in raw_content)
                        else:
                            agent_response = raw_content
                else:
                    # Non-OpenAI modeller için standart invoke
                    response = self.llm.invoke(messages)

                    # Response content'i al - GPT-5-mini için list kontrolü
                    raw_content = response.content
                    if isinstance(raw_content, list):
                        agent_response = " ".join(str(item) for item in raw_content)
                    else:
                        agent_response = raw_content
                # print("agent_response", agent_response)
                # Response'u parse et - action type'ını almak için önce parse
                observation, thought, (action, action_content) = (
                    self.parse_agent_response(agent_response)
                )

                # LLM response'unu dosyaya kaydet
                # self.log_llm_response(
                #     agent_response, state.iteration_count, action, action_content, True
                # )  # will_write=False ile sadece debug log

                # Token kullanımını action type ile logla
                self.log_token_usage(response, state.iteration_count, action)

                # DEBUG: LLM response'unu logla
                logger.info(f"🔍 DEBUG - Raw LLM Response:\n{agent_response}")

                # Action'ı uygula
                if action == "final_answer":
                    # Final answer - temiz cevabı direkt kullan, sadece referansları ekle
                    clean_answer = action_content.strip()

                    # LLM yorumlama aktifse eski metodu kullan, değilse sadece referans ekle
                    if self.enable_llm_interpretation:
                        logger.info(
                            "🤖 LLM yorumlama aktif - Ham veriler yorumlanıyor..."
                        )

                        # Ham veri bölümlerini topla (eski yöntem için)
                        raw_data_sections = []

                        # Successful findings (cypher raw sonuçları)
                        if state.successful_findings:
                            findings_data = []
                            for finding in state.successful_findings:
                                if finding.raw_data:  # Ham JSON data varsa
                                    # Neo4j DateTime objelerini serialize edilebilir hale getir
                                    serialized_data = serialize_neo4j_data(
                                        finding.raw_data
                                    )
                                    findings_data.append(
                                        {
                                            "action_type": finding.action_type,
                                            "iteration": finding.iteration,
                                            "raw_data": serialized_data,
                                            "summary": finding.summary,
                                        }
                                    )
                            if findings_data:
                                raw_data_sections.append(
                                    f"=== CYPHER HAM VERİLER ===\n{json.dumps(findings_data, ensure_ascii=False, indent=2)}"
                                )

                        # Chunk'ları (varsa)
                        if state.discovered_chunks:
                            sorted_chunks = sorted(
                                state.discovered_chunks,
                                key=lambda x: x.relevance_score,
                                reverse=True,
                            )
                            chunk_content = []
                            for i, chunk in enumerate(
                                sorted_chunks[:10], 1
                            ):  # En iyi 10 chunk
                                chunk_content.append(
                                    f"[Kaynak {i}: {chunk.document_name}, Sayfa {chunk.page_number}]\n{chunk.text.strip()}"
                                )
                            raw_data_sections.append(
                                f"=== KAYNAK BİLGİLER ===\n"
                                + "\n\n".join(chunk_content)
                            )

                        # Raw final answer'ı oluştur
                        if raw_data_sections:
                            raw_final_answer = (
                                f"{clean_answer}\n\n--- HAM VERİLER ---\n"
                                + "\n\n".join(raw_data_sections)
                            )
                        else:
                            raw_final_answer = clean_answer

                        # Ham veriyi LLM'e aktar
                        final_answer = self.interpret_final_answer_with_llm(
                            raw_final_answer, user_question, state.discovered_chunks
                        )
                    else:
                        logger.info(
                            "✨ YENİ BASİT REFERANS SİSTEMİ - Resource Manager kullanılıyor"
                        )
                        # Yeni basit sistem: Resource Manager'dan kaynakları al ve ekle
                        final_answer = self.format_final_answer_with_references_simple(
                            clean_answer
                        )

                    current_observation = (
                        f"Final answer verildi: {final_answer}..."
                    )
                    logger.info(f"Agent final answer verdi: {final_answer[:200]}...")

                    # Conversation history'ye ekle
                    conversation_history.append(
                        f"İterasyon {state.iteration_count}:\nObservation: {observation}\nThought: {thought}\nAction: {action}\nContent: {action_content}...\nSonuç: {current_observation}"
                    )
                    break

                elif action == "cypher_query":
                    success, result = self.execute_cypher_query(action_content)
                    if success and result:
                        # Sonuç anlamlı mı kontrol et (domain-agnostic)
                        if not self._is_meaningful_result(result, user_question):
                            logger.info(f"❌ Cypher sonucu anlamsız (aggregate=0), analyze_empty_result'a yönlendiriliyor")
                            
                            # Boş sonuç analizi yap
                            analysis = self.analyze_empty_result(
                                user_question, action_content, "cypher", thought, action
                            )
                            
                            # Context memory'e başarısız sorgu olarak ekle
                            # self.add_failed_query(
                            #     state, state.iteration_count, action_content, 
                            #     f"Anlamsız sonuç (aggregate=0): {result}", "cypher"
                            # )
                            
                            current_observation = f"Anlamsız sonuç: {result}. Analiz: {analysis}"
                            
                            # Conversation history'ye ekle
                            conversation_history.append(
                                f"İterasyon {state.iteration_count}:\nObservation: {observation}\nThought: {thought}\nAction: {action}\nContent: {action_content[:100]}...\nSonuç: {current_observation}"
                            )
                            continue  # Bir sonraki iterasyona geç
                        
                        # Cypher sonuçlarından document filename'lerini çıkar
                        document_filenames_found = []
                        for row in result:
                            if isinstance(row, dict):
                                # Policy source_file alanını kontrol et
                                if "p.source_file" in row and row["p.source_file"]:
                                    filename = row["p.source_file"]
                                    if (
                                        filename
                                        not in state.discovered_document_filenames
                                        and filename not in document_filenames_found
                                    ):
                                        document_filenames_found.append(filename)
                                        state.discovered_document_filenames.append(
                                            filename
                                        )

                                # Document fileName alanını kontrol et
                                if "d.fileName" in row and row["d.fileName"]:
                                    filename = row["d.fileName"]
                                    if (
                                        filename
                                        not in state.discovered_document_filenames
                                        and filename not in document_filenames_found
                                    ):
                                        document_filenames_found.append(filename)
                                        state.discovered_document_filenames.append(
                                            filename
                                        )

                                # fileName field'ını kontrol et
                                if "fileName" in row and row["fileName"]:
                                    filename = row["fileName"]
                                    if (
                                        filename
                                        not in state.discovered_document_filenames
                                        and filename not in document_filenames_found
                                    ):
                                        document_filenames_found.append(filename)
                                        state.discovered_document_filenames.append(
                                            filename
                                        )

                        # Cypher sonuçlarını basit şekilde işle - otomatik chunk arama yapmadan
                        data_summary = []
                        for row in result[:5]:  # İlk 5 sonucu özetle
                            row_summary = []
                            for key, value in row.items():
                                if value is not None and str(value).strip():
                                    if isinstance(value, list):
                                        row_summary.append(
                                            f"{key}: {', '.join(map(str, value))}"
                                        )
                                    else:
                                        row_summary.append(f"{key}: {value}")
                            if row_summary:
                                data_summary.append(", ".join(row_summary))

                        # Document filename bilgisini duruma ekle
                        filename_info = ""
                        if document_filenames_found:
                            filename_info = f" Document filenames keşfedildi: {document_filenames_found}. Sonraki chunk sorgusunda bu filename'leri kullan!"

                        current_observation = f"Cypher sorgusu başarılı: {len(result)} sonuç bulundu. Örnek veriler: {'; '.join(data_summary[:2])}.{filename_info} Bu veri soru için yeterliyse final_answer ver"

                        # 🧠 MEM0: Başarılı stratejiyi takip et - DEVRE DIŞI
                        # strategy_type = self._classify_cypher_strategy(action_content)
                        # self.track_strategy_attempt(
                        #     strategy=f"cypher_query_{strategy_type}",
                        #     success=True,
                        #     details=f"Başarılı sorgu: {action_content[:100]}...",
                        #     reason=f"{len(result)} sonuç bulundu",
                        # )

                        # Başarılı cypher sorgu bulgusunu kaydet
                        summary = self.summarize_finding(
                            f"Cypher Query: {action_content}", 
                            result, 
                            user_question,
                            thought,  # LLM'in düşüncesi ayrı parametre
                            action    # LLM'in kararı ayrı parametre
                        )
                        self.add_successful_finding(
                            state,  # state parametresi eklendi
                            state.iteration_count,
                            "cypher_query",
                            summary,
                            0.9,  # Yüksek relevance - sonuçlar mevcut
                            result,  # Ham cypher sonuçları
                        )

                        # Cypher sonuçlarından chunk bilgilerini çıkar ve discovered_chunks'a ekle
                        chunks_found = 0
                        for row in result:
                            # Chunk bilgilerini içeren sonuçları ara
                            if isinstance(row, dict):
                                chunk_text = None
                                chunk_id = None
                                page_number = None
                                document_name = None

                                # Farklı chunk field'larını kontrol et
                                if "text" in row:
                                    chunk_text = row["text"]
                                    chunk_id = row.get(
                                        "chunkId",
                                        row.get(
                                            "chunk_id",
                                            f"cypher_{state.iteration_count}_{chunks_found}",
                                        ),
                                    )
                                    page_number = row.get(
                                        "pageNumber", row.get("page_number", 0)
                                    )
                                    document_name = row.get(
                                        "documentFileName",
                                        row.get("document_name", "Unknown"),
                                    )
                                elif (
                                    "c.text" in row
                                ):  # Direct field return (c.text, c.chunkId, etc.)
                                    chunk_text = row["c.text"]
                                    chunk_id = row.get(
                                        "c.chunkId",
                                        f"cypher_{state.iteration_count}_{chunks_found}",
                                    )
                                    page_number = row.get("c.page_number", 0)
                                    # Document name'i çeşitli alanlardan almaya çalış
                                    document_name = (
                                        row.get("document_name")
                                        or row.get("d.fileName")
                                        or row.get("fileName")
                                    )

                                    # Eğer document name bulunamazsa, chunk ID'den document bilgisini alalım
                                    if not document_name and chunk_id:
                                        try:
                                            doc_query = "MATCH (c:Chunk {chunkId: $chunk_id})-[:PART_OF]->(d:Document) RETURN d.fileName as fileName"
                                            doc_result = self.graph.query(
                                                doc_query, {"chunk_id": chunk_id}
                                            )
                                            if doc_result and len(doc_result) > 0:
                                                document_name = doc_result[0].get(
                                                    "fileName", "Unknown"
                                                )
                                            else:
                                                document_name = "Unknown"
                                        except Exception as e:
                                            logger.warning(
                                                f"Document name alınamadı chunk {chunk_id} için: {e}"
                                            )
                                            document_name = "Unknown"
                                    else:
                                        document_name = document_name or "Unknown"
                                elif (
                                    "node.text" in row
                                ):  # Neo4j return (node.text, node.chunkId, etc.)
                                    chunk_text = row["node.text"]
                                    chunk_id = row.get(
                                        "node.chunkId",
                                        f"cypher_{state.iteration_count}_{chunks_found}",
                                    )
                                    page_number = row.get("node.page_number", 0)
                                    document_name = (
                                        row.get("document_name")
                                        or row.get("d.fileName")
                                        or row.get("fileName")
                                    )

                                    # Eğer document name bulunamazsa, chunk ID'den document bilgisini alalım
                                    if not document_name and chunk_id:
                                        try:
                                            doc_query = "MATCH (c:Chunk {chunkId: $chunk_id})-[:PART_OF]->(d:Document) RETURN d.fileName as fileName"
                                            doc_result = self.graph.query(
                                                doc_query, {"chunk_id": chunk_id}
                                            )
                                            if doc_result and len(doc_result) > 0:
                                                document_name = doc_result[0].get(
                                                    "fileName", "Unknown"
                                                )
                                            else:
                                                document_name = "Unknown"
                                        except Exception as e:
                                            logger.warning(
                                                f"Document name alınamadı chunk {chunk_id} için: {e}"
                                            )
                                            document_name = "Unknown"
                                    else:
                                        document_name = document_name or "Unknown"
                                elif "c" in row and isinstance(row["c"], dict):
                                    chunk_data = row["c"]
                                    chunk_text = chunk_data.get("text")
                                    chunk_id = chunk_data.get(
                                        "chunkId",
                                        f"cypher_{state.iteration_count}_{chunks_found}",
                                    )
                                    page_number = chunk_data.get("pageNumber", 0)
                                    document_name = chunk_data.get("fileName")

                                    # Eğer document name bulunamazsa, chunk ID'den document bilgisini alalım
                                    if not document_name and chunk_id:
                                        try:
                                            doc_query = "MATCH (c:Chunk {chunkId: $chunk_id})-[:PART_OF]->(d:Document) RETURN d.fileName as fileName"
                                            doc_result = self.graph.query(
                                                doc_query, {"chunk_id": chunk_id}
                                            )
                                            if doc_result and len(doc_result) > 0:
                                                document_name = doc_result[0].get(
                                                    "fileName", "Unknown"
                                                )
                                            else:
                                                document_name = "Unknown"
                                        except Exception as e:
                                            logger.warning(
                                                f"Document name alınamadı chunk {chunk_id} için: {e}"
                                            )
                                            document_name = "Unknown"
                                    else:
                                        document_name = document_name or "Unknown"
                                elif "chunk" in row and isinstance(row["chunk"], dict):
                                    chunk_data = row["chunk"]
                                    chunk_text = chunk_data.get("text")
                                    chunk_id = chunk_data.get(
                                        "chunkId",
                                        f"cypher_{state.iteration_count}_{chunks_found}",
                                    )
                                    page_number = chunk_data.get("pageNumber", 0)
                                    document_name = chunk_data.get("fileName")

                                    # Eğer document name bulunamazsa, chunk ID'den document bilgisini alalım
                                    if not document_name and chunk_id:
                                        try:
                                            doc_query = "MATCH (c:Chunk {chunkId: $chunk_id})-[:PART_OF]->(d:Document) RETURN d.fileName as fileName"
                                            doc_result = self.graph.query(
                                                doc_query, {"chunk_id": chunk_id}
                                            )
                                            if doc_result and len(doc_result) > 0:
                                                document_name = doc_result[0].get(
                                                    "fileName", "Unknown"
                                                )
                                            else:
                                                document_name = "Unknown"
                                        except Exception as e:
                                            logger.warning(
                                                f"Document name alınamadı chunk {chunk_id} için: {e}"
                                            )
                                            document_name = "Unknown"
                                    else:
                                        document_name = document_name or "Unknown"

                                # Geçerli chunk bulunursa discovered_chunks'a ekle
                                if (
                                    chunk_text and len(chunk_text.strip()) > 10
                                ):  # Minimum uzunluk kontrolü
                                    chunk_info = ChunkInfo(
                                        chunk_id=chunk_id,
                                        text=chunk_text,
                                        page_number=page_number,
                                        document_name=document_name,
                                        relevance_score=0.9,  # Cypher sonuçları yüksek relevance
                                        document_metadata={},
                                        split_texts=[],
                                        split_scores=[],
                                    )
                                    state.discovered_chunks.append(chunk_info)
                                    chunks_found += 1

                        if chunks_found > 0:
                            logger.info(
                                f"🔍 Cypher query'den {chunks_found} chunk discovered_chunks'a eklendi"
                            )
                            current_observation += (
                                f" ({chunks_found} chunk discovered_chunks'a eklendi)"
                            )

                        # Conversation history'ye ekle - Başarılı cypher query
                        conversation_history.append(
                            f"İterasyon {state.iteration_count}:\nObservation: {observation}\nThought: {thought}\nAction: {action}\nContent: {action_content[:100]}...\nSonuç: {current_observation}"
                        )

                    elif success and not result:
                        # Başarılı sorgu ama boş sonuç - analiz et ve öneride bulun
                        logger.info(f"Cypher sorgusu boş sonuç döndürdü: {action_content}")
                        
                        # LLM ile boş sonucu analiz et
                        analysis = self.analyze_empty_result(
                            user_question, action_content, "entity", thought, action
                        )
                        
                        current_observation = f"Sorgu başarılı ama 0 kayıt bulundu. Analiz: {analysis} "
                        
                        # Bu başarısız bulguyu context memory'ye ekle
                        # self.add_failed_query(
                        #     state,
                        #     state.iteration_count,
                        #     action_content,
                        #     f"Boş sonuç analizi: {analysis}",
                        #     "cypher"
                        # )
                        
                        # Conversation history'ye ekle - Boş sonuç cypher query
                        conversation_history.append(
                            f"İterasyon {state.iteration_count}:\nObservation: {observation}\nThought: {thought}\nAction: {action}\nContent: {action_content[:100]}...\nSonuç: {current_observation}"
                        )

                    else:
                        # Cypher query başarısız - failover stratejisi ve analiz
                        state.failed_entity_query_count += 1

                        # LLM ile başarısız sorguyu analiz et - error message'ı da geç
                        error_info = f"CYPHER SORGU: {action_content}\n\nHATA MESAJI: {result}"
                        analysis = self.analyze_empty_result(
                            user_question, error_info, "cypher_error", thought, action
                        )

                        # Eğer vector search'e geçmeden önce daha fazla entity query denemesi yapalım
                        if (
                            state.failed_entity_query_count
                            <= state.max_entity_query_attempts
                        ):
                            logger.info(
                                f"⚠️ Entity query başarısız ({state.failed_entity_query_count}/{state.max_entity_query_attempts}). LLM analizi: {analysis[:100]}..."
                            )

                            # Başarısız sorguyu state'e ekle
                            # self.add_failed_query(
                            #     state,
                            #     state.iteration_count,
                            #     action_content,
                            #     str(result),
                            #     "cypher",
                            # )

                            current_observation = f"Cypher sorgusu başarısız: {result}. Deneme {state.failed_entity_query_count}/{state.max_entity_query_attempts}. Analiz: {analysis} Farklı bir cypher_query ile tekrar dene."

                            # 🧠 MEM0: Başarısız stratejiyi takip et - DEVRE DIŞI
                            # strategy_type = self._classify_cypher_strategy(
                            #     action_content
                            # )
                            # self.track_strategy_attempt(
                            #     strategy=f"cypher_query_{strategy_type}",
                            #     success=False,
                            #     details=f"Başarısız sorgu: {action_content[:100]}...",
                            #     reason=str(result)[:200],
                            # )

                            # Conversation history'ye ekle - Başarısız cypher query (retry)
                            conversation_history.append(
                                f"İterasyon {state.iteration_count}:\nObservation: {observation}\nThought: {thought}\nAction: {action}\nContent: {action_content[:100]}...\nSonuç: {current_observation}"
                            )
                        else:
                            # Son başarısız denemeden sonra da state'e ekle
                            # self.add_failed_query(
                            #     state,
                            #     state.iteration_count,
                            #     action_content,
                            #     str(result),
                            #     "cypher",
                            # )

                            logger.info(
                                f"❌ Entity query {state.max_entity_query_attempts} kez başarısız. Vector search'e geç."
                            )
                            current_observation = f"Entity sorguları {state.max_entity_query_attempts} kez başarısız oldu. "

                            # Conversation history'ye ekle - Başarısız cypher query (final)
                            conversation_history.append(
                                f"İterasyon {state.iteration_count}:\nObservation: {observation}\nThought: {thought}\nAction: {action}\nContent: {action_content[:100]}...\nSonuç: {current_observation}"
                            )

                else:
                    current_observation = f"Bilinmeyen action: {action}. Geçerli action'lar: cypher_query, final_answer"

                    # Conversation history'ye ekle - Bilinmeyen action
                    conversation_history.append(
                        f"İterasyon {state.iteration_count}:\nObservation: {observation}\nThought: {thought}\nAction: {action}\nContent: {action_content[:100] if action_content else 'N/A'}...\nSonuç: {current_observation}"
                    )

                # Chunk limit kontrolü
                if len(state.discovered_chunks) >= state.max_chunks_limit:
                    current_observation += f" (Chunk limiti {state.max_chunks_limit} aşıldı, artık yeni chunk aranmayacak)"

            except Exception as e:
                logger.error(f"İterasyon {state.iteration_count} hatası: {e}")
                current_observation = f"Hata oluştu: {e}. Farklı bir yaklaşım dene."

                # Conversation history'ye ekle - Exception
                conversation_history.append(
                    f"İterasyon {state.iteration_count}:\nHATA: {str(e)[:100]}...\nSonuç: {current_observation}"
                )

            # Chunk limit kontrolü (sadece uyarı ver, LLM karar versin)
            if len(state.discovered_chunks) >= state.max_chunks_limit:
                current_observation += f" (Chunk limiti {state.max_chunks_limit} aşıldı, istersen final_answer verebilirsin)"

            # Son iterasyon kontrolü - final answer vermeye zorla
            if state.iteration_count >= self.max_iterations - 1:
                current_observation += f" ⚠️ SON İTERASYON! Artık MUTLAKA final_answer ver - eldeki verilerle cevapla!"

        # Token kullanımını logla ve detaylı rapor oluştur
        logger.info(
            f"TOPLAM TOKEN KULLANIMI - Input: {self.token_usage['input_tokens']}, Output: {self.token_usage['output_tokens']}, Total: {self.token_usage['total_tokens']}"
        )

        # Detaylı token raporu
        self.log_detailed_token_report()

        # Final answer varsa döndür, yoksa chunk ve entity verilerini döndür
        if final_answer:
            logger.info(f"Agent final answer verdi 1: {final_answer}")

            # 🧠 MEM0: Önceki memory aramayı tamamla (eğer varsa) - DEVRE DIŞI
            # try:
            #     if memory_context_future:
            #         # Background thread'den memory sonucunu al (timeout ile)
            #         memory_context = memory_context_future.result(
            #             timeout=2.0
            #         )  # 2 saniye timeout
            #         if memory_context and memory_context not in self.context_memory:
            #             logger.info(
            #                 "🧠 Mem0'dan önceki deneyimler geç bulundu ama kullanılamadı (zaten işlem bitti)"
            #             )
            # except concurrent.futures.TimeoutError:
            #     logger.warning("🧠 Mem0 arama timeout oldu")
            # except Exception as e:
            #     logger.warning(f"🧠 Mem0 arama hatası: {e}")

            # 🧠 MEM0: Başarılı stratejiyi kaydet (background thread'de) - DEVRE DIŞI
            # has_success = any(s.get("success", False) for s in self.query_strategies)
            # try:
            #     # Shared executor ile background'da strategy kaydet
            #     store_future = self._memory_executor.submit(
            #         self.store_query_strategy,
            #         user_question,
            #         self.query_strategies,
            #         has_success,
            #     )
            #     logger.info("🧠 Mem0'a strateji kaydı background'da başlatıldı")

            # except Exception as e:
            #     logger.warning(f"🧠 Mem0 background kaydetme hatası: {e}")
            #     # Fallback: non-blocking sync store
            #     try:
            #         self.store_query_strategy(
            #             user_question, self.query_strategies, has_success
            #         )
            #     except:
            #         pass  # Mem0 kaydı başarısız olsa da ana işlem devam etsin

            return {
                "final_answer": final_answer,
                "iterations": state.iteration_count,
                "discovered_chunks": len(state.discovered_chunks),
                "discovered_entities": len(state.discovered_entities),
                "token_usage": self.token_usage.copy(),
                "detailed_token_usage": self.detailed_token_usage.copy(),
                "chunk_details": [
                    {
                        "document": chunk.document_name,
                        "page": chunk.page_number,
                        "relevance": chunk.relevance_score,
                        "preview": (
                            chunk.text[:200] + "..."
                            if len(chunk.text) > 200
                            else chunk.text
                        ),
                    }
                    for chunk in state.discovered_chunks[:10]  # En iyi 10 chunk
                ],
            }
        # Daha detaylı analiz logu ekle
        logger.info(
            f"Final answer yok: Detaylı analiz verileri: {conversation_history}, {state.discovered_chunks}"
        )
        # Final answer yoksa detaylı analiz verileri döndür
        return {
            "iterations": state.iteration_count,
            "conversation_history": conversation_history,
            "discovered_chunks": len(state.discovered_chunks),
            "discovered_entities": len(state.discovered_entities),
            "entity_details": [
                {
                    "id": e.get("id", ""),
                    "type": e.get("type", ""),
                    "labels": e.get("labels", []),
                }
                for e in state.discovered_entities[:10]  # En iyi 10 entity
            ],
            "chunk_details": [
                {
                    "document": c.document_name,
                    "page": c.page_number,
                    "relevance": c.relevance_score,
                    "preview": c.text[:100] + "...",
                }
                for c in sorted(
                    state.discovered_chunks,
                    key=lambda x: x.relevance_score,
                    reverse=True,
                )[:5]
            ],
            "schema_info": self.schema_cache,
            "token_usage": self.token_usage.copy(),
            "detailed_token_usage": self.detailed_token_usage.copy(),
            "successful_findings": [
                {
                    "iteration": f.iteration,
                    "action": f.action_type,
                    "finding": f.summary,
                    "relevance_score": f.relevance_score,
                }
                for f in state.successful_findings
            ],
            "context_memory": self.context_memory,
        }

    def get_system_prompt(self) -> str:
        """System prompt'u cache'den al - artık başlangıçta oluşturuluyor"""
        
        if self.system_prompt_cache:
            logger.info("📋 System prompt cache'den alınıyor (agent oluşturulurken hazırlandı)")
            logger.info(f"📋 Cache'deki system prompt uzunluğu: {len(self.system_prompt_cache)} karakter")
            return self.system_prompt_cache
        
        # Fallback: Eğer cache yoksa (normalde olmamalı), oluştur
        logger.warning("⚠️ System prompt cache boş, fallback ile oluşturuluyor...")
        self.system_prompt_cache = self.create_enhanced_system_prompt({})
        logger.info(f"✅ System prompt fallback ile oluşturuldu: {len(self.system_prompt_cache)} karakter")
        return self.system_prompt_cache

    def create_enhanced_system_prompt(self, schema: Dict[str, Any] = None) -> str:
        """Schema-based ReAct Agent - General Purpose Graph Database Query Assistant"""

        # Schema'yı cache'den al
        try:
            from src.schema_extractor import Neo4jSchemaExtractor

            extractor = Neo4jSchemaExtractor()
            extractor.graph = self.graph
            compact_schema = self.get_cached_schema()

            schema_text = f"""NEO4J GRAPH DATABASE SCHEMA:
{compact_schema}

🎯 DOMAIN CONTEXT:
Bu graph database, genel amaçlı bir veri sistemidir. Schema dinamik olarak sistem tarafından sağlanır:
- **Nodes**: Schema'da tanımlı tüm varlık türleri (node labels)
- **Properties**: Her node türünün sahip olduğu özellikler (property keys)
- **Relationships**: Varlıklar arasındaki bağlantılar (relationship types)

**KRİTİK**: Sorguları yazarken MUTLAKA schema'da tanımlı node türlerini, property'leri ve relationship'leri kullan!"""

        except Exception as e:
            logger.warning(f"Schema çekme hatası: {e}")
            schema_text = """NEO4J GRAPH DATABASE SCHEMA:
⚠️ Schema bilgisi alınamadı - Graph database bağlantısını kontrol edin"""

        system_prompt = f"""# GRAPH DATABASE QUERY AGENT

Sen verilen Neo4j graph database şemasını kullanan bir ReAct (Reasoning + Acting) ajansın. 
Kullanıcı sorularını analiz ederek en uygun graph database sorgularını oluşturur ve sonuçları yorumlarsın.

{schema_text}

## 🔧 TEMEL KURALLAR:

### � SCHEMA KULLANIM KURALLARI (KRİTİK):
1. **SCHEMA FIRST**: Her sorgu öncesi yukarıdaki schema bölümünü incele
2. **NODE TYPES**: Sadece schema'da listelenen node label'larını kullan
3. **PROPERTIES**: Sadece schema'da tanımlı property key'lerini kullan
4. **RELATIONSHIPS**: Sadece schema'da gösterilen relationship type'larını kullan
5. **NO ASSUMPTIONS**: Schema'da yoksa kullanma - hardcoded domain bilgisi yasak!


### 🔍 AKILLI ARAMA STRATEJİSİ:
**CONTENT SORULARI için direkt semantic search kullan** (taksit, tutar, detay, açıklama, tablo)
**METADATA SORULARI için entity araması** 


### �📝 CYPHER QUERY KURALLARI:
1. **STRING NORMALİZASYONU ZORUNLU**: Tüm string karşılaştırmalarında MUTLAKA:
   - **Güvenli toString kullanımı**: `toLower(apoc.text.clean(field)) CONTAINS toLower(apoc.text.clean('value'))`
   - **ÖRN**: `toLower(apoc.text.clean(p.type)) CONTAINS toLower(apoc.text.clean('str'))`
   - Asla doğrudan `p.type = 'str'` kullanma!

2. **Field Type Matching**: Schema'dan field tipini kontrol et
   - **String fields**: `toLower(apoc.text.clean(field))) CONTAINS toLower(apoc.text.clean('value'))`
   - **Integer fields**: `field = value` 
   - **Boolean fields**: `field = true/false`

3. **Node/Relationship Kullanımı**: Sadece schema'da tanımlı node'ları ve relationship'leri kullan

4. **Embedding Field Hariç Tutma**: Schema'da `embedding_vector` tipindeki field'larda CONTAINS araması yapma - bunlar vector search için kullanılır

5. **Return Clause**: Sorguya uygun alanları döndür

### 🔍 ARAMA STRATEJİSİ:

**KURAL**: ÖNCE KEŞİF YAP - HER ZAMAN KEŞİF İLE BAŞLA!

#### 🎯 KEŞİF SORGUSU YAKLAŞIMI (Schema-Driven):

**ZORUNLU**: Keşif sorgularında schema'dan öğrenilen node türlerini ve property'lerini dinamik olarak kullan!

**1. SCHEMA-BASED NODE KEŞFİ:**
```cypher
// ADIM 1: Schema'daki tüm node türlerinde ilgili property'lerde ara
MATCH (n:SchemaNodeType)  // <-- SchemaNodeType'ı gerçek node türü ile değiştir
WHERE toLower(apoc.text.clean(n.schema_property)) CONTAINS toLower(apoc.text.clean('kullanici_terimi'))
RETURN n.schema_property, labels(n) as node_type, properties(n)
LIMIT 5

```

**2. SCHEMA-BASED PROPERTY KEŞFİ:**
```cypher
// ADIM 2: Schema'da bulunan property'leri dinamik olarak kontrol et
// Embedding fieldlarını hariç tutarak arama yap
MATCH (n)
WHERE any(prop IN keys(n) WHERE 
    prop <> 'embedding' AND NOT prop CONTAINS 'vector' AND
    toLower(apoc.text.clean(coalesce(toString(n[prop]), ''))) CONTAINS toLower(apoc.text.clean('kullanici_terimi')))
RETURN labels(n) as node_type, keys(n) as properties, 
       [prop IN keys(n) WHERE 
         prop <> 'embedding' AND NOT prop CONTAINS 'vector' AND
         toLower(apoc.text.clean(coalesce(toString(n[prop]), ''))) CONTAINS toLower(apoc.text.clean('kullanici_terimi')) 
       | {{property: prop, value: n[prop]}}] as matches
LIMIT 5
```

**3. SCHEMA-BASED RELATİONSHİP KEŞFİ:**
```cypher
// ADIM 3: Schema'da tanımlı relationship'leri kullanarak bağlantılı ara
// Schema'dan öğrenilen relationship türlerini kullan
MATCH (n)-[r:SchemaRelationType]->(m)  // <-- SchemaRelationType'ı gerçek relationship türü ile değiştir
WHERE toLower(apoc.text.clean(coalesce(toString(n.schema_property), ''))) CONTAINS toLower(apoc.text.clean('kullanici_terimi'))
   OR toLower(apoc.text.clean(coalesce(toString(m.schema_property), ''))) CONTAINS toLower(apoc.text.clean('kullanici_terimi'))
RETURN labels(n) as source_type, type(r) as relation_type, labels(m) as target_type,
       n.schema_property as source_value, m.schema_property as target_value
LIMIT 5
```

#### 📋 KEŞİF SONRASI ANALİZ (Schema-Based):

**ÇOKLU SONUÇ DURUMU**: Birden fazla eşleşme varsa, schema'daki node türlerini analiz et
   ```
   "kullanici_terimi" ile eşleşen yapılar:
   - SchemaNodeType1'de: X sonuç
   - SchemaNodeType2'de: Y sonuç
   - SchemaNodeType3'te: Z sonuç
   Hangi node türü ile devam etmek istiyorsun?
   ```

**TEK SONUÇ DURUMU**: Tek eşleşme varsa, o node türü ile devam et
   ```
   ✅ Tek "SchemaNodeType" node'u bulundu. Bu varlık ile devam ediyorum.
   ```

**BOŞ SONUÇ DURUMU**: Hiç eşleşme yoksa, schema'daki alternatif property'leri dene
   ```
   ⚠️ İlk property'de eşleşme bulunamadı. Schema'daki alternatif property'lerde arıyorum:
   - Farklı property isimlerinde ara
   - Daha geniş node türlerinde ara
   - Relationship üzerinden bağlantılı ara
   ```

#### 🔄 KEŞİF İTERASYON YAKLAŞIMI (Schema-Driven):

**GENİŞ KEŞİF**: Schema'daki tüm node türlerinde ara (MATCH (n) WHERE schema_property...)
**DAR KEŞİF**: Schema'dan spesifik node türünde ara (MATCH (n:SchemaNodeType) WHERE ...)
**DERİN KEŞİF**: Schema'daki relationship'ler üzerinden ara (MATCH (n)-[r:SchemaRelType]->(m) WHERE ...)
**YENİDEN ŞEKİLLENDİRME**: Başarısızsa schema'daki farklı property'lerde ara

**UYGULAMA**:
- İlk sorgu her zaman schema'daki tüm node türlerini keşfet
- Schema'dan öğrenilen property isimlerini dinamik olarak kullan
- Schema'da tanımlı relationship türlerini keşfet
- Hardcoded domain terimleri kullanma - her şeyi schema'dan al!

### 🎯 ARAMA STRATEJİSİ:

**METADATA ARAMALARI**: Entity'ler ve yapılandırılmış veriler için
- Node properties üzerinden filtreleme
- Eğer birden fazla kelimeden oluşan bir node arama başarısız olursa ayrı ayrı arama yap

**CONTENT ARAMALARI**: Belge içeriği ve semantic arama için  
- Chunk nodes üzerinden text içeriği arama
- Embedding-based similarity search

**HİBRİT ARAMALARI**: Hem metadata hem content gereken durumlarda
- Önce entity filtresi, sonra content arama
- Filename discovery → content search chain


**AKILLI CHUNK ARAMA**: Eğer gelen chunk'lar eksik bilgi içeriyorsa (kesik cümleler, tablo devamı), 
sonraki chunk'ları da getir: `WHERE node.position > X AND node.position < X+5`
- Cypher sonucunu DEĞERLENDİR: Bu yeterli mi, yoksa daha fazla chunk lazım mı?

### 🔍 VECTOR ARAMA STRATEJİSİ (Schema-Driven, Domain Agnostic):

**A) METADATA + VECTOR ARAMA (Schema-Driven):**
```cypher
WITH $embedding_vector AS queryVec
MATCH (content_node)-[rel]->(container_node)
WHERE toLower(apoc.text.clean(coalesce(toString(container_node.schema_property), ''))) CONTAINS toLower(apoc.text.clean("filter_term"))
  AND content_node.embedding IS NOT NULL
WITH content_node, container_node, gds.similarity.cosine(content_node.embedding, queryVec) AS score
WHERE score >= 0.5
RETURN content_node.text, labels(content_node), labels(container_node), score
ORDER BY score DESC LIMIT 10
```

**B) SADECE VECTOR ARAMA (Schema-Driven):**
```cypher
WITH $embedding_vector AS queryVec
MATCH (content_node)-[rel]->(container_node)
WHERE content_node.embedding IS NOT NULL
WITH content_node, container_node, gds.similarity.cosine(content_node.embedding, queryVec) AS score
WHERE score >= 0.5
RETURN content_node.text, labels(content_node), labels(container_node), score
ORDER BY score DESC LIMIT 15
```

**C) FİLTRELİ VECTOR ARAMA (Schema-Driven + Context):**
```cypher
WITH $embedding_vector AS queryVec
MATCH (content_node)-[rel]->(container_node)
WHERE container_node.schema_property IN $context_list
  AND content_node.embedding IS NOT NULL
WITH content_node, container_node, gds.similarity.cosine(content_node.embedding, queryVec) AS score
WHERE score >= 0.5
RETURN content_node.text, labels(content_node), labels(container_node), score
ORDER BY score DESC LIMIT 10
```

**NOT**: Yukarıdaki örneklerde:
- `SchemaContentType`, `SchemaContainerType`: Schema'dan öğrenilen gerçek node türleri
- `SchemaRelationType`: Schema'dan öğrenilen gerçek relationship türü
- `schema_*_property`: Schema'dan öğrenilen gerçek property isimleri
- LLM bu placeholder'ları schema bilgisi ile değiştirmeli!



### AVAILABLE TOOLS (OpenAI Function Calling):

**generate_embeddings_for_cypher(text)**: 
- Cypher sorgularında kullanmak üzere text'ten embedding oluşturur
- text: Metadata temizlenmiş anahtar kelimeler/kavramlar (örn: "taksit tutarı", "prim bilgileri")
- LLM embedding'leri görmez, sadece Cypher'da $embedding_vector değişkeni olarak kullanır
- KULLANIM: Tool çağır → Cypher'da "gds.similarity.cosine(c.embedding, $embedding_vector)" ile semantic similarity kullan

**🎯 ANAHTAR KELİME SEÇİM STRATEJİSİ:**
- **KRİTİK KURAL**: Müşteri adı, yıl, poliçe türü gibi metadata'yı embedding'e ekleme!
- **SADECE İÇERİK TERİMLERİ**: Belgede aranacak kavram/içerik kelimelerini kullan
- **ÖRNEK YANLIŞ**: "ayça hanım 2020 d4 konut poliçesi taksit tablosu" ❌
- **ÖRNEK DOĞRU**: "taksit tablosu ödeme planı" ✅
- ❌ TEK KELİME YETERLI DEĞİL: "taksit" → çok genel, yanlış chunk'lar bulabilir
- ✅ BAĞLAMLI TERIMLER KULLAN: "taksit tutarları", "ödeme planı", "taksit tablosu"
- ✅ SAYISAL VERİ: "prim tutarı", "hasar bedeli", "teminat limiti", "ödeme miktarı"
- ✅ TABLO/LİSTE: "ödeme vadesi", "taksit vadesi", "ödeme planı tablosu"
- ✅ KONTEKST EKLEYİN: Kullanıcı "taksitleri" diyorsa → "taksit tutarları ödeme planı"
- **METADATA FİLTRELEME**: Cypher'da WHERE ile müşteri/yıl/tip filtresi uygula, embedding'de kullanma!

**add_page_resource(page_link)**:
- Kullanılan içerik node'larının sayfa referanslarını kaynak olarak ekler
- Her kullanılan içerik için mutlaka çağır
- `page_link` parametresi: Cypher sonucundan gelen page_link değeri

#### 🛠️ TOOL KULLANIM KURALLARI:

**TOOL CALLING**: Tool'ları çağırmak için OpenAI Function Calling kullan:
- **generate_embeddings_for_cypher**: Semantic/Vector arama için embedding oluştur  
- **add_page_resource**: Chunk'lardan sayfa referanslarını kaydet

**ZORUNLU TOOL ÇAĞIRMA DURUMLARI:**

1. **Vector/Semantic/Chunk Search Gerektiğinde → generate_embeddings_for_cypher ÇAĞIR:**
   - Kullanıcı semantik sorular soruyorsa (benzerlik, içerik arama)

2. **Cypher Sonuçlarından Sayfa Referansı Alınca → add_page_resource ÇAĞIR:**
   - Cypher sonucunda `page_link`, `page_number` vb. sayfa bilgisi gelince
   - Final answer'da sayfa referansları gösterilecekse
   - Chunk'lar bulunup kullanıcıya kaynak gösterilecekse

### 🔗 PARAMETER INHERITANCE:

**ZORUNLU**: Her yeni soruda önceki conversation'ı analiz et ve eksik parametreleri tamamla!

**STEP-BY-STEP ENFORCEMENT:**

1. **ÖNCEKI SORU ANALİZİ (ZORUNLU):**
   - Conversation history'den son soruyu parse et

2. **YENİ SORU ANALİZİ (ZORUNLU):**
   - Hangi parametreler explicit olarak belirtilmiş?
   - Hangi parametreler eksik/belirsiz?

3. **INHERITANCE KURALI (ZORUNLU):**
   - Entity eksikse → Önceki conversation'dan al
   - Filter eksikse → Önceki conversation'dan al  
   - Operation eksikse → Önceki conversation'dan al
   - Yeni constraint eklenmişse → Önceki parametrelerle birleştir

4. **VALIDATE BEFORE QUERY (ZORUNLU):**
   - "Bu query önceki soru ile uyumlu mu?"
   - "Tüm context parametreleri dahil edildi mi?"

## 📋 REACT FORMAT:

Her iterasyonda şu formatı kullan:

```
Observation: [Durum ve önceki sonuçlar]
Thought: [Kullanıcının sorusundaki TÜM terimleri thought kısmında da kullan. İçerik/detay arıyorum mu yoksa metadata mı? Schema'da hangi node/relation'lar relevant? ]
Action: [cypher_query | final_answer]
Content: [Cypher sorgusu | final cevap]
```

### 🎯 ACTION STRATEJİLERİ:

**cypher_query**: Schema'daki node/relationship'leri kullanarak veri araştırması
- Eğer semantic arama gerekiyorsa → önce tool'u çağır, sonra cypher_query yap
- Eğer entity araması gerekiyorsa → schema'daki node türlerini ve property'lerini kullanarak cypher_query yap
- Eğer metadata + content araması gerekiyorsa → önce entity
- **1. İTERASYON**: Entity'leri bul (Customer, Policy) - p.source_file'ı mutlaka RETURN et!
- **2. İTERASYON**: Keşfedilen filename'leri kullan - WHERE d.fileName IN [liste] formatında!
- **KRİTİK**: Filename CONTAINS araması yapma, direkt IN listesi kullan!
- **Chunk Metadata İçin**: node.chunkId, node.page_number, node.position, score'u da döndür  
- **ZORUNLU**: Cypher sonucunda chunk bulunca, faydalandığın her chunk için add_page_resource(page_link) çağır!


**final_answer**: Son cevabı ver
- **ÖNEMLİ**: Final answer'da sayfa referanslarını KENDİN ekleme! 
- Sistem otomatik olarak tool ile eklenen sayfaları ekleyecek
- Sadece sorunun cevabını yaz, referanslarla ilgilenmeyece

## 🎯 ITERATION BAŞLANGICI:

Her iterasyon başında şunları değerlendir:
1. **Kullanıcı Sorusu**: Ne tür bilgi aranıyor?
2. **Context Analizi**: Bu soru önceki konuşmayla ilgili mi? Belirsiz kelimeler (kaç, hangi, ne zaman) önceki varlıkları referans alıyor mu?
3. **Önceki Bulgular**: Hangi veriler elde edildi?
4. **Schema Mapping**: Soruya hangi node/relationship'ler cevap verebilir?
5. **Strateji Seçimi**: Metadata mı, content mi, yoksa hibrit arama mı?

## ⚠️ ÖNEMLİ NOTLAR (Schema-Driven, Domain Agnostic):

- **KRİTİK: SCHEMA FIRST!** Her sorgu öncesi schema'yı incele ve sadece orada tanımlı node/property/relationship kullan!
- **KRİTİK: SEMANTIC ARAMA İÇİN TOOL ÇAĞIR!** Vector/semantic arama gerektiğinde generate_embeddings_for_cypher TOOL'UNU çağır, sonra cypher_query eylemi yap!
- Schema'da olmayan node/property/relationship kullanma - sadece schema'dan öğrendiklerini kullan
- İçerik node'larından faydalanıyorsan mutlaka add_page_resource çağır (schema'daki reference property'yi kullan)
- Embedding'lerde metadata kullanma, sadece content terimleri
- Final answer'da kullanıcı dostu dil kullan, teknik terimlerden kaçın

Şimdi kullanıcının sorusunu analiz et ve schema'yı kullanarak en uygun yaklaşımı belirle."""

        return system_prompt


def test_agent():
    """Test fonksiyonu"""
    from langchain_neo4j import Neo4jGraph

    # Neo4j bağlantısı - server environment'tan al
    graph = Neo4jGraph(
        url=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        username=os.getenv("NEO4J_USERNAME", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD", "qwerty5555"),
        database=os.getenv("NEO4J_DATABASE", "neo4j"),
    )

    # Agent'ı oluştur - önce raw data mode, sonra LLM interpretation mode test et
    test_modes = [
        {"enable_llm_interpretation": False, "mode_name": "RAW DATA MODE"},
        # {"enable_llm_interpretation": True, "mode_name": "LLM INTERPRETATION MODE"}
    ]

    # Test soruları
    test_questions = [
        # "Kaç poliçe var ve kimin adına",
        # "Ayça Dinçkök'un poliçesini özetle",
        "Ayça Hanım’ın D5 poliçesinin primi ne kadar?",
        # "Kaç tane müşteri var?",
        # "Sistemde hangi poliçe türleri mevcut?",
        # "DASK poliçeleri hakkında ne tür bilgiler var?",
        # "Galata Residence ile ilgili hangi bilgiler mevcut?"
    ]

    for mode_config in test_modes:
        print(f"\n{'='*80}")
        print(f"🔬 TEST MODU: {mode_config['mode_name']}")
        print(f"LLM Interpretation: {mode_config['enable_llm_interpretation']}")
        print("=" * 80)

        # Agent'ı bu mode'da oluşturf
        agent = IntelligentAgent(
            graph, enable_llm_interpretation=mode_config["enable_llm_interpretation"]
        )

        for question in test_questions:
            print(f"\n{'-'*60}")
            print(f"SORU: {question}")
            print("-" * 60)

            result = agent.solve_question(question)

            # Final answer varsa onu göster
            if "final_answer" in result:
                print(f"🎯 FINAL ANSWER:\n{result['final_answer']}")
                print(f"\n📊 STATİSTİKLER:")
                print(f"- İTERASYON: {result['iterations']}")
                print(f"- CHUNK SAYISI: {result['discovered_chunks']}")
                print(f"- ENTITY SAYISI: {result['discovered_entities']}")
                print(f"- TOKEN KULLANIMI: {result['token_usage']['total_tokens']}")
            else:
                print(f"📊 STATİSTİKLER:")
                print(f"- İTERASYON: {result['iterations']}")
                print(f"- CHUNK SAYISI: {result['discovered_chunks']}")
                print(f"- ENTITY SAYISI: {result['discovered_entities']}")

                print("\nEN İLGİLİ CHUNK'LAR:")
                for chunk in result["chunk_details"]:
                    print(
                        f"- {chunk['document']} (Sayfa {chunk['page']}) - Relevance: {chunk['relevance']:.3f}"
                    )
                    print(f"  {chunk['preview']}")

        print(f"\n{mode_config['mode_name']} TEST TAMAMLANDI\n")


if __name__ == "__main__":
    test_agent()
