"""
Policy Graph Structure Enhancement
Bu modül sigorta poliçeleri için müşteri me        logging.info(f"📊 Entity kategorileri (YENİ YAPI):")
        logging.info(f"  👥 Müşteriler (Customer): {len(customers)}")
        logging.info(f"  📋 Poliçeler (InsurancePolicy): {len(policies)}")
        logging.info(f"  🔢 Poliçe Numaraları: {len(policy_numbers)}")
        logging.info(f"  📅 Poliçe Yılları (PolicyYear): {len(policy_years)}")
        logging.info(f"  🏷️ Poliçe Tipleri (PolicyType): {len(policy_types)}")
        logging.info(f"  🛡️ Teminatlar (Coverage): {len(coverages)}")
        logging.info(f"  👨‍💼 Acenteler (Agent): {len(agents)}")
        logging.info(f"  🏠 Varlıklar (Asset): {len(assets)}")
        
        # 3. Müşteri merkezli yapı oluştur (yeni model)
        structures_created = create_customer_policy_structure(
            graph, file_name, customers, policies, policy_numbers, 
            policy_years, policy_types, coverages, agents, assets
        )apısı oluşturur.

YENİ YAPISAL MODEL:
Customer (Person/Policyholder) -[:OWNS]-> (Policy:InsurancePolicy) -[:FOR_YEAR]-> (Year:PolicyYear)
                                                |
                                                +-[:OF_TYPE]-> (Type:PolicyType) # Kasko, Trafik, Konut, DASK
                                                +-[:HAS_COVERAGE]-> (Coverage)
                                                +-[:DOCUMENTED_IN]-> (Document)
                                                +-[:HANDLED_BY]-> (Agent)
                                                +-[:COVERS]-> (Asset) # Araç, Ev, vb.

Bu yapıda Policy node'ları ana varlık olarak korunur ve müşteri merkezli ilişkiler kurulur.
"""
import logging
from typing import Dict, Any, List
import re

def enhance_policy_graph_structure(graph, file_name: str) -> Dict[str, Any]:
    """
    Bu dosya için sigorta poliçeleri etrafında müşteri merkezli graph yapısı oluşturur.
    
    YENİ OLUŞTURULAN YAPI:
    Customer (Person/Policyholder) -[:OWNS]-> (Policy:InsurancePolicy) -[:FOR_YEAR]-> (Year:PolicyYear)
                                                    |
                                                    +-[:OF_TYPE]-> (Type:PolicyType) # Kasko, Trafik, Konut, DASK
                                                    +-[:HAS_COVERAGE]-> (Coverage)
                                                    +-[:DOCUMENTED_IN]-> (Document)
                                                    +-[:HANDLED_BY]-> (Agent)
                                                    +-[:COVERS]-> (Asset) # Araç, Ev, vb.
    
    Args:
        graph: Neo4j graph connection
        file_name: İşlenecek dosya adı
    
    Returns:
        Graph enhancement işlemi sonuç raporu
    """
    try:
        logging.info(f"🏗️ Policy graph structure enhancement başlıyor: {file_name}")
        
        # 1. Dosyaya ait temel entityleri topla
        entities_query = """
        MATCH (d:Document {fileName: $filename})
        MATCH (d)<-[:PART_OF]-(c:Chunk)-[:HAS_ENTITY]->(e:__Entity__)
        RETURN DISTINCT e, labels(e) as entity_labels, e.id as entity_id
        """
        
        entities_result = graph.query(entities_query, params={"filename": file_name})
        
        # 2. Entity'leri kategorize et (yeni yapıya göre)
        customers = []  # Person/Policyholder
        policies = []   # Policy (artık InsurancePolicy olarak kullanılacak)
        policy_numbers = []
        policy_years = []
        policy_types = []
        coverages = []
        agents = []
        assets = []     # Building, Risk vb. Asset olarak kategorize edilecek
        
        for entity in entities_result:
            labels = entity['entity_labels']
            entity_id = entity['entity_id']
            
            if 'Policyholder' in labels or 'Person' in labels:
                customers.append(entity)
            elif 'Policy' in labels:
                policies.append(entity)
            elif 'PolicyNumber' in labels:
                policy_numbers.append(entity)
            elif 'PolicyYear' in labels:
                policy_years.append(entity)
            elif 'PolicyType' in labels:
                policy_types.append(entity)
            elif 'Coverage' in labels:
                coverages.append(entity)
            elif 'Agent' in labels:
                agents.append(entity)
            elif 'Building' in labels or 'Risk' in labels:
                assets.append(entity)  # Asset kategorisine ekle
        
        logging.info(f"� Entity kategorileri:")
        logging.info(f"  👥 Müşteriler: {len(customers)}")
        logging.info(f"  📋 Poliçeler: {len(policies)}")
        logging.info(f"  🔢 Poliçe Numaraları: {len(policy_numbers)}")
        logging.info(f"  📅 Poliçe Yılları: {len(policy_years)}")
        logging.info(f"  🏷️ Poliçe Tipleri: {len(policy_types)}")
        logging.info(f"  🛡️ Teminatlar: {len(coverages)}")
        logging.info(f"  👨‍💼 Acenteler: {len(agents)}")
        
        # 3. Müşteri merkezli yapı oluştur
        structures_created = create_customer_policy_structure(
            graph, file_name, customers, policies, policy_numbers, 
            policy_years, policy_types, coverages, agents
        )
        
        # 4. Document bağlantıları oluştur
        document_links = create_document_links(graph, file_name)
        
        # 5. Sonuç raporu
        logging.info(f"📋 POLICY GRAPH ENHANCEMENT ÖZET RAPORU:")
        logging.info(f"  📁 Dosya: {file_name}")
        logging.info(f"  🏗️ Oluşturulan yapı sayısı: {structures_created}")
        logging.info(f"  🔗 Document bağlantıları: {document_links}")
        
        return {
            "status": "success",
            "file_name": file_name,
            "customers_found": len(customers),
            "policies_found": len(policies),
            "structures_created": structures_created,
            "document_links": document_links
        }
        
    except Exception as e:
        logging.error(f"Policy graph enhancement hatası: {e}")
        return {
            "status": "error",
            "error": str(e),
            "file_name": file_name
        }


def create_customer_policy_structure(graph, file_name: str, customers, policies, 
                                   policy_numbers, policy_years, policy_types, 
                                   coverages, agents, assets) -> int:
    """
    Müşteri merkezli poliçe yapısını oluşturur.
    
    YENİ YAPISAL MODEL:
    Customer -[:OWNS]-> Policy -[:FOR_YEAR]-> PolicyYear
                        |
                        +-[:OF_TYPE]-> PolicyType
                        +-[:HAS_COVERAGE]-> Coverage  
                        +-[:DOCUMENTED_IN]-> Document
                        +-[:HANDLED_BY]-> Agent
                        +-[:COVERS]-> Asset
    
    Returns:
        Oluşturulan yapı sayısı
    """
    structures_created = 0
    
    try:
        # Her müşteri için yapı oluştur
        for customer in customers:
            customer_id = customer['entity_id']
            
            # Müşteriye ait poliçeleri bul ve bağla
            for policy in policies:
                policy_id = policy['entity_id']
                
                # Customer -[:OWNS]-> Policy ilişkisi (yeni model)
                customer_policy_query = """
                MATCH (c:__Entity__:Policyholder {id: $customer_id})
                MATCH (p:__Entity__:Policy {id: $policy_id})
                MERGE (c)-[r:OWNS]->(p)
                SET r.created_at = datetime()
                SET r.source_file = $filename
                RETURN COUNT(r) as links_created
                """
                
                result = graph.query(customer_policy_query, params={
                    "customer_id": customer_id,
                    "policy_id": policy_id,
                    "filename": file_name
                })
                
                if result and result[0]['links_created'] > 0:
                    structures_created += 1
                    
                    # Policy ile diğer entity'leri bağla (yeni yapıya göre)
                    link_policy_details(graph, file_name, policy_id, 
                                      policy_numbers, policy_years, 
                                      policy_types, coverages, agents, assets)
        
        return structures_created
        
    except Exception as e:
        logging.error(f"Customer-Policy yapı oluşturma hatası: {e}")
        return 0


def link_policy_details(graph, file_name: str, policy_id: str, 
                       policy_numbers, policy_years, policy_types, 
                       coverages, agents, assets):
    """
    Policy node'unu diğer detay entity'ler ile bağlar.
    
    YENİ YAPISAL İLİŞKİLER:
    Policy -[:FOR_YEAR]-> PolicyYear
    Policy -[:OF_TYPE]-> PolicyType  
    Policy -[:HAS_COVERAGE]-> Coverage
    Policy -[:HANDLED_BY]-> Agent
    Policy -[:COVERS]-> Asset
    Policy -[:DOCUMENTED_IN]-> Document
    """
    try:
        # Policy -[:FOR_YEAR]-> PolicyYear (yeni yapı)
        for py in policy_years:
            link_query = """
            MATCH (p:__Entity__:Policy {id: $policy_id})
            MATCH (py:__Entity__:PolicyYear {id: $detail_id})
            MERGE (p)-[r:FOR_YEAR]->(py)
            SET r.created_at = datetime()
            SET r.source_file = $filename
            """
            graph.query(link_query, params={
                "policy_id": policy_id,
                "detail_id": py['entity_id'],
                "filename": file_name
            })
        
        # Policy -[:OF_TYPE]-> PolicyType (yeni yapı) 
        for pt in policy_types:
            link_query = """
            MATCH (p:__Entity__:Policy {id: $policy_id})
            MATCH (pt:__Entity__:PolicyType {id: $detail_id})
            MERGE (p)-[r:OF_TYPE]->(pt)
            SET r.created_at = datetime()
            SET r.source_file = $filename
            """
            graph.query(link_query, params={
                "policy_id": policy_id,
                "detail_id": pt['entity_id'],
                "filename": file_name
            })
        
        # Policy -[:HAS_COVERAGE]-> Coverage
        for cov in coverages:
            link_query = """
            MATCH (p:__Entity__:Policy {id: $policy_id})
            MATCH (c:__Entity__:Coverage {id: $detail_id})
            MERGE (p)-[r:HAS_COVERAGE]->(c)
            SET r.created_at = datetime()
            SET r.source_file = $filename
            """
            graph.query(link_query, params={
                "policy_id": policy_id,
                "detail_id": cov['entity_id'],
                "filename": file_name
            })
        
        # Policy -[:HANDLED_BY]-> Agent
        for agent in agents:
            link_query = """
            MATCH (p:__Entity__:Policy {id: $policy_id})
            MATCH (a:__Entity__:Agent {id: $detail_id})
            MERGE (p)-[r:HANDLED_BY]->(a)
            SET r.created_at = datetime()
            SET r.source_file = $filename
            """
            graph.query(link_query, params={
                "policy_id": policy_id,
                "detail_id": agent['entity_id'],
                "filename": file_name
            })
        
        # Policy -[:COVERS]-> Asset (yeni: Building, Risk vb.)
        for asset in assets:
            link_query = """
            MATCH (p:__Entity__:Policy {id: $policy_id})
            MATCH (a:__Entity__ {id: $detail_id})
            WHERE 'Building' IN labels(a) OR 'Risk' IN labels(a)
            MERGE (p)-[r:COVERS]->(a)
            SET r.created_at = datetime()
            SET r.source_file = $filename
            """
            graph.query(link_query, params={
                "policy_id": policy_id,
                "detail_id": asset['entity_id'],
                "filename": file_name
            })
            
    except Exception as e:
        logging.error(f"Policy detay bağlama hatası: {e}")


def create_document_links(graph, file_name: str) -> int:
    """
    Policy node'larını Document ile bağlar.
    Policy -[:DOCUMENTED_IN]-> Document ilişkisi oluşturur.
    """
    try:
        document_link_query = """
        MATCH (d:Document {fileName: $filename})
        MATCH (d)<-[:PART_OF]-(c:Chunk)-[:HAS_ENTITY]->(p:__Entity__:Policy)
        MERGE (p)-[r:DOCUMENTED_IN]->(d)
        SET r.created_at = datetime()
        SET r.source_file = $filename
        RETURN COUNT(DISTINCT r) as links_created
        """
        
        result = graph.query(document_link_query, params={"filename": file_name})
        return result[0]['links_created'] if result else 0
        
    except Exception as e:
        logging.error(f"Document bağlama hatası: {e}")
        return 0


def verify_policy_graph_structure(graph, file_name: str) -> Dict[str, Any]:
    """
    Dosya için oluşturulan müşteri merkezli policy graph yapısını doğrular.
    
    Args:
        graph: Neo4j graph connection
        file_name: Kontrol edilecek dosya adı
    
    Returns:
        Doğrulama sonuç raporu
    """
    try:
        logging.info(f"Policy graph structure doğrulanıyor: {file_name}")
        
        # Müşteri-Policy bağlantılarını kontrol et
        customer_policy_query = """
        MATCH (d:Document {fileName: $filename})
        MATCH (d)<-[:PART_OF]-(c:Chunk)-[:HAS_ENTITY]->(customer:__Entity__:Policyholder)
        MATCH (customer)-[r:OWNS_POLICY]->(policy:__Entity__:Policy)
        RETURN COUNT(DISTINCT r) as customer_policy_links,
               COUNT(DISTINCT customer) as customers,
               COUNT(DISTINCT policy) as policies
        """
        
        customer_result = graph.query(customer_policy_query, params={"filename": file_name})
        
        # Policy-Document bağlantılarını kontrol et
        policy_document_query = """
        MATCH (d:Document {fileName: $filename})
        MATCH (policy:__Entity__:Policy)-[r:DOCUMENTED_IN]->(d)
        RETURN COUNT(DISTINCT r) as policy_document_links,
               COLLECT(DISTINCT policy.id) as policy_ids
        """
        
        document_result = graph.query(policy_document_query, params={"filename": file_name})
        
        # Policy detay bağlantılarını kontrol et
        policy_details_query = """
        MATCH (d:Document {fileName: $filename})
        MATCH (d)<-[:PART_OF]-(c:Chunk)-[:HAS_ENTITY]->(policy:__Entity__:Policy)
        OPTIONAL MATCH (policy)-[:HAS_NUMBER]->(pn:PolicyNumber)
        OPTIONAL MATCH (policy)-[:FOR_YEAR]->(py:PolicyYear)
        OPTIONAL MATCH (policy)-[:OF_TYPE]->(pt:PolicyType)
        OPTIONAL MATCH (policy)-[:HAS_COVERAGE]->(cov:Coverage)
        OPTIONAL MATCH (policy)-[:HANDLED_BY]->(agent:Agent)
        RETURN COUNT(DISTINCT policy) as total_policies,
               COUNT(DISTINCT pn) as policy_numbers,
               COUNT(DISTINCT py) as policy_years,
               COUNT(DISTINCT pt) as policy_types,
               COUNT(DISTINCT cov) as coverages,
               COUNT(DISTINCT agent) as agents
        """
        
        details_result = graph.query(policy_details_query, params={"filename": file_name})
        
        if customer_result and document_result and details_result:
            customer_data = customer_result[0]
            document_data = document_result[0]
            details_data = details_result[0]
            
            logging.info(f"📊 {file_name} Policy Graph Structure:")
            logging.info(f"  👥 Müşteriler: {customer_data['customers']}")
            logging.info(f"  📋 Poliçeler: {customer_data['policies']}")
            logging.info(f"  🔗 Müşteri-Policy bağlantıları: {customer_data['customer_policy_links']}")
            logging.info(f"  📄 Policy-Document bağlantıları: {document_data['policy_document_links']}")
            logging.info(f"  🔢 Policy Numbers: {details_data['policy_numbers']}")
            logging.info(f"  📅 Policy Years: {details_data['policy_years']}")
            logging.info(f"  🏷️ Policy Types: {details_data['policy_types']}")
            logging.info(f"  🛡️ Coverages: {details_data['coverages']}")
            logging.info(f"  👨‍💼 Agents: {details_data['agents']}")
            
            return {
                "status": "verified",
                "file_name": file_name,
                "customers": customer_data['customers'],
                "policies": customer_data['policies'],
                "customer_policy_links": customer_data['customer_policy_links'],
                "policy_document_links": document_data['policy_document_links'],
                "policy_details": {
                    "policy_numbers": details_data['policy_numbers'],
                    "policy_years": details_data['policy_years'],
                    "policy_types": details_data['policy_types'],
                    "coverages": details_data['coverages'],
                    "agents": details_data['agents']
                }
            }
        else:
            logging.warning(f"⚠️ {file_name} için policy graph structure bulunamadı")
            return {
                "status": "no_structure_found",
                "file_name": file_name
            }
            
    except Exception as e:
        logging.error(f"Policy graph structure verification hatası: {e}")
        return {
            "status": "error",
            "error": str(e),
            "file_name": file_name
        }


def get_customer_policy_summary(graph, customer_name: str = None) -> Dict[str, Any]:
    """
    Müşterilerin poliçe özetini getirir.
    
    Args:
        graph: Neo4j graph connection
        customer_name: Belirli bir müşteri (opsiyonel)
    
    Returns:
        Müşteri-poliçe özet raporu
    """
    try:
        if customer_name:
            # Belirli müşteri için özet
            query = """
            MATCH (customer:__Entity__:Policyholder {id: $customer_name})
            MATCH (customer)-[:OWNS_POLICY]->(policy:__Entity__:Policy)
            OPTIONAL MATCH (policy)-[:FOR_YEAR]->(year:PolicyYear)
            OPTIONAL MATCH (policy)-[:OF_TYPE]->(type:PolicyType)
            OPTIONAL MATCH (policy)-[:DOCUMENTED_IN]->(doc:Document)
            RETURN customer.id as customer_name,
                   COUNT(DISTINCT policy) as total_policies,
                   COLLECT(DISTINCT year.id) as years,
                   COLLECT(DISTINCT type.id) as policy_types,
                   COLLECT(DISTINCT doc.fileName) as documents
            """
            
            result = graph.query(query, params={"customer_name": customer_name})
        else:
            # Tüm müşteriler için özet
            query = """
            MATCH (customer:__Entity__:Policyholder)
            MATCH (customer)-[:OWNS_POLICY]->(policy:__Entity__:Policy)
            OPTIONAL MATCH (policy)-[:FOR_YEAR]->(year:PolicyYear)
            OPTIONAL MATCH (policy)-[:OF_TYPE]->(type:PolicyType)
            RETURN customer.id as customer_name,
                   COUNT(DISTINCT policy) as total_policies,
                   COLLECT(DISTINCT year.id) as years,
                   COLLECT(DISTINCT type.id) as policy_types
            ORDER BY total_policies DESC
            """
            
            result = graph.query(query)
        
        return {
            "status": "success",
            "customer_summaries": result
        }
        
    except Exception as e:
        logging.error(f"Customer policy summary hatası: {e}")
        return {
            "status": "error",
            "error": str(e)
        }
