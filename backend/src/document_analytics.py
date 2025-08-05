"""
Document Analytics Module - Provides analysis functions for document relationships
"""
import logging
from typing import Dict, List, Any
from src.shared.common_fn import execute_graph_query
from src.shared.constants import PERSON_POLICY_COUNT_QUERY, COMPANY_ANALYSIS_QUERY

def get_person_policy_analytics(graph) -> List[Dict[str, Any]]:
    """
    Kişi bazlı poliçe analizi - her kişinin kaç poliçesi olduğunu ve bunların bağlantılarını döner
    
    Returns:
        List[Dict]: [{"person_name": str, "total_policies": int, "document_connections": int, "policy_files": List[str]}]
    """
    try:
        result = execute_graph_query(graph, PERSON_POLICY_COUNT_QUERY)
        analytics = []
        
        for record in result:
            if record['total_policies'] > 1:  # Sadece birden fazla poliçesi olanları göster
                analytics.append({
                    "person_name": record['person_name'],
                    "total_policies": record['total_policies'],
                    "document_connections": record['document_connections'] or 0,
                    "policy_files": record['policy_files'] or []
                })
        
        logging.info(f"Found {len(analytics)} people with multiple policies")
        return analytics
        
    except Exception as e:
        logging.error(f"Error in person policy analytics: {e}")
        return []

def get_company_analytics(graph) -> List[Dict[str, Any]]:
    """
    Şirket bazlı analiz - hangi sigorta şirketinin kaç poliçesi olduğunu döner
    
    Returns:
        List[Dict]: [{"company_name": str, "total_policies": int}]
    """
    try:
        result = execute_graph_query(graph, COMPANY_ANALYSIS_QUERY)
        analytics = []
        
        for record in result:
            analytics.append({
                "company_name": record['company_name'],
                "total_policies": record['total_policies']
            })
        
        logging.info(f"Found {len(analytics)} insurance companies")
        return analytics
        
    except Exception as e:
        logging.error(f"Error in company analytics: {e}")
        return []

def get_document_relationship_stats(graph) -> Dict[str, Any]:
    """
    Document relationship istatistikleri
    
    Returns:
        Dict: {"total_documents": int, "connected_documents": int, "relationship_types": Dict}
    """
    try:
        # Toplam document sayısı
        total_docs_query = "MATCH (d:Document) RETURN count(d) as total"
        total_result = execute_graph_query(graph, total_docs_query)
        total_documents = total_result[0]['total'] if total_result else 0
        
        # Bağlı document sayısı
        connected_docs_query = """
        MATCH (d:Document)
        WHERE exists((d)-[:BELONGS_TO_SAME_PERSON|SAME_POLICY_TYPE|SAME_INSURANCE_COMPANY|SAME_OWNER]-())
        RETURN count(DISTINCT d) as connected
        """
        connected_result = execute_graph_query(graph, connected_docs_query)
        connected_documents = connected_result[0]['connected'] if connected_result else 0
        
        # Relationship türleri ve sayıları
        rel_types_query = """
        MATCH (d1:Document)-[r]->(d2:Document)
        WHERE type(r) IN ['BELONGS_TO_SAME_PERSON', 'SAME_POLICY_TYPE', 'SAME_INSURANCE_COMPANY', 'SAME_OWNER']
        RETURN type(r) as relationship_type, count(r) as count
        ORDER BY count DESC
        """
        rel_types_result = execute_graph_query(graph, rel_types_query)
        relationship_types = {record['relationship_type']: record['count'] for record in rel_types_result}
        
        stats = {
            "total_documents": total_documents,
            "connected_documents": connected_documents,
            "connection_rate": f"{(connected_documents/total_documents)*100:.1f}%" if total_documents > 0 else "0%",
            "relationship_types": relationship_types
        }
        
        logging.info(f"Document relationship stats: {stats}")
        return stats
        
    except Exception as e:
        logging.error(f"Error in document relationship stats: {e}")
        return {}

def search_person_documents(graph, person_name: str) -> Dict[str, Any]:
    """
    Belirli bir kişinin tüm document'larını ve bunlar arasındaki bağlantıları döner
    
    Args:
        person_name: Aranacak kişi adı
        
    Returns:
        Dict: {"person_name": str, "documents": List, "connections": List}
    """
    try:
        query = """
        MATCH (person:Person {id: $person_name})<-[:HAS_ENTITY]-(c:Chunk)-[:PART_OF]->(d:Document)
        WITH person, collect(DISTINCT d) AS person_docs
        
        // Bu kişiye ait document'lar arasındaki bağlantıları bul
        UNWIND person_docs AS doc1
        OPTIONAL MATCH (doc1)-[r:BELONGS_TO_SAME_PERSON]-(doc2:Document)
        WHERE doc2 IN person_docs
        
        WITH person, person_docs, collect(DISTINCT {
            from: doc1.fileName,
            to: doc2.fileName,
            type: type(r),
            strength: r.relationship_strength
        }) AS connections
        
        RETURN 
            person.id AS person_name,
            [d.fileName FOR d IN person_docs] AS documents,
            connections
        """
        
        result = execute_graph_query(graph, query, params={"person_name": person_name})
        
        if result:
            data = result[0]
            return {
                "person_name": data['person_name'],
                "documents": data['documents'] or [],
                "connections": [conn for conn in data['connections'] if conn['to'] is not None],
                "total_documents": len(data['documents'] or [])
            }
        else:
            return {"person_name": person_name, "documents": [], "connections": [], "total_documents": 0}
            
    except Exception as e:
        logging.error(f"Error searching person documents: {e}")
        return {"person_name": person_name, "documents": [], "connections": [], "total_documents": 0}
