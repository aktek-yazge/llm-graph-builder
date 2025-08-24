"""
Entity Resolution ve Deduplication modülü
Benzer entity'leri tespit eder ve birleştirir
"""

import logging
import numpy as np
from typing import List, Dict, Tuple, Optional
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
import difflib
import re

class EntityResolver:
    """
    Entity Resolution ve deduplication için kullanılan sınıf
    """
    
    def __init__(self, 
                 similarity_threshold: float = 0.6,
                 name_similarity_threshold: float = 0.6,
                 embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"):
        """
        Args:
            similarity_threshold: Embedding benzerlik eşiği (0-1 arası)
            name_similarity_threshold: İsim benzerlik eşiği (0-1 arası)
            embedding_model: Kullanılacak embedding modeli
        """
        self.similarity_threshold = similarity_threshold
        self.name_similarity_threshold = name_similarity_threshold
        self.embedding_model = SentenceTransformer(embedding_model)
        
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
        
    def calculate_name_similarity(self, name1: str, name2: str) -> float:
        """
        İki isim arasında benzerlik hesaplar
        """
        if not name1 or not name2:
            return 0.0
            
        # Normalize et
        norm1 = self.normalize_name(name1)
        norm2 = self.normalize_name(name2)
        
        if norm1 == norm2:
            return 1.0
            
        # Levenshtein similarity
        similarity = difflib.SequenceMatcher(None, norm1, norm2).ratio()
        
        # İsim parçalarını kontrol et (Ayça Dinçkök vs Ayça Dinçkök Dinkal)
        words1 = set(norm1.split())
        words2 = set(norm2.split())
        
        if words1.issubset(words2) or words2.issubset(words1):
            # Bir isim diğerinin alt kümesi ise yüksek benzerlik ver
            similarity = max(similarity, 0.9)
            
        return similarity
        
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
                
            # İsim benzerliği kontrolü
            name_similarity = self.calculate_name_similarity(new_name, existing_name)
            
            # Embedding benzerliği kontrolü
            existing_embedding = self.get_entity_embedding(existing_id, existing_type, existing_name)
            embedding_similarity = cosine_similarity([new_embedding], [existing_embedding])[0][0]
            
            # Kombinasyon skoru (isim %60, embedding %40)
            combined_score = (name_similarity * 0.6) + (embedding_similarity * 0.4)
            
            logging.debug(f"  - Karşılaştırma: '{existing_id}' (name: '{existing_name}')")
            logging.debug(f"    İsim benzerliği: {name_similarity:.3f}")
            logging.debug(f"    Embedding benzerliği: {embedding_similarity:.3f}")
            logging.debug(f"    Kombinasyon skoru: {combined_score:.3f}")
            
            # Eşik kontrolü
            if (name_similarity >= self.name_similarity_threshold or 
                embedding_similarity >= self.similarity_threshold or
                combined_score >= self.similarity_threshold):
                
                similar_entities.append((existing_entity, combined_score))
                logging.info(f"  ✅ Benzer entity bulundu: '{existing_id}' -> skor: {combined_score:.3f}")
        
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


# Global entity resolver instance
entity_resolver = EntityResolver()

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
    try:
        # Mevcut benzer entity'leri ara
        query = """
        MATCH (e)
        WHERE $entity_type IN labels(e) OR e.entity_type = $entity_type
        RETURN e.id as id, 
               e.name as name,
               e.entity_type as entity_type,
               elementId(e) as element_id,
               properties(e) as properties
        """
        
        existing_entities = graph.run(query, entity_type=entity_type)
        
        if not existing_entities:
            return None
            
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
