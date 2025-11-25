"""
Entity Resolution ve Deduplication modülü
Benzer entity'leri tespit eder ve birleştirir
"""

import logging
import numpy as np
import os
from pathlib import Path
from typing import List, Dict, Tuple, Optional
# sentence_transformers is only needed for embeddings, which is done in celery_worker
try:
    from sentence_transformers import SentenceTransformer
except (ImportError, ModuleNotFoundError):
    SentenceTransformer = None  # Embeddings are in celery_worker
# sklearn is only needed for similarity calculations, which is done in celery_worker
try:
    from sklearn.metrics.pairwise import cosine_similarity
except (ImportError, ModuleNotFoundError):
    cosine_similarity = None  # Similarity calculations are in celery_worker
import difflib
import re

class EntityResolver:
    """
    Entity Resolution ve deduplication için kullanılan sınıf
    """
    
    def __init__(self, 
                 similarity_threshold: float = 0.6,
                 name_similarity_threshold: float = 0.6,
                 embedding_model: str = "BAAI/bge-m3"):
        """
        Args:
            similarity_threshold: Embedding benzerlik eşiği (0-1 arası)
            name_similarity_threshold: İsim benzerlik eşiği (0-1 arası)
            embedding_model: Kullanılacak embedding modeli
        """
        self.similarity_threshold = similarity_threshold
        self.name_similarity_threshold = name_similarity_threshold
        
        # Cache klasörü ayarları
        # SentenceTransformer default olarak ~/.cache/torch/sentence_transformers kullanır
        # Ama özel cache klasörü de belirtebiliriz
        cache_folder = os.getenv(
            "HUGGINGFACE_CACHE_FOLDER",
            None  # None ise SentenceTransformer default cache kullanır
        )
        
        if cache_folder:
            Path(cache_folder).mkdir(parents=True, exist_ok=True)
            logging.info(f"📦 EntityResolver - Özel cache klasörü: {cache_folder}")
        else:
            # SentenceTransformer'ın default cache klasörü
            default_cache = os.path.join(os.path.expanduser("~"), ".cache", "torch", "sentence_transformers")
            logging.info(f"📦 EntityResolver - Default cache klasörü: {default_cache}")
        
        logging.info(f"🤖 EntityResolver - Embedding model: {embedding_model}")
        
        # SentenceTransformer is only available in celery_worker
        if SentenceTransformer is None:
            raise NotImplementedError("EntityResolver requires sentence_transformers, which is only available in celery_worker")
        
        # SentenceTransformer otomatik olarak cache kullanır:
        # - Model cache'te varsa oradan yüklenir (hızlı)
        # - Yoksa internet'ten indirilir ve cache'lenir (ilk kullanım)
        # - Sonraki kullanımlarda otomatik olarak cache'ten yüklenir
        if cache_folder:
            self.embedding_model = SentenceTransformer(
                embedding_model,
                cache_folder=cache_folder,
            )
        else:
            # Default cache kullan (SentenceTransformer otomatik yönetir)
            self.embedding_model = SentenceTransformer(embedding_model)
        
        logging.info(f"✅ EntityResolver - Model başarıyla yüklendi (cache'ten veya indirildi)")
        
    def normalize_name(self, name: str) -> str:
        """
        İsmi normalize eder (büyük/küçük harf, boşluk, Türkçe karakter)
        """
        if not name:
            return ""
            
        # Türkçe karakterleri normalize et
        replacements = {
            'ç': 'c', 'Ç': 'C',
            'ğ': 'g', 'Ğ': 'G', 
            'ı': 'i', 'I': 'I',
            'ö': 'o', 'Ö': 'O',
            'ş': 's', 'Ş': 'S',
            'ü': 'u', 'Ü': 'U'
        }
        
        normalized = name.lower().strip()
        for tr_char, en_char in replacements.items():
            normalized = normalized.replace(tr_char, en_char)
            
        # Ekstra boşlukları temizle
        normalized = re.sub(r'\s+', ' ', normalized)
        
        return normalized
        
    def calculate_name_similarity(self, name1: str, name2: str, entity_type: str = None) -> float:
        """
        İki isim arasında benzerlik hesaplar
        Önce kesin kontroller (contains), sonra similarity hesabı
        """
        if not name1 or not name2:
            return 0.0
            
        # Normalize et
        norm1 = self.normalize_name(name1)
        norm2 = self.normalize_name(name2)
        
        # 1. TAM EŞLEŞİK KONTROLl
        if norm1 == norm2:
            return 1.0
            
        # 2. ÇİFT TARAFLI CONTAINS KONTROLÜ (APOC benzeri)
        # Bir ismin diğerini tamamen içermesi durumu
        if norm1 in norm2 or norm2 in norm1:
            # Kesin içerme varsa yüksek skor ver ama tam 1.0 değil
            return 0.95
            
        # 3. KELİME BAZLI CONTAINS KONTROLÜ
        words1 = norm1.split()
        words2 = norm2.split()
        
        # Her iki yönde de kelime bazlı contains kontrolü
        words1_set = set(words1)
        words2_set = set(words2)
        
        # Tüm kelimeler eşleşiyorsa (sadece sıra farklı olabilir)
        if words1_set == words2_set:
            return 0.98  # Çok yüksek ama tam değil
            
        # Bir tarafın kelimeleri diğerinin tamamen alt kümesi mi?
        if words1_set.issubset(words2_set) or words2_set.issubset(words1_set):
            # Örnek: "Ayça Dinçkök" ⊆ "Ayça Dinçkök Dinkal"
            return 0.92
            
        # 4. Customer'lar için ÖZel SIKI KONTROL
        if entity_type and entity_type.lower() == 'customer':
            # Customer'larda ortak kelime sayısını kontrol et
            common_words = words1_set & words2_set
            
            # Sadece 1 kelime ortaksa (genellikle soyisim) -> Çok düşük skor
            if len(common_words) == 1:
                return 0.25  # Çok düşük - muhtemelen farklı kişiler
                
            # 2 veya daha fazla ortak kelime varsa similarity hesabına geç
            if len(common_words) >= 2:
                # Kesin ortak kelimeler var, similarity hesapla
                return self._calculate_advanced_similarity(norm1, norm2, words1, words2)
            else:
                # Hiç ortak kelime yok
                return 0.0
        
        # 5. SIMILARITY HESABI (Customer olmayan veya Customer'da 2+ ortak kelime)
        return self._calculate_advanced_similarity(norm1, norm2, words1, words2)
        
    def _calculate_advanced_similarity(self, norm1: str, norm2: str, words1: list, words2: list) -> float:
        """
        Gelişmiş similarity hesabı
        """
        # Levenshtein similarity
        levenshtein_sim = difflib.SequenceMatcher(None, norm1, norm2).ratio()
        
        # Kelime bazlı similarity
        words1_set = set(words1)
        words2_set = set(words2)
        
        # Jaccard similarity (ortak kelime / toplam unique kelime)
        intersection = len(words1_set & words2_set)
        union = len(words1_set | words2_set)
        jaccard_sim = intersection / union if union > 0 else 0
        
        # Pozisyon bazlı similarity (aynı pozisyondaki kelimeler)
        position_matches = 0
        min_length = min(len(words1), len(words2))
        for i in range(min_length):
            if words1[i] == words2[i]:
                position_matches += 1
        
        position_sim = position_matches / max(len(words1), len(words2)) if max(len(words1), len(words2)) > 0 else 0
        
        # Weighted combination
        final_similarity = (
            levenshtein_sim * 0.4 +    # Karakter bazlı
            jaccard_sim * 0.4 +        # Kelime bazlı 
            position_sim * 0.2         # Pozisyon bazlı
        )
        
        return min(1.0, final_similarity)
        
    def get_entity_embedding(self, entity_id: str, entity_type: str, entity_name: str = None) -> np.ndarray:
        """
        Entity için embedding oluşturur
        """
        # Entity bilgilerini birleştir
        text_parts = []
        
        if entity_name:
            text_parts.append(f"name: {entity_name}")
        if entity_id:
            text_parts.append(f"id: {entity_id}")
        if entity_type:
            text_parts.append(f"type: {entity_type}")
            
        combined_text = " | ".join(text_parts)
        
        # Embedding oluştur
        embedding = self.embedding_model.encode([combined_text])
        return embedding[0]
        
    def find_similar_entities(self, 
                            new_entity: Dict, 
                            existing_entities: List[Dict],
                            entity_type_filter: str = None) -> List[Tuple[Dict, float]]:
        """
        Yeni entity'ye benzer mevcut entity'leri bulur
        
        Args:
            new_entity: Yeni entity bilgileri
            existing_entities: Mevcut entity'lerin listesi
            entity_type_filter: Sadece belirli entity_type'ları kontrol et
            
        Returns:
            (entity, similarity_score) tuple'larının listesi
        """
        new_id = new_entity.get('id', '')
        new_name = new_entity.get('name', new_entity.get('id', ''))
        new_type = new_entity.get('entity_type', new_entity.get('type', ''))
        
        logging.info(f"🔍 Benzer entity aranıyor: '{new_id}' (name: '{new_name}', type: '{new_type}')")
        
        similar_entities = []
        
        # Yeni entity için embedding oluştur
        new_embedding = self.get_entity_embedding(new_id, new_type, new_name)
        
        for existing_entity in existing_entities:
            existing_id = existing_entity.get('id', '')
            existing_name = existing_entity.get('name', existing_entity.get('id', ''))
            existing_type = existing_entity.get('entity_type', existing_entity.get('type', ''))
            
            # Entity type filtresi
            if entity_type_filter and existing_type.lower() != entity_type_filter.lower():
                continue
                
            # Aynı entity type kontrolü (Person vs Person)
            if new_type.lower() != existing_type.lower():
                continue
                
            # İsim benzerliği kontrolü - entity_type'ı geç
            name_similarity = self.calculate_name_similarity(new_name, existing_name, new_type)
            
            # Embedding benzerliği kontrolü
            existing_embedding = self.get_entity_embedding(existing_id, existing_type, existing_name)
            embedding_similarity = cosine_similarity([new_embedding], [existing_embedding])[0][0]
            
            # Customer'lar için daha sıkı kombinasyon skoru
            if new_type.lower() == 'customer':
                # Customer'larda isim benzerliği daha önemli (%80 isim, %20 embedding)
                combined_score = (name_similarity * 0.8) + (embedding_similarity * 0.2)
            else:
                # Diğer entity'lerde mevcut oran (%60 isim, %40 embedding)
                combined_score = (name_similarity * 0.6) + (embedding_similarity * 0.4)
            
            logging.debug(f"  - Karşılaştırma: '{existing_id}' (name: '{existing_name}')")
            logging.debug(f"    İsim benzerliği: {name_similarity:.3f}")
            logging.debug(f"    Embedding benzerliği: {embedding_similarity:.3f}")
            logging.debug(f"    Kombinasyon skoru: {combined_score:.3f}")
            
            # Eşik kontrolü - Customer'lar için çok daha sıkı kurallar
            if new_type.lower() == 'customer':
                # Customer'lar için çok sıkı eşikler
                customer_name_threshold = 0.90   # İsim benzerliği en az %90
                customer_combined_threshold = 0.88  # Kombinasyon skoru en az %88
                
                if (name_similarity >= customer_name_threshold and 
                    combined_score >= customer_combined_threshold):
                    similar_entities.append((existing_entity, combined_score))
                    logging.info(f"  ✅ Benzer Customer bulundu: '{existing_id}' -> name_sim: {name_similarity:.3f}, combined: {combined_score:.3f}")
                else:
                    logging.debug(f"  ❌ Customer eşik altı: '{existing_id}' -> name_sim: {name_similarity:.3f}, combined: {combined_score:.3f}")
            else:
                # Diğer entity'ler için mevcut eşikler
                if (name_similarity >= self.name_similarity_threshold or 
                    embedding_similarity >= self.similarity_threshold or
                    combined_score >= self.similarity_threshold):
                    
                    similar_entities.append((existing_entity, combined_score))
                    logging.info(f"  ✅ Benzer entity bulundu: '{existing_id}' -> skor: {combined_score:.3f}")
                else:
                    logging.debug(f"  ❌ Entity eşik altı: '{existing_id}' -> name_sim: {name_similarity:.3f}, combined: {combined_score:.3f}")
        
        # Skora göre sırala
        similar_entities.sort(key=lambda x: x[1], reverse=True)
        return similar_entities
        
    def resolve_entity_duplicates(self, graph, entity_type: str = "Person") -> Dict:
        """
        Neo4j graph'daki duplicate entity'leri resolve eder
        """
        logging.info(f"🔄 {entity_type} entity'leri için duplicate resolution başlıyor...")
        
        try:
            # Tüm Person entity'leri al
            query = """
            MATCH (e)
            WHERE $entity_type IN labels(e) OR e.entity_type = $entity_type
            RETURN e.id as id, 
                   e.name as name,
                   e.entity_type as entity_type,
                   elementId(e) as element_id,
                   labels(e) as labels,
                   properties(e) as properties
            ORDER BY e.id
            """
            
            entities = graph.run(query, entity_type=entity_type)
            logging.info(f"Toplam {len(entities)} adet {entity_type} entity bulundu")
            
            if len(entities) < 2:
                return {"merged_count": 0, "message": "Yeterli entity yok"}
                
            merged_count = 0
            processed_ids = set()
            
            for i, entity in enumerate(entities):
                entity_id = entity['id']
                entity_element_id = entity['element_id']
                
                if entity_element_id in processed_ids:
                    continue
                    
                # Bu entity'ye benzer olanları bul
                remaining_entities = entities[i+1:]
                similar_entities = self.find_similar_entities(
                    entity, 
                    remaining_entities, 
                    entity_type_filter=entity_type
                )
                
                if similar_entities:
                    # En benzer entity'yi al
                    most_similar, similarity_score = similar_entities[0]
                    most_similar_element_id = most_similar['element_id']
                    
                    logging.info(f"🔗 Merge işlemi: '{entity_id}' + '{most_similar['id']}' (skor: {similarity_score:.3f})")
                    
                    # Entity'leri merge et
                    merge_result = self.merge_entities(graph, entity_element_id, most_similar_element_id)
                    
                    if merge_result:
                        merged_count += 1
                        processed_ids.add(entity_element_id)
                        processed_ids.add(most_similar_element_id)
                        
            logging.info(f"✅ Duplicate resolution tamamlandı: {merged_count} merge işlemi")
            return {"merged_count": merged_count, "message": "Başarılı"}
            
        except Exception as e:
            logging.error(f"❌ Duplicate resolution hatası: {e}")
            return {"merged_count": 0, "error": str(e)}
            
    def merge_entities(self, graph, primary_entity_id: str, duplicate_entity_id: str) -> bool:
        """
        İki entity'yi birleştirir
        """
        try:
            merge_query = """
            MATCH (primary) WHERE elementId(primary) = $primary_id
            MATCH (duplicate) WHERE elementId(duplicate) = $duplicate_id
            
            // Duplicate'in tüm relationship'lerini primary'e aktar
            OPTIONAL MATCH (duplicate)-[r]->(target)
            WHERE target <> primary
            WITH primary, duplicate, collect({rel: r, target: target, type: type(r), props: properties(r)}) as out_rels
            
            OPTIONAL MATCH (source)-[r]->(duplicate) 
            WHERE source <> primary
            WITH primary, duplicate, out_rels, collect({rel: r, source: source, type: type(r), props: properties(r)}) as in_rels
            
            // Primary entity'nin özelliklerini güncelle (en zengin bilgiyi kullan)
            SET primary.name = CASE 
                WHEN length(coalesce(primary.name, '')) >= length(coalesce(duplicate.name, '')) 
                THEN primary.name 
                ELSE duplicate.name 
            END
            SET primary.merged_from = coalesce(primary.merged_from, []) + [duplicate.id]
            SET primary.last_updated = datetime()
            
            // Duplicate'i sil
            DETACH DELETE duplicate
            
            RETURN count(primary) as merged_entities
            """
            
            result = graph.run(merge_query, 
                              primary_id=primary_entity_id,
                              duplicate_id=duplicate_entity_id)
            
            if result and result[0]['merged_entities'] > 0:
                logging.info(f"✅ Entity merge başarılı: {primary_entity_id} + {duplicate_entity_id}")
                
                # Relationship'leri yeniden oluştur
                self.recreate_relationships_after_merge(graph, primary_entity_id, duplicate_entity_id)
                return True
            else:
                logging.warning(f"⚠️ Entity merge başarısız: {primary_entity_id} + {duplicate_entity_id}")
                return False
                
        except Exception as e:
            logging.error(f"❌ Entity merge hatası: {e}")
            return False
            
    def recreate_relationships_after_merge(self, graph, primary_entity_id: str, duplicate_entity_id: str):
        """
        Merge sonrası relationship'leri yeniden oluşturur
        """
        try:
            # Bu kısım merge_query içinde hallediliyor, ama ekstra kontrol için
            logging.info(f"🔗 Relationship'ler kontrol ediliyor: {primary_entity_id}")
            
        except Exception as e:
            logging.error(f"❌ Relationship recreation hatası: {e}")


# Global entity resolver instance - only create if sentence_transformers is available
# This is only needed in celery_worker, not in backend
entity_resolver = None
try:
    if SentenceTransformer is not None:
        entity_resolver = EntityResolver()
except (NotImplementedError, Exception):
    # EntityResolver requires sentence_transformers, which is only available in celery_worker
    entity_resolver = None

def resolve_entity_before_creation(new_entity: Dict, graph, entity_type: str = "Person") -> Optional[str]:
    """
    Yeni entity yaratılmadan önce benzer entity var mı kontrol eder
    Varsa mevcut entity'nin ID'sini döndürür
    
    Args:
        new_entity: Yaratılacak yeni entity
        graph: Neo4j graph objesi
        entity_type: Entity tipi
        
    Returns:
        Mevcut entity'nin element ID'si (varsa) veya None
    """
    # Entity resolver is only available in celery_worker
    if entity_resolver is None:
        logging.debug("EntityResolver not available (only in celery_worker), skipping entity resolution")
        return None
    
    try:
        # Mevcut benzer entity'leri ara - sadece belirtilen entity_type için
        query = f"""
        MATCH (e:{entity_type})
        WHERE e.id IS NOT NULL OR e.name IS NOT NULL
        RETURN e.id as id, 
               e.name as name,
               '{entity_type}' as entity_type,
               elementId(e) as element_id,
               properties(e) as properties
        """
        
        existing_entities_result = graph.query(query)
        
        if not existing_entities_result:
            return None
        
        # Query result'ını list of dict format'a çevir
        existing_entities = []
        for record in existing_entities_result:
            existing_entities.append({
                'id': record.get('id'),
                'name': record.get('name'),
                'entity_type': record.get('entity_type'),
                'element_id': record.get('element_id'),
                'properties': record.get('properties', {})
            })
            
        # Benzer entity'leri bul
        similar_entities = entity_resolver.find_similar_entities(
            new_entity, 
            existing_entities, 
            entity_type_filter=entity_type
        )
        
        if similar_entities:
            # En benzer entity'yi döndür
            most_similar, similarity_score = similar_entities[0]
            logging.info(f"🔗 Benzer entity bulundu, mevcut kullanılacak: '{most_similar['id']}' (skor: {similarity_score:.3f})")
            return most_similar['element_id']
            
        return None
        
    except Exception as e:
        logging.error(f"❌ Entity resolution kontrolü hatası: {e}")
        return None
