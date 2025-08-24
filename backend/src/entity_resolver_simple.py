"""
Simplified Entity Resolution modülü - Neo4j similarity kullanır
"""

import logging
import re
from typing import Dict, List, Tuple, Optional

class SimpleEntityResolver:
    """
    Neo4j'nin kendi similarity fonksiyonlarını kullanarak entity resolution yapar
    """
    
    def __init__(self, similarity_threshold: float = 0.5):
        """
        Args:
            similarity_threshold: Neo4j similarity eşiği (0-1 arası)
        """
        self.similarity_threshold = similarity_threshold
        
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
        
    def find_similar_entities_neo4j(self, graph, entity_id: str, entity_type: str, entity_name: str = None) -> List[Tuple]:
        """
        Neo4j'de benzer entity'leri bulur - similarity fonksiyonları kullanır
        """
        if not entity_name:
            entity_name = entity_id
            
        logging.debug(f"🔍 '{entity_id}' için benzer entity'ler aranıyor...")
        
        # Neo4j string similarity kullanarak arama
        query = """
        MATCH (e:__Entity__)
        WHERE e.entity_type = $entity_type 
        AND e.id <> $entity_id
        WITH e, 
             apoc.text.jaroWinklerDistance(toLower(e.name), toLower($entity_name)) as jaro_score,
             apoc.text.sorensenDiceSimilarity(toLower(e.name), toLower($entity_name)) as dice_score
        WHERE jaro_score >= $threshold 
           OR dice_score >= $threshold
        RETURN e.id as id, 
               e.name as name,
               e.entity_type as entity_type,
               jaro_score,
               dice_score,
               (jaro_score + dice_score) / 2.0 as avg_score
        ORDER BY avg_score DESC
        LIMIT 5
        """
        
        try:
            result = graph.run(query, 
                             entity_type=entity_type,
                             entity_id=entity_id,
                             entity_name=entity_name,
                             threshold=self.similarity_threshold)
            
            similar_entities = []
            for record in result:
                entity_data = {
                    'id': record['id'],
                    'name': record['name'],
                    'entity_type': record['entity_type']
                }
                score = record['avg_score']
                
                logging.debug(f"  - Benzer entity: '{record['id']}' (name: '{record['name']}')")
                logging.debug(f"    Jaro-Winkler: {record['jaro_score']:.3f}")
                logging.debug(f"    Dice: {record['dice_score']:.3f}")  
                logging.debug(f"    Ortalama skor: {score:.3f}")
                
                similar_entities.append((entity_data, score))
                
            logging.info(f"  📊 {len(similar_entities)} benzer entity bulundu")
            return similar_entities
            
        except Exception as e:
            logging.warning(f"⚠️ Neo4j similarity sorgusu başarısız: {e}")
            # Fallback: basit string matching
            return self._fallback_simple_matching(graph, entity_id, entity_type, entity_name)
    
    def _fallback_simple_matching(self, graph, entity_id: str, entity_type: str, entity_name: str) -> List[Tuple]:
        """
        APOC olmadığında fallback - basit string matching
        """
        logging.info("🔄 Fallback basit string matching kullanılıyor...")
        
        # Normalize edilmiş isimlerle kontrol
        normalized_name = self.normalize_name(entity_name)
        
        query = """
        MATCH (e)
        WHERE ($entity_type IN labels(e) OR e.entity_type = $entity_type)
        AND e.id <> $entity_id
        RETURN e.id as id, e.name as name, e.entity_type as entity_type
        """
        
        try:
            result = graph.run(query, entity_type=entity_type, entity_id=entity_id)
            similar_entities = []
            
            for record in result:
                existing_name = record['name'] or record['id']
                normalized_existing = self.normalize_name(existing_name)
                
                # Basit substring kontrolü
                if (normalized_name in normalized_existing or 
                    normalized_existing in normalized_name or
                    normalized_name == normalized_existing):
                    
                    entity_data = {
                        'id': record['id'],
                        'name': record['name'],
                        'entity_type': record['entity_type']
                    }
                    # Substring match için 0.8 score ver
                    score = 0.8
                    similar_entities.append((entity_data, score))
                    
                    logging.debug(f"  - Substring match: '{record['id']}' -> skor: {score}")
            
            return similar_entities
            
        except Exception as e:
            logging.error(f"❌ Fallback matching de başarısız: {e}")
            return []
    
    def resolve_entities(self, graph, entities: List[Dict]) -> Tuple[List[Dict], Dict[str, str]]:
        """
        Entity listesini resolve eder ve duplicate mapping döner
        """
        logging.info(f"🔄 {len(entities)} entity için resolution başlıyor...")
        
        final_entities = []
        id_mapping = {}  # original_id -> final_id
        processed_ids = set()
        
        for entity in entities:
            entity_id = entity['id']
            entity_type = entity.get('entity_type', 'Unknown')
            entity_name = entity.get('name', entity_id)
            
            if entity_id in processed_ids:
                continue
                
            # Benzer entity'leri bul
            similar_entities = self.find_similar_entities_neo4j(
                graph, entity_id, entity_type, entity_name
            )
            
            if similar_entities:
                # En yüksek skorlu entity'yi kullan
                best_match, best_score = similar_entities[0]
                final_id = best_match['id']
                
                logging.info(f"  ✅ '{entity_id}' -> '{final_id}' eşleştirildi (skor: {best_score:.3f})")
                
                # Mapping'e ekle
                id_mapping[entity_id] = final_id
                processed_ids.add(entity_id)
                
                # Best match'i final entities'e ekle (eğer yoksa)
                if not any(e['id'] == final_id for e in final_entities):
                    final_entities.append(best_match)
                    
            else:
                # Benzer entity yoksa, kendi ID'sini kullan
                id_mapping[entity_id] = entity_id
                processed_ids.add(entity_id)
                final_entities.append(entity)
                
                logging.debug(f"  🆕 '{entity_id}' yeni entity olarak eklendi")
        
        resolution_stats = {
            'total_entities': len(entities),
            'final_entities': len(final_entities),
            'reused_entities': len(entities) - len(final_entities),
            'mapping_count': len(id_mapping)
        }
        
        logging.info(f"✅ Entity Resolution tamamlandı:")
        logging.info(f"  - Toplam entity: {resolution_stats['total_entities']}")
        logging.info(f"  - Yeni node sayısı: {resolution_stats['final_entities']}")
        logging.info(f"  - Mevcut entity kullanımı: {resolution_stats['reused_entities']}")
        
        return final_entities, id_mapping
