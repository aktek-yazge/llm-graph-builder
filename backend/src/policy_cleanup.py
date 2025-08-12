"""
Policy Node Cleanup Utilities
Bu modül Policy entity'lerini Document node'larına yönlendiren yardımcı fonksiyonları içerir.
"""
import logging
from typing import Dict, Any, List

def cleanup_policy_nodes_to_document(graph, file_name: str) -> Dict[str, Any]:
    """
    Bu dosya için oluşturulan Policy node'larını bulur ve tüm relationship'leri 
    Document node'una yönlendirir. Policy node'larını siler.
    
    Args:
        graph: Neo4j graph connection
        file_name: İşlenecek dosya adı
    
    Returns:
        Temizleme işlemi sonuç raporu
    """
    try:
        logging.info(f"Policy node cleanup başlıyor: {file_name}")
        
        # 1. Bu dosyaya ait Policy node'larını bul
        find_policy_query = """
        MATCH (d:Document {fileName: $filename})
        MATCH (d)<-[:PART_OF]-(c:Chunk)-[:HAS_ENTITY]->(p:Policy)
        RETURN DISTINCT p, elementId(p) as policyId, p.id as policyName
        """
        
        policy_result = graph.query(find_policy_query, params={"filename": file_name})
        policy_count = len(policy_result)
        
        if policy_count == 0:
            logging.info(f"✅ {file_name} için Policy node bulunamadı")
            return {
                "status": "no_policies_found",
                "policy_nodes_found": 0,
                "relationships_moved": 0,
                "policy_nodes_deleted": 0
            }
        
        logging.info(f"⚠️ {policy_count} Policy node bulundu: {[p['policyName'] for p in policy_result]}")
        
        # 2. Her Policy node için tüm relationship'leri bul ve Document'a yönlendir
        total_relationships_moved = 0
        policy_nodes_processed = []
        
        for policy_data in policy_result:
            policy_id = policy_data['policyId']
            policy_name = policy_data['policyName']
            
            logging.info(f"Policy işleniyor: {policy_name} (ID: {policy_id})")
            
            # Policy'ye gelen relationship'leri bul (incoming)
            incoming_rels_query = """
            MATCH (d:Document {fileName: $filename})
            MATCH (source)-[r]->(p:Policy)
            WHERE elementId(p) = $policy_id
              AND source <> d  // Document dışındaki node'lar
            RETURN source, r, type(r) as relType, properties(r) as relProps, 
                   elementId(source) as sourceId, labels(source) as sourceLabels
            """
            
            incoming_rels = graph.query(incoming_rels_query, params={
                "filename": file_name,
                "policy_id": policy_id
            })
            
            # Policy'den çıkan relationship'leri bul (outgoing)
            outgoing_rels_query = """
            MATCH (d:Document {fileName: $filename})
            MATCH (p:Policy)-[r]->(target)
            WHERE elementId(p) = $policy_id
              AND target <> d  // Document dışındaki node'lar
            RETURN target, r, type(r) as relType, properties(r) as relProps,
                   elementId(target) as targetId, labels(target) as targetLabels
            """
            
            outgoing_rels = graph.query(outgoing_rels_query, params={
                "filename": file_name,
                "policy_id": policy_id
            })
            
            relationships_for_policy = len(incoming_rels) + len(outgoing_rels)
            logging.info(f"Policy {policy_name}: {len(incoming_rels)} gelen, {len(outgoing_rels)} giden relationship")
            
            # Detaylı relationship bilgilerini logla
            if incoming_rels:
                logging.info(f"📥 Gelen relationships:")
                for i, rel in enumerate(incoming_rels, 1):
                    source_labels = rel.get('sourceLabels', ['Unknown'])
                    source_type = source_labels[0] if source_labels else 'Unknown'
                    logging.info(f"  {i}. {source_type} -[{rel['relType']}]-> Policy")
            
            if outgoing_rels:
                logging.info(f"📤 Giden relationships:")
                for i, rel in enumerate(outgoing_rels, 1):
                    target_labels = rel.get('targetLabels', ['Unknown'])
                    target_type = target_labels[0] if target_labels else 'Unknown'
                    logging.info(f"  {i}. Policy -[{rel['relType']}]-> {target_type}")
            
            # Taşınan relationship sayacı
            moved_incoming = 0
            moved_outgoing = 0
            
            # 3. Incoming relationship'leri Document'a yönlendir
            for i, rel_data in enumerate(incoming_rels, 1):
                try:
                    source_labels = rel_data.get('sourceLabels', ['Unknown'])
                    source_type = source_labels[0] if source_labels else 'Unknown'
                    
                    logging.info(f"🔄 Incoming {i}/{len(incoming_rels)}: {source_type} -[{rel_data['relType']}]-> Policy → Document'a yönlendiriliyor...")
                    
                    # Yeni relationship oluştur: source -> Document
                    redirect_incoming_query = """
                    MATCH (d:Document {fileName: $filename})
                    MATCH (source)
                    WHERE elementId(source) = $source_id
                    MERGE (source)-[newRel:`RELATED_TO_DOCUMENT`]->(d)
                    SET newRel += $rel_props
                    SET newRel.original_target = 'Policy'
                    SET newRel.original_relationship_type = $original_rel_type
                    SET newRel.migrated_from_policy = true
                    """
                    
                    graph.query(redirect_incoming_query, params={
                        "filename": file_name,
                        "source_id": rel_data['sourceId'],
                        "rel_props": rel_data['relProps'] or {},
                        "original_rel_type": rel_data['relType']
                    })
                    
                    moved_incoming += 1
                    total_relationships_moved += 1
                    logging.info(f"✅ Başarılı: {source_type} -[RELATED_TO_DOCUMENT]-> Document (orijinal: {rel_data['relType']})")
                    
                except Exception as rel_error:
                    logging.error(f"❌ Incoming relationship yönlendirme hatası ({i}/{len(incoming_rels)}): {rel_error}")
            
            # 4. Outgoing relationship'leri Document'tan yönlendir
            for i, rel_data in enumerate(outgoing_rels, 1):
                try:
                    target_labels = rel_data.get('targetLabels', ['Unknown'])
                    target_type = target_labels[0] if target_labels else 'Unknown'
                    
                    logging.info(f"🔄 Outgoing {i}/{len(outgoing_rels)}: Policy -[{rel_data['relType']}]-> {target_type} → Document'tan yönlendiriliyor...")
                    
                    # Yeni relationship oluştur: Document -> target
                    redirect_outgoing_query = """
                    MATCH (d:Document {fileName: $filename})
                    MATCH (target)
                    WHERE elementId(target) = $target_id
                    MERGE (d)-[newRel:`DOCUMENT_CONTAINS`]->(target)
                    SET newRel += $rel_props
                    SET newRel.original_source = 'Policy'
                    SET newRel.original_relationship_type = $original_rel_type
                    SET newRel.migrated_from_policy = true
                    """
                    
                    graph.query(redirect_outgoing_query, params={
                        "filename": file_name,
                        "target_id": rel_data['targetId'],
                        "rel_props": rel_data['relProps'] or {},
                        "original_rel_type": rel_data['relType']
                    })
                    
                    moved_outgoing += 1
                    total_relationships_moved += 1
                    logging.info(f"✅ Başarılı: Document -[DOCUMENT_CONTAINS]-> {target_type} (orijinal: {rel_data['relType']})")
                    
                except Exception as rel_error:
                    logging.error(f"❌ Outgoing relationship yönlendirme hatası ({i}/{len(outgoing_rels)}): {rel_error}")
            
            policy_nodes_processed.append({
                "policy_name": policy_name,
                "policy_id": policy_id,
                "total_relationships_found": relationships_for_policy,
                "incoming_relationships_moved": moved_incoming,
                "outgoing_relationships_moved": moved_outgoing,
                "total_moved": moved_incoming + moved_outgoing
            })
            
            logging.info(f"📊 Policy {policy_name} özet: {moved_incoming}/{len(incoming_rels)} gelen, {moved_outgoing}/{len(outgoing_rels)} giden relationship taşındı")
        
        # 5. Tüm relationship'ler yönlendirildikten sonra Policy node'larını sil
        delete_policy_query = """
        MATCH (d:Document {fileName: $filename})
        MATCH (d)<-[:PART_OF]-(c:Chunk)-[:HAS_ENTITY]->(p:Policy)
        DETACH DELETE p
        RETURN count(p) as deleted_policies
        """
        
        delete_result = graph.query(delete_policy_query, params={"filename": file_name})
        deleted_policies = delete_result[0]['deleted_policies'] if delete_result else 0
        
        # Detaylı özet raporu
        logging.info(f"📋 POLICY CLEANUP ÖZET RAPORU:")
        logging.info(f"  📁 Dosya: {file_name}")
        logging.info(f"  🔍 Bulunan Policy node sayısı: {policy_count}")
        logging.info(f"  📊 Toplam bulunan relationship: {sum(p['total_relationships_found'] for p in policy_nodes_processed)}")
        logging.info(f"  ✅ Başarıyla taşınan relationship: {total_relationships_moved}")
        logging.info(f"  🗑️ Silinen Policy node sayısı: {deleted_policies}")
        
        for policy in policy_nodes_processed:
            logging.info(f"  └── {policy['policy_name']}: {policy['total_moved']}/{policy['total_relationships_found']} relationship taşındı")
        
        logging.info(f"✅ Policy cleanup tamamlandı: {deleted_policies} Policy node silindi, {total_relationships_moved} relationship Document'a yönlendirildi")
        
        return {
            "status": "success",
            "policy_nodes_found": policy_count,
            "relationships_moved": total_relationships_moved,
            "policy_nodes_deleted": deleted_policies,
            "processed_policies": policy_nodes_processed
        }
        
    except Exception as e:
        logging.error(f"Policy cleanup hatası: {e}")
        return {
            "status": "error",
            "error": str(e),
            "policy_nodes_found": 0,
            "relationships_moved": 0,
            "policy_nodes_deleted": 0
        }


def promote_chunk_entities_to_document(graph, file_name: str, entity_promotion_rules: List[str]) -> Dict[str, Any]:
    """
    Belirtilen entity tiplerini Chunk'tan Document'a terfi ettirir.
    
    Args:
        graph: Neo4j graph connection
        file_name: İşlenecek dosya adı
        entity_promotion_rules: Document'a terfi ettirilecek entity tipleri
                               Örnek: ['Address', 'Company', 'Person', 'Phone', 'Email']
    
    Returns:
        Terfi ettirme işlemi sonuç raporu
    """
    try:
        logging.info(f"Entity promotion başlıyor: {file_name}")
        logging.info(f"Terfi ettirilecek entity tipleri: {entity_promotion_rules}")
        
        if not entity_promotion_rules:
            logging.info("Terfi ettirilecek entity tipi yok")
            return {
                "status": "no_rules",
                "promoted_entities": 0,
                "relationships_created": 0
            }
        
        total_promoted = 0
        total_relationships_created = 0
        promotion_details = []
        
        # Her entity tipi için işlem yap
        for entity_type in entity_promotion_rules:
            logging.info(f"🔄 Entity tipi işleniyor: {entity_type}")
            
            # Bu entity tipindeki tüm entity'leri bul
            find_entities_query = """
            MATCH (d:Document {fileName: $filename})
            MATCH (d)<-[:PART_OF]-(c:Chunk)-[:HAS_ENTITY]->(e)
            WHERE $entity_type IN labels(e)
            RETURN DISTINCT e, elementId(e) as entityId, e.id as entityName, 
                   labels(e) as entityLabels, count(c) as chunkCount
            """
            
            entity_result = graph.query(find_entities_query, params={
                "filename": file_name,
                "entity_type": entity_type
            })
            
            entity_count = len(entity_result)
            logging.info(f"  📊 {entity_type} tipinde {entity_count} entity bulundu")
            
            if entity_count == 0:
                continue
            
            entities_promoted = 0
            relationships_created = 0
            
            # Her entity için Document'a direkt bağlantı oluştur
            for entity_data in entity_result:
                entity_id = entity_data['entityId']
                entity_name = entity_data['entityName']
                chunk_count = entity_data['chunkCount']
                
                try:
                    logging.info(f"  🔗 {entity_type} '{entity_name}' Document'a bağlanıyor ({chunk_count} chunk'tan)")
                    
                    # Document'a direkt bağlantı oluştur
                    create_document_relation_query = """
                    MATCH (d:Document {fileName: $filename})
                    MATCH (e)
                    WHERE elementId(e) = $entity_id
                    MERGE (d)-[newRel:`CONTAINS_ENTITY`]->(e)
                    SET newRel.entity_type = $entity_type
                    SET newRel.chunk_count = $chunk_count
                    SET newRel.promoted_from_chunks = true
                    SET newRel.created_date = datetime()
                    RETURN newRel
                    """
                    
                    result = graph.query(create_document_relation_query, params={
                        "filename": file_name,
                        "entity_id": entity_id,
                        "entity_type": entity_type,
                        "chunk_count": chunk_count
                    })
                    
                    if result:
                        entities_promoted += 1
                        relationships_created += 1
                        logging.info(f"    ✅ Başarılı: Document -[CONTAINS_ENTITY]-> {entity_type}({entity_name})")
                    
                except Exception as entity_error:
                    logging.error(f"    ❌ Entity promotion hatası ({entity_type}/{entity_name}): {entity_error}")
            
            promotion_details.append({
                "entity_type": entity_type,
                "entities_found": entity_count,
                "entities_promoted": entities_promoted,
                "relationships_created": relationships_created
            })
            
            total_promoted += entities_promoted
            total_relationships_created += relationships_created
            
            logging.info(f"  📋 {entity_type} özet: {entities_promoted}/{entity_count} entity Document'a bağlandı")
        
        # Genel özet
        logging.info(f"📋 ENTITY PROMOTION ÖZET RAPORU:")
        logging.info(f"  📁 Dosya: {file_name}")
        logging.info(f"  📊 Toplam terfi ettirilen entity: {total_promoted}")
        logging.info(f"  🔗 Oluşturulan Document-Entity ilişkisi: {total_relationships_created}")
        
        for detail in promotion_details:
            success_rate = f"{detail['entities_promoted']}/{detail['entities_found']}"
            logging.info(f"  └── {detail['entity_type']}: {success_rate} entity terfi edildi")
        
        return {
            "status": "success",
            "promoted_entities": total_promoted,
            "relationships_created": total_relationships_created,
            "promotion_details": promotion_details
        }
        
    except Exception as e:
        logging.error(f"Entity promotion hatası: {e}")
        return {
            "status": "error",
            "error": str(e),
            "promoted_entities": 0,
            "relationships_created": 0
        }
