"""
Document Analytics Module - Provides analysis functions for document relationships
"""
import logging
from typing import Dict, List, Any
from src.shared.common_fn import execute_graph_query
from src.shared.constants import PERSON_POLICY_COUNT_QUERY, COMPANY_ANALYSIS_QUERY

def get_person_policy_analytics(graph) -> List[Dict[str, Any]]:
    """
    Kişi bazlı poliçe analizi - HAS_POLICY ilişkisi ile kişilerin poliçelerini analiz eder
    
    Returns:
        List[Dict]: [{"person_name": str, "total_policies": int, "total_chunk_mentions": int, 
                     "avg_mentions_per_policy": float, "policy_files": List[str], 
                     "confidence_levels": List[str]}]
    """
    try:
        result = execute_graph_query(graph, PERSON_POLICY_COUNT_QUERY)
        analytics = []
        
        for record in result:
            if record['total_policies'] >= 1:  # En az bir poliçesi olanları göster
                analytics.append({
                    "person_name": record['person_name'],
                    "total_policies": record['total_policies'],
                    "total_chunk_mentions": record['total_chunk_mentions'] or 0,
                    "avg_mentions_per_policy": round(record['avg_mentions_per_policy'] or 0.0, 2),
                    "policy_files": record['policy_files'] or [],
                    "confidence_levels": record['confidence_levels'] or []
                })
        
        logging.info(f"Found {len(analytics)} people with policies (HAS_POLICY relationships)")
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
        
        # Kişiler tarafından sahip olunan document sayısı
        person_connected_docs_query = """
        MATCH (p:Person)-[:HAS_POLICY]->(d:Document)
        RETURN count(DISTINCT d) as person_connected_docs, count(DISTINCT p) as persons_with_policies
        """
        person_result = execute_graph_query(graph, person_connected_docs_query)
        person_connected_documents = person_result[0]['person_connected_docs'] if person_result else 0
        persons_with_policies = person_result[0]['persons_with_policies'] if person_result else 0
        
        # Relationship türleri ve sayıları (HAS_POLICY + diğerleri)
        rel_types_query = """
        MATCH (d1)-[r]->(d2:Document)
        WHERE type(r) IN ['HAS_POLICY', 'SAME_POLICY_TYPE', 'SAME_INSURANCE_COMPANY', 'SAME_OWNER']
        RETURN type(r) as relationship_type, count(r) as count
        ORDER BY count DESC
        """
        rel_types_result = execute_graph_query(graph, rel_types_query)
        relationship_types = {record['relationship_type']: record['count'] for record in rel_types_result}
        
        stats = {
            "total_documents": total_documents,
            "person_connected_documents": person_connected_documents,
            "persons_with_policies": persons_with_policies,
            "person_coverage_rate": f"{(person_connected_documents/total_documents)*100:.1f}%" if total_documents > 0 else "0%",
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
        MATCH (person:Person {id: $person_name})-[r:HAS_POLICY]->(d:Document)
        WITH person, collect(DISTINCT {
            document: d.fileName,
            chunk_count: r.chunk_count,
            confidence: r.confidence,
            created_at: r.created_at
        }) AS policy_details
        
        RETURN 
            person.id AS person_name,
            [p.document FOR p IN policy_details] AS documents,
            policy_details AS policy_connections,
            size(policy_details) AS total_policies
        """
        
        result = execute_graph_query(graph, query, params={"person_name": person_name})
        
        if result:
            data = result[0]
            return {
                "person_name": data['person_name'],
                "documents": data['documents'] or [],
                "policy_connections": data['policy_connections'] or [],
                "total_policies": data['total_policies'] or 0
            }
        else:
            return {"person_name": person_name, "documents": [], "policy_connections": [], "total_policies": 0}
            
    except Exception as e:
        logging.error(f"Error searching person documents: {e}")
        return {"person_name": person_name, "documents": [], "policy_connections": [], "total_policies": 0}
