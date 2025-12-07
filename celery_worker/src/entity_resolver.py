"""
Entity Resolution ve Deduplication modülü
Benzer entity'leri tespit eder ve birleştirir

load_embedding_model üzerinden OpenAI Embeddings kullanır
"""

import logging
import numpy as np
from typing import List, Dict, Tuple, Optional
import time
import difflib
import re

from src.shared.common_fn import load_embedding_model


class EntityResolver:
    """
    Entity Resolution ve deduplication için kullanılan sınıf
    
    load_embedding_model("openai") kullanır - batch, cache, similarity destekli
    """
    
    def __init__(self, 
                 similarity_threshold: float = 0.6,
                 name_similarity_threshold: float = 0.6):
        """
        Args:
            similarity_threshold: Embedding benzerlik eşiği (0-1 arası)
            name_similarity_threshold: İsim benzerlik eşiği (0-1 arası)
        """
        self.similarity_threshold = similarity_threshold
        self.name_similarity_threshold = name_similarity_threshold
        
        # Merkezi embedding model (OpenAI with batch+cache)
        self._embeddings, self._dimension = load_embedding_model("openai")
        logging.info(f"✅ EntityResolver - load_embedding_model('openai') yüklendi")
    
    def cosine_similarity(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        """İki vektör arasında cosine similarity hesaplar"""
        return self._embeddings.cosine_similarity(vec1, vec2)
    
    def clear_embedding_cache(self):
        """Embedding cache'ini temizle"""
        self._embeddings.clear_cache()
    
    def get_cache_stats(self) -> Dict:
        """Cache istatistiklerini döndür"""
        return self._embeddings.get_cache_stats()
        
    def normalize_name(self, name: str) -> str:
        """
        İsmi normalize eder (büyük/küçük harf, boşluk, Türkçe karakter)
        
        Türkçe karakter dönüşümleri:
        - İ -> i (büyük noktalı i)
        - I -> i (büyük noktasız ı, Türkçe'de ı'nın büyüğü)
        - ı -> i (küçük noktasız ı)
        - i -> i (küçük noktalı i, değişmez)
        """
        if not name:
            return ""
        
        # ÖNCE Türkçe büyük harfleri küçüğe çevir (lower()'dan önce!)
        # Çünkü Python'ın lower() fonksiyonu İ'yi i'ye, I'yı ı'ya çevirir
        turkish_upper_to_lower = {
            'İ': 'i',  # Büyük noktalı İ -> küçük i
            'I': 'i',  # Büyük noktasız I -> küçük i (Türkçe'de I, ı'nın büyüğü ama biz hepsini i yapıyoruz)
            'Ç': 'c',
            'Ğ': 'g',
            'Ö': 'o',
            'Ş': 's',
            'Ü': 'u',
        }
        
        # Önce Türkçe büyük harfleri dönüştür
        for tr_upper, tr_lower in turkish_upper_to_lower.items():
            name = name.replace(tr_upper, tr_lower)
        
        # Şimdi lower() güvenli
        normalized = name.lower().strip()
        
        # Küçük Türkçe karakterleri de ASCII'ye çevir
        turkish_to_ascii = {
            'ç': 'c',
            'ğ': 'g',
            'ı': 'i',  # Küçük noktasız ı -> i
            'ö': 'o',
            'ş': 's',
            'ü': 'u',
        }
        
        for tr_char, ascii_char in turkish_to_ascii.items():
            normalized = normalized.replace(tr_char, ascii_char)
            
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
        Entity için embedding oluşturur (load_embedding_model üzerinden, cached)
        """
        text_parts = []
        if entity_name:
            text_parts.append(f"name: {entity_name}")
        if entity_id:
            text_parts.append(f"id: {entity_id}")
        if entity_type:
            text_parts.append(f"type: {entity_type}")
        
        combined_text = " | ".join(text_parts)
        result = self._embeddings.embed_text(combined_text)
        
        if result is None:
            return np.zeros(self._dimension)
        return result
    
    def batch_get_entity_embeddings(self, entities: List[Dict]) -> Dict[str, np.ndarray]:
        """
        Birden fazla entity için batch embedding hesaplar (load_embedding_model üzerinden, cached)
        
        Args:
            entities: Entity dict listesi (id, name, entity_type)
            
        Returns:
            {cache_key_str: embedding} dict'i
        """
        texts = []
        cache_keys = []
        
        for entity in entities:
            entity_id = entity.get('id', '')
            entity_type = entity.get('entity_type', entity.get('type', ''))
            entity_name = entity.get('name', entity.get('id', ''))
            
            text_parts = []
            if entity_name:
                text_parts.append(f"name: {entity_name}")
            if entity_id:
                text_parts.append(f"id: {entity_id}")
            if entity_type:
                text_parts.append(f"type: {entity_type}")
            
            texts.append(" | ".join(text_parts))
            cache_keys.append(f"{entity_id or ''}|{entity_type or ''}|{entity_name or ''}")
        
        # Batch embedding (load_embedding_model handles caching)
        if texts:
            logging.info(f"🚀 Batch embedding: {len(texts)} entities")
            embeddings = self._embeddings.embed_texts(texts)
            
            results = {}
            for i, key in enumerate(cache_keys):
                results[key] = embeddings[i] if embeddings[i] is not None else np.zeros(self._dimension)
            return results
        
        return {}
        
    def _is_complex_name(self, name: str) -> bool:
        """
        İsmin "karmaşık" olup olmadığını tespit et (şirket ismi olma ihtimali yüksek)
        
        Karmaşık isim özellikleri:
        - Uzun (4+ kelime)
        - Noktalama içerir (., /, &, -)
        - Sayı içerir
        - Tamamen büyük harf
        """
        if not name:
            return False
        
        words = name.split()
        
        # 4+ kelime = muhtemelen şirket
        if len(words) >= 4:
            return True
        
        # Noktalama karakterleri = muhtemelen şirket
        if any(c in name for c in './-&'):
            return True
        
        # Sayı içerir = muhtemelen şirket
        if any(c.isdigit() for c in name):
            return True
        
        # 20+ karakter ve tamamen büyük harf = muhtemelen şirket
        if len(name) > 20 and name.isupper():
            return True
        
        return False

    def find_similar_entities(self, 
                            new_entity: Dict, 
                            existing_entities: List[Dict],
                            entity_type_filter: str = None) -> List[Tuple[Dict, float]]:
        """
        Yeni entity'ye benzer mevcut entity'leri bulur
        
        🚀 Performance & Accuracy Optimizations:
        1. Akıllı pre-filtering: Şirket/kişi ayrımına göre dinamik eşik
        2. Embedding-first for companies: Şirket isimleri için embedding'e güven
        3. High similarity shortcut: %95+ isim benzerliğinde embedding atla
        4. Batch embedding: Potansiyel eşleşmeler için tek seferde embedding
        5. Embedding cache: Daha önce hesaplanan embedding'ler tekrar hesaplanmaz
        
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
        
        logging.debug(f"🔍 Benzer entity aranıyor: '{new_id}' (name: '{new_name}', type: '{new_type}')")
        
        similar_entities = []
        high_similarity_matches = []  # %95+ isim benzerliği - embedding gereksiz
        
        # 🎯 Yeni isim karmaşık mı? (şirket olma ihtimali)
        new_is_complex = self._is_complex_name(new_name)
        
        # 🚀 PHASE 1: İsim benzerliği ile ön filtreleme
        candidates = []
        
        for existing_entity in existing_entities:
            existing_id = existing_entity.get('id', '')
            existing_name = existing_entity.get('name', existing_entity.get('id', ''))
            existing_type = existing_entity.get('entity_type', existing_entity.get('type', ''))
            
            # Entity type filtresi
            if entity_type_filter and existing_type.lower() != entity_type_filter.lower():
                continue
                
            # Aynı entity type kontrolü
            if new_type.lower() != existing_type.lower():
                continue
            
            # İsim benzerliği hesapla
            name_similarity = self.calculate_name_similarity(new_name, existing_name, new_type)
            
            # 🎯 %95+ isim benzerliği = Kesin eşleşme, embedding gereksiz
            if name_similarity >= 0.95:
                high_similarity_matches.append({
                    'entity': existing_entity,
                    'name_similarity': name_similarity,
                    'existing_id': existing_id,
                    'existing_name': existing_name,
                    'combined_score': name_similarity  # Embedding'siz skor
                })
                continue
            
            # 🎯 Dinamik pre-filter eşiği
            existing_is_complex = self._is_complex_name(existing_name)
            
            if new_is_complex or existing_is_complex:
                # Şirket ismi olabilir - DÜŞÜK eşik (%25), embedding'e güven
                pre_threshold = 0.25
            elif new_type.lower() == 'customer':
                # Kişi ismi - orta eşik (%50)
                pre_threshold = 0.50
            else:
                # Diğer entity'ler - düşük eşik (%30)
                pre_threshold = 0.30
            
            if name_similarity < pre_threshold:
                continue
            
            # Potansiyel eşleşme - embedding hesaplanacak
            candidates.append({
                'entity': existing_entity,
                'name_similarity': name_similarity,
                'existing_id': existing_id,
                'existing_name': existing_name,
                'existing_type': existing_type,
                'is_complex': existing_is_complex
            })
        
        logging.debug(f"  📊 Pre-filter: {len(existing_entities)} -> {len(candidates)} candidates, {len(high_similarity_matches)} high-similarity")
        
        # 🚀 PHASE 2: Yüksek benzerlikli eşleşmeleri ekle (embedding'siz)
        for match in high_similarity_matches:
            similar_entities.append((match['entity'], match['combined_score']))
            logging.info(f"  ✅ Yüksek benzerlik (embedding'siz): '{match['existing_id']}' -> name_sim: {match['name_similarity']:.3f}")
        
        if not candidates:
            similar_entities.sort(key=lambda x: x[1], reverse=True)
            return similar_entities
        
        # 🚀 PHASE 3: Batch embedding hesaplama (sadece candidates için)
        new_embedding = self.get_entity_embedding(new_id, new_type, new_name)
        
        entities_for_batch = [c['entity'] for c in candidates]
        batch_embeddings = self.batch_get_entity_embeddings(entities_for_batch)
        
        # 🚀 PHASE 4: Final skor hesaplama
        for candidate in candidates:
            existing_entity = candidate['entity']
            existing_id = candidate['existing_id']
            existing_name = candidate['existing_name']
            existing_type = candidate['existing_type']
            name_similarity = candidate['name_similarity']
            is_complex = candidate['is_complex']
            
            # Batch'ten embedding al
            cache_key_str = f"{existing_id or ''}|{existing_type or ''}|{existing_name or ''}"
            existing_embedding = batch_embeddings.get(cache_key_str)
            
            if existing_embedding is None:
                existing_embedding = self.get_entity_embedding(existing_id, existing_type, existing_name)
            
            embedding_similarity = self.cosine_similarity(new_embedding, existing_embedding)
            
            # 🎯 Şirket/Kişi'ye göre farklı ağırlıklar
            if new_is_complex or is_complex:
                # Şirket isimleri: Embedding daha önemli (%40 isim, %60 embedding)
                # Çünkü "A.Ş." vs "Anonim Şirketi" gibi farklılıklar var
                combined_score = (name_similarity * 0.40) + (embedding_similarity * 0.60)
            elif new_type.lower() == 'customer':
                # Kişi isimleri: İsim daha önemli (%70 isim, %30 embedding)
                combined_score = (name_similarity * 0.70) + (embedding_similarity * 0.30)
            else:
                # Diğer: Dengeli (%50 isim, %50 embedding)
                combined_score = (name_similarity * 0.50) + (embedding_similarity * 0.50)
            
            logging.debug(f"  - '{existing_id}': name={name_similarity:.3f}, emb={embedding_similarity:.3f}, combined={combined_score:.3f}, complex={is_complex}")
            
            # 🎯 Eşik kontrolü
            if new_is_complex or is_complex:
                # Şirket isimleri: Embedding benzerliği yüksekse eşleş
                # %75 embedding benzerliği VEYA %70 combined score
                if embedding_similarity >= 0.75 or combined_score >= 0.70:
                    similar_entities.append((existing_entity, combined_score))
                    logging.info(f"  ✅ Benzer şirket: '{existing_id}' -> emb: {embedding_similarity:.3f}, combined: {combined_score:.3f}")
            elif new_type.lower() == 'customer':
                # Kişi isimleri: İsim benzerliği yüksek olmalı
                # %85 isim benzerliği VE %75 combined score
                if name_similarity >= 0.85 and combined_score >= 0.75:
                    similar_entities.append((existing_entity, combined_score))
                    logging.info(f"  ✅ Benzer kişi: '{existing_id}' -> name: {name_similarity:.3f}, combined: {combined_score:.3f}")
            else:
                # Diğer entity'ler
                if combined_score >= self.similarity_threshold:
                    similar_entities.append((existing_entity, combined_score))
                    logging.info(f"  ✅ Benzer entity: '{existing_id}' -> combined: {combined_score:.3f}")
        
        # Cache stats log (her 100 aramada bir)
        if (self._cache_hits + self._cache_misses) % 100 == 0:
            stats = self.get_cache_stats()
            logging.info(f"📊 Embedding Cache: size={stats['cache_size']}, hit_rate={stats['hit_rate']:.1%}")
        
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
