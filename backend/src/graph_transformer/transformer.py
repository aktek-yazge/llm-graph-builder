import asyncio
import json
import logging
import time
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple, Type, Union, cast
import time
import logging
import asyncio
import json

from langchain_community.graphs.graph_document import GraphDocument, Node, Relationship
from langchain_core.documents import Document
from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import SystemMessage
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import (
    ChatPromptTemplate,
    HumanMessagePromptTemplate,
    PromptTemplate,
)
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field, create_model

DEFAULT_NODE_TYPE = "Node"
SST_NODE_TYPE = "Statement"

def log_llm_output_incremental(raw_output, chunk_id=None, timestamp=None, document_filename=None, enable_logging=True):
    """
    LLM'in çıkardığı tüm node ve relationship'leri incremental olarak JSON dosyasına yazar
    
    Args:
        raw_output: LLM'den dönen raw çıktı
        chunk_id: Chunk ID'si
        timestamp: Zaman damgası
        document_filename: Belge dosya adı (log'da isim olarak kullanılır)
        enable_logging: Log'u açıp kapatmak için (True/False)
    """
    # Eğer logging kapalıysa hiçbir şey yapma
    if not enable_logging:
        return
        
    try:
        # Log dosyası yolu
        log_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'logs')
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, document_filename or 'llm_extractions.jsonl')
        
        # Timestamp oluştur
        if not timestamp:
            timestamp = datetime.now().isoformat()
        
        # Raw output'u parse et
        parsed_data = None
        if hasattr(raw_output, 'content'):
            content = raw_output.content
        else:
            content = str(raw_output)
        
        # DEBUG: raw_output'u JSON olarak logla
        try:
            if hasattr(raw_output, '__dict__'):
                raw_output_json = {
                    'type': str(type(raw_output)),
                    'content': content,
                    'attributes': {k: str(v) for k, v in raw_output.__dict__.items()}
                }
                print(f"🔍 DEBUG - raw_output as JSON: {json.dumps(raw_output_json, indent=2, ensure_ascii=False)}")
            else:
                print(f"🔍 DEBUG - raw_output (no __dict__): {raw_output}")
                print(f"🔍 DEBUG - raw_output type: {type(raw_output)}")
                print(f"🔍 DEBUG - raw_output content: {content}")
        except Exception as debug_e:
            print(f"🔍 DEBUG - raw_output debug error: {debug_e}")
        
        try:
            # JSON parse et - backtick'li JSON'u temizle
            clean_content = content
            if isinstance(content, str):
                # ```json ve ``` backtick'lerini temizle
                clean_content = content.strip()
                if clean_content.startswith('```json'):
                    clean_content = clean_content[7:]  # '```json' kaldır
                if clean_content.startswith('```'):
                    clean_content = clean_content[3:]  # '```' kaldır
                if clean_content.endswith('```'):
                    clean_content = clean_content[:-3]  # Son '```' kaldır
                clean_content = clean_content.strip()
            
            if isinstance(clean_content, str) and clean_content.startswith('['):
                parsed_data = json.loads(clean_content)
            elif isinstance(clean_content, str) and clean_content.startswith('{'):
                parsed_data = [json.loads(clean_content)]
            else:
                parsed_data = clean_content
        except json.JSONDecodeError as e:
            # JSON değilse raw olarak kaydet
            parsed_data = {"raw_content": content, "parse_error": True, "error_message": str(e)}
        
        # Log entry oluştur
        log_entry = {
            "timestamp": timestamp,
            "document_name": document_filename or "unknown_document",
            "chunk_id": chunk_id,
            "raw_output_type": str(type(raw_output)),
            "content_length": len(content),
            "parsed_data": parsed_data,
            "extraction_count": len(parsed_data) if isinstance(parsed_data, list) else 1
        }
        
        # Node ve relationship sayılarını çıkar
        if isinstance(parsed_data, list):
            nodes = set()
            relationships = []
            for item in parsed_data:
                if isinstance(item, dict):
                    if 'head' in item and 'tail' in item:
                        nodes.add((item.get('head'), item.get('head_type')))
                        nodes.add((item.get('tail'), item.get('tail_type')))
                        relationships.append({
                            'relation': item.get('relation'),
                            'head': item.get('head'),
                            'head_type': item.get('head_type'),
                            'tail': item.get('tail'),
                            'tail_type': item.get('tail_type')
                        })
            
            log_entry["unique_nodes"] = list(nodes)
            log_entry["relationships"] = relationships
            log_entry["node_count"] = len(nodes)
            log_entry["relationship_count"] = len(relationships)
        
        # JSON dosyasına yaz (append mode)
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
        
        doc_name = document_filename or "unknown_document"
        print(f"🟢 LLM extraction logged for [{doc_name}]: {log_file}")
        print(f"📊 Nodes: {log_entry.get('node_count', 'N/A')}, Relationships: {log_entry.get('relationship_count', 'N/A')}")
        
    except Exception as e:
        print(f"❌ Incremental logging error: {e}")
        logging.error(f"Incremental logging error: {e}")

def get_existing_context_for_prompt(graph_instance, chunk_id):
    """
    Chunk'a ait mevcut node'ları ID'leriyle birlikte bul
    LLM'e bu context'i ver ki doğru bağlantıları kursun
    """
    if not graph_instance or not chunk_id:
        return ""
    
    context_query = """
    MATCH (c:Chunk {id: $chunk_id})-[:PART_OF]->(d:Document)
    
    // Belge ile ilişkili tüm node'ları bul
    OPTIONAL MATCH (policy:Policy)-[:DOCUMENTED_IN]->(d)
    OPTIONAL MATCH (customer:Customer)-[:HAS_DOC]->(d)
    OPTIONAL MATCH (policy)-[:HAS_TYPE]->(policyType:PolicyType)
    OPTIONAL MATCH (policy)-[:HAS_INSURED_ITEM]->(insuredItem:InsuredItem)
    OPTIONAL MATCH (policy)-[:HAS_YEAR]->(policyYear:PolicyYear)
    
    // Belgeye bağlı diğer entity'leri de bul
    OPTIONAL MATCH (d)<-[:DOCUMENTED_IN]-(otherPolicy:Policy)
    OPTIONAL MATCH (otherPolicy)-[:HAS_TYPE]->(otherPolicyType:PolicyType)
    OPTIONAL MATCH (otherPolicy)-[:HAS_INSURED_ITEM]->(otherInsuredItem:InsuredItem)
    
    RETURN DISTINCT
        collect(DISTINCT {type: 'Policy', id: policy.id, name: policy.name}) as policies,
        collect(DISTINCT {type: 'Customer', id: customer.id, name: customer.name}) as customers,
        collect(DISTINCT {type: 'PolicyType', id: policyType.id, name: policyType.name}) as policyTypes,
        collect(DISTINCT {type: 'InsuredItem', id: insuredItem.id, name: insuredItem.name}) as insuredItems,
        collect(DISTINCT {type: 'PolicyYear', id: policyYear.id, name: policyYear.year}) as policyYears
    """
    
    try:
        result = graph_instance.query(context_query, params={"chunk_id": chunk_id})
        if result and result[0]:
            data = result[0]
            context_parts = []
            
            context_parts.append("# MEVCUT SİSTEM NODE'LARI (Bu exact ID'leri kullan):")
            
            # Policy node'ları
            policies = [p for p in data.get('policies', []) if p.get('id')]
            if policies:
                context_parts.append("\n## MEVCUT POLICY NODE'LARI:")
                for policy in policies:
                    context_parts.append(f"- Policy(id: '{policy['id']}', name: '{policy.get('name', '')}') - Bu ID'yi kullan!")
            
            # Customer node'ları  
            customers = [c for c in data.get('customers', []) if c.get('id')]
            if customers:
                context_parts.append("\n## MEVCUT CUSTOMER NODE'LARI:")
                for customer in customers:
                    context_parts.append(f"- Customer(id: '{customer['id']}', name: '{customer.get('name', '')}') - Bu ID'yi kullan!")
            
            # PolicyType node'ları
            policyTypes = [pt for pt in data.get('policyTypes', []) if pt.get('id')]
            if policyTypes:
                context_parts.append("\n## MEVCUT POLICYTYPE NODE'LARI:")
                for policyType in policyTypes:
                    context_parts.append(f"- PolicyType(id: '{policyType['id']}', name: '{policyType.get('name', '')}') - Bu ID'yi kullan!")
            
            # InsuredItem node'ları
            insuredItems = [ii for ii in data.get('insuredItems', []) if ii.get('id')]
            if insuredItems:
                context_parts.append("\n## MEVCUT INSUREDITEM NODE'LARI:")
                for insuredItem in insuredItems:
                    context_parts.append(f"- InsuredItem(id: '{insuredItem['id']}', name: '{insuredItem.get('name', '')}') - Bu ID'yi kullan!")
            
            # PolicyYear node'ları
            policyYears = [py for py in data.get('policyYears', []) if py.get('id')]
            if policyYears:
                context_parts.append("\n## MEVCUT POLICYYEAR NODE'LARI:")
                for policyYear in policyYears:
                    context_parts.append(f"- PolicyYear(id: '{policyYear['id']}', year: '{policyYear.get('name', '')}') - Bu ID'yi kullan!")
            
            context_parts.append("\n# BAĞLANTI KURALLARI:")
            context_parts.append("- YUKARDAKI NODE'LARI TEKRAR OLUŞTURMA! Sadece exact ID'lerini kullan")
            context_parts.append("- Yeni entity'leri mevcut node'lara HAS_ENTITY ile bağla")
            context_parts.append("- Örnek: Yeni 'Adres' entity'si -> mevcut Customer ID'sine bağla")
            context_parts.append("- Örnek: Yeni 'Acente' entity'si -> mevcut Policy ID'sine bağla")
            context_parts.append("- CRITICAL: Mevcut node'ların exact ID'lerini kullan, yeni node yaratma!")
            
            return "\n".join(context_parts) + "\n\n"
    except Exception as e:
        import logging
        logging.warning(f"Context alınamadı: {e}")
    
    return ""

def get_db_schema_for_prompt(graph_instance=None):
    """
    Veritabanından node ve relationship tiplerini çeker ve prompt için hazırlar.
    Document ve Policy haricindeki tipleri döndürür.
    """
    try:
        # intelligent_agent.py'deki get_neo4j_schema fonksiyonunu kullan
        print("🔄 Neo4j'den schema çekiliyor...")
        
        if graph_instance is None:
            # Graph instance yoksa, doğrudan Neo4j sorgusu yap
            from neo4j import GraphDatabase
            import os
            
            # Neo4j bağlantı bilgilerini al
            uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
            username = os.getenv("NEO4J_USERNAME", "neo4j")
            password = os.getenv("NEO4J_PASSWORD", "password")
            
            driver = GraphDatabase.driver(uri, auth=(username, password))
            
            try:
                with driver.session() as session:
                    # Node labels çek
                    node_labels_result = session.run("CALL db.labels()")
                    node_labels = [record["label"] for record in node_labels_result]
                    
                    # Relationship types çek
                    rel_types_result = session.run("CALL db.relationshipTypes()")
                    relationship_types = [record["relationshipType"] for record in rel_types_result]
                    
            finally:
                driver.close()
                
        else:
            # Graph instance varsa intelligent_agent'taki fonksiyonu kullan
            from intelligent_agent import IntelligentAgent
            if hasattr(graph_instance, 'get_neo4j_schema'):
                schema_data = graph_instance.get_neo4j_schema()
                node_labels = schema_data.get("node_labels", [])
                relationship_types = schema_data.get("relationship_types", [])
            else:
                # Fallback: Doğrudan sorgu
                node_labels_result = graph_instance.query("CALL db.labels()")
                node_labels = [record["label"] for record in node_labels_result]
                
                rel_types_result = graph_instance.query("CALL db.relationshipTypes()")
                relationship_types = [record["relationshipType"] for record in rel_types_result]
        
        # Node tiplerini filtrele (Document, Policy, __Entity__, Chunk, Session, Message ve _ ile başlayanlar hariç)
        db_node_types = []
        for node_name in node_labels:
            if (node_name not in ["Document", "Policy", "__Entity__", "Chunk", "Session", "Message"] and
                not node_name.startswith("_")):
                db_node_types.append(node_name)
        
        # Relationship tiplerini filtrele (sistem internal'ları hariç)
        db_relationship_types = []
        for rel_name in relationship_types:
            if rel_name not in ["PART_OF", "NEXT_CHUNK", "FIRST_CHUNK", "NEXT", "LAST_MESSAGE", "EXTRACTED_FROM"]:
                db_relationship_types.append(rel_name)
        
        print(f"🗄️ DB'den çekilen node tipleri ({len(db_node_types)}): {db_node_types}")
        print(f"🔗 DB'den çekilen relationship tipleri ({len(db_relationship_types)}): {db_relationship_types}")
        
        return db_node_types, db_relationship_types
        
    except Exception as e:
        logging.warning(f"DB schema çekilemedi, varsayılan değerler kullanılıyor: {e}")
        # Hata durumunda varsayılan değerler
        return [
            "Customer", "Company", "Person", "Organization", "PolicyType", 
            "InsuredItem", "Location", "Date"
        ], ["HAS_POLICY", "HAS_ENTITY", "HAS_TYPE", "HAS_INSURED_ITEM"]

def create_turkish_insurance_prompt(allowed_nodes=None, allowed_relationships=None):
    """
    Türkçe sigorta prompt'unu oluşturur - allowed types ile entegre
    """
    base_prompt = (
        "# Entity Extraction ve Knowledge Graph Oluşturma\n"
        "## 1. Ana Görev\n"
        "Metindeki TÜM anlamlı entity'leri çıkarın ve knowledge graph oluşturun.\n"
        "En spesifik ve doğru node tiplerini kullanın.\n"
        
        "## 2. MEVCUT SİSTEM NODE'LARI (KRİTİK)\n"
        "- MEVCUT SİSTEM NODE'LARI bölümünde listelenen node'ları ASLA tekrar oluşturmayın\n"
        "- Bu node'lar zaten sistemde mevcut - sadece exact ID'lerini kullanın\n"
        "- Örnek: 'Policy(id: POL123)' görürsen, yeni Policy yaratma! 'POL123' ID'sini kullan\n"
        "- Örnek: 'Customer(id: CUST456)' görürsen, yeni Customer yaratma! 'CUST456' ID'sini kullan\n"
        "- CRITICAL: Mevcut node'ların exact ID'lerini kullan, yeni node yaratma!\n"

        "## 3. Düğüm Etiketleme\n"
        "- **Tutarlılık**: Mevcut türleri düğüm etiketleri için kullanın.\n"
        "Temel veya başlangıç düzeyindeki türleri kullanın.\n"
        "- Örneğin, poliçede taraflardan müşteri olan kişiyi temsil eden varlık tespit ettiğinizde, "
        "her zaman **'Customer'** olarak etiketleyin. 'Kişi' veya 'Person' gibi "
        "genel terimler kullanmayın."
        "- **Düğüm ID'leri**: Asla tamsayı kullanmayın. Düğüm ID'leri "
        "metinde bulunan isimler veya okunabilir tanımlayıcılar olmalıdır.\n"
        "- **İlişkiler** varlıklar veya kavramlar arasındaki bağlantıları temsil eder.\n"
        "Bilgi grafiği oluştururken ilişki türlerinde tutarlılık ve genellik sağlayın.\n"
    )
    
    # Allowed nodes kısmını ekle - çok esnek yaklaşım
    if allowed_nodes:
        nodes_str = ", ".join(allowed_nodes)
        base_prompt += f"\n## 4. NODE TİPLERİ (ÇOK ESNEKLİK - YENİ TİPLER ÖNEMSENEN)\n"
        base_prompt += f"**DOĞRULUK UYUM ÜZERİNDE**: Her zaman en doğru tipi seçin\n"
        base_prompt += f"**Öncelikle bu node tiplerini kullanmayı gözden geçir: {nodes_str} Eğer uygun değilse yeni tipler yaratabilirsin.\n"
    
    # Allowed relationships kısmını ekle - katı
    if allowed_relationships:
        if isinstance(allowed_relationships[0], tuple):
            rels_str = ", ".join([f"({s}, {r}, {t})" for s, r, t in allowed_relationships])
        else:
            rels_str = ", ".join(allowed_relationships)
        base_prompt += f"\n## 5. İZİN VERİLEN İLİŞKİ TİPLERİ (KATI ZORUNLU)\n"
        base_prompt += f"SADECE şu relationship tiplerini kullanın: {rels_str}\n"
        base_prompt += f"Bu listede olmayan hiçbir relationship tipi kullanmayın!\n"
        base_prompt += f"MANTIK KONTROLÜ: Eğer allowed relationship'ler entity'ler için mantıklı değilse, daha az ilişki çıkarın veya ilişki çıkarmayın!\n"
    

    
    return base_prompt

examples = [
    {
        "text": (
            "Ayça Dinçkök Galata Residans D4 dairesinde oturmaktadır "
            "ve 2020 yılı konut poliçesi sahibidir"
        ),
        "head": "Ayça Dinçkök",
        "head_type": "Customer",
        "relation": "HAS_POLICY",
        "tail": "Konut Poliçesi 2020",
        "tail_type": "PolicyType",
    },
    {
        "text": (
            "Ayça Dinçkök Galata Residans D4 dairesinde oturmaktadır "
            "ve 2020 yılı konut poliçesi sahibidir"
        ),
        "head": "Ayça Dinçkök",
        "head_type": "Customer",
        "relation": "HAS_ENTITY",
        "tail": "Galata Residans D4",
        "tail_type": "Location",
    },
    {
        "text": (
            "Doga Sigorta A.Ş. tarafından düzenlenen DASK poliçesi "
            "Ankara Çiftçi Apartmanı için geçerlidir"
        ),
        "head": "Doga Sigorta A.Ş.",
        "head_type": "Company",
        "relation": "HAS_ENTITY",
        "tail": "DASK Poliçesi",
        "tail_type": "PolicyType",
    },
]

system_prompt = (
    "# Knowledge Graph Instructions for GPT-4\n"
    "## 1. Overview\n"
    "You are a top-tier algorithm designed for extracting information in structured "
    "formats to build a knowledge graph.\n"
    "CREATIVITY ENCOURAGED: Extract ALL meaningful entities and relationships from the text. "
    "Create specific and descriptive node types for each entity.\n"
    "Extract all important and relevant information that provides value. "
    "Prioritize accuracy and completeness. LIMIT: Extract maximum 3 relationships per document chunk. "
    "Do not add any information that is not explicitly "
    "mentioned in the text.\n"
    "- **Nodes** represent entities and concepts.\n"
    "- The aim is to achieve accuracy and completeness in the knowledge graph, making it\n"
    "comprehensive and detailed.\n"
    "## 2. Labeling Nodes\n"
    "- **Specificity**: Create specific and descriptive types for node labels.\n"
    "Use specific and descriptive types for node labels.\n"
    "- For example, when you identify an entity representing a doctor, "
    "always label it as **'Doctor'**. Use specific terms "
    "like 'University', 'Hospital', or 'Award'."
    "- **Node IDs**: Never utilize integers as node IDs. Node IDs should be "
    "names or human-readable identifiers found in the text.\n"
    "- **Relationships** represent connections between entities or concepts.\n"
    "Ensure consistency and generality in relationship types when constructing "
    "knowledge graphs. Instead of using specific and momentary types "
    "such as 'BECAME_PROFESSOR', use more general and timeless relationship types "
    "like 'PROFESSOR'. Make sure to use general and timeless relationship types!\n"
    "## 3. Coreference Resolution\n"
    "- **Maintain Entity Consistency**: When extracting entities, it's vital to "
    "ensure consistency.\n"
    'If an entity, such as "John Doe", is mentioned multiple times in the text '
    'but is referred to by different names or pronouns (e.g., "Joe", "he"),'
    "always use the most complete identifier for that entity throughout the "
    'knowledge graph. In this example, use "John Doe" as the entity ID.\n'
    "Remember, the knowledge graph should be coherent and easily understandable, "
    "so maintaining consistency in entity references is crucial.\n"
    "## 4. Entity Extraction Guidelines\n"
    "Extract ALL meaningful entities and relationships from the text:\n"
    "- People, Organizations, Locations, Objects, Concepts\n"
    "- Use specific and descriptive node types for each entity\n"
    "- Create comprehensive knowledge graphs\n"
    "- Focus on accuracy and completeness\n"
    "LIMIT: Extract MAXIMUM 3 relationships per document chunk.\n"
    "## 5. Quality Focus\n"
    "Prioritize accuracy and meaningful connections over quantity."
)


def get_default_prompt(
    additional_instructions: str = "",
    node_labels: Optional[List[str]] = None,
    rel_types: Optional[Union[List[str], List[Tuple[str, str, str]]]] = None,
    relationship_type: Optional[str] = None,
) -> ChatPromptTemplate:
    # Build dynamic system prompt with constraints
    constraints_parts = []
    
    if node_labels:
        constraints_parts.append(
            f"## 5. Node Type Guidelines (VERY FLEXIBLE - CREATIVITY ENCOURAGED)\n"
            f"- **CREATIVITY FIRST**: Create NEW node types that best describe each entity\n"
            f"- **ACCURACY OVER CONFORMITY**: Always choose the most accurate type for each entity\n"
            f"- **PREFERRED BUT NOT MANDATORY**: You may use these types when appropriate: {node_labels}\n"
            f"- **ENCOURAGE NEW TYPES**: Create specific types like 'Doctor', 'University', 'Hospital', 'Book', 'Award', etc.\n"
            f"- **EXAMPLES**: \n"
            f"  - Doctor → use 'Doctor' (not 'Customer')\n"
            f"  - University → use 'University' (not 'Policy')\n"
            f"  - Hospital → use 'Hospital' (not 'Customer')\n"
            f"- **PRINCIPLE**: The most descriptive and accurate type is always the best choice\n"
            f"- **PREFERRED BUT OPTIONAL**: {node_labels}"
        )
    
    if rel_types:
        if relationship_type == "tuple":
            rel_types_str = list({item[1] for item in rel_types})
            constraints_parts.append(
                f"## 6. STRICT Relationship Type Constraints (MANDATORY)\n"
                f"- **MANDATORY**: You MUST ONLY use these exact relationship types: {rel_types_str}\n"
                f"- **FORBIDDEN**: Do NOT create relationships with any other types\n"
                f"- **STRICT**: Relationships are strictly enforced - no exceptions\n"
                f"- **SCHEMA**: All relationships must follow this schema: {rel_types}\n"
                f"- **FORMAT**: (SourceNodeType, RELATIONSHIP_TYPE, TargetNodeType)\n"
                f"- **VALIDATION**: Only extract relationships that match the schema exactly\n"
                f"- **ALLOWED RELATIONSHIP TYPES**: {rel_types_str}\n"
                f"- **ALLOWED RELATIONSHIP SCHEMA**: {rel_types}"
            )
        else:
            constraints_parts.append(
                f"## 6. STRICT Relationship Type Constraints (MANDATORY)\n"
                f"- **MANDATORY**: You MUST ONLY use these exact relationship types: {rel_types}\n"
                f"- **FORBIDDEN**: Do NOT create relationships with any other types\n"
                f"- **LOGIC CHECK**: If allowed relationship types don't make logical sense for the entities, extract fewer relationships or skip relationships\n"
                f"- **STRICT**: Relationships are strictly enforced - no exceptions\n"
                f"- **VALIDATION**: Every relationship must have one of these types: {', '.join(rel_types)}\n"
                f"- **REJECTION**: Ignore any relationships that don't fit these types\n"
                f"- **ALLOWED RELATIONSHIP TYPES**: {rel_types}"
            )
    
    # Combine system prompt with constraints
    enhanced_system_prompt = system_prompt
    if constraints_parts:
        enhanced_system_prompt += "\n" + "\n".join(constraints_parts)
        enhanced_system_prompt += (
            "\n\n## EXTRACTION INSTRUCTION:\n"
            "LIMIT: Extract MAXIMUM 3 relationships per document chunk. "
            "Extract ALL meaningful entities from the text using the most specific and accurate node types. "
            "Create new node types as needed for accuracy (not limited to allowed list). "
            "For relationships, use ONLY the specified relationship types (strict requirement). "
            "Focus on extracting comprehensive and accurate entities. Choose the 3 most meaningful relationships. "
            "Prioritize accuracy and completeness in entity extraction."
        )
    
    return ChatPromptTemplate.from_messages(
        [
            ("system", enhanced_system_prompt),
            (
                "human",
                additional_instructions
                + " IMPORTANT: Extract ALL meaningful entities using specific and accurate node types. "
                + "Create new node types as needed for accuracy (not limited to allowed list). "
                + "For relationships, use ONLY the specified relationship types (strict requirement). "
                + "LIMIT: Extract maximum 3 relationships per document chunk. "
                + "Prioritize accuracy and completeness in entity extraction. "
                + "Tip: Make sure to answer in the correct format and do "
                "not include any explanations. "
                "Use the given format to extract information from the "
                "following input: {input}",
            ),
        ]
    )


def _get_additional_info(input_type: str) -> str:
    # Check if the input_type is one of the allowed values
    if input_type not in ["node", "relationship", "property"]:
        raise ValueError("input_type must be 'node', 'relationship', or 'property'")

    # Perform actions based on the input_type
    if input_type == "node":
        return (
            "Use specific and descriptive types for node labels.\n"
            "For example, when you identify an entity representing a doctor, "
            "always label it as **'Doctor'**. Use specific terms "
            "like 'VehiclePlate', 'Company', 'University', 'Hospital' instead of generic types."
        )
    elif input_type == "relationship":
        return (
            "Instead of using specific and momentary types such as "
            "'BECAME_PROFESSOR', use more general and timeless relationship types "
            "like 'PROFESSOR'. However, do not sacrifice any accuracy for generality"
        )
    elif input_type == "property":
        return ""
    return ""


def optional_enum_field(
    enum_values: Optional[Union[List[str], List[Tuple[str, str, str]]]] = None,
    description: str = "",
    input_type: str = "node",
    llm_type: Optional[str] = None,
    relationship_type: Optional[str] = None,
    **field_kwargs: Any,
) -> Any:
    """Utility function to conditionally create a field with an enum constraint."""
    parsed_enum_values = enum_values
    # We have to extract enum types from tuples
    if relationship_type == "tuple":
        parsed_enum_values = list({el[1] for el in enum_values})  # type: ignore

    # Only openai supports enum param - but we want flexibility, so don't use strict enum
    if enum_values and llm_type == "openai-chat" and input_type == "relationship":
        # Keep strict enum only for relationships
        return Field(
            ...,
            enum=parsed_enum_values,  # type: ignore[call-arg]
            description=f"{description}. Available options are {parsed_enum_values}",
            **field_kwargs,
        )  # type: ignore[call-overload]
    elif enum_values and llm_type == "openai-chat" and input_type == "node":
        # For nodes, be flexible - no strict enum
        return Field(
            ...,
            description=f"{description}. Preferred options (use if suitable): {parsed_enum_values}. Create new specific types if none fit the entity.",
            **field_kwargs,
        )
    elif enum_values:
        return Field(
            ...,
            description=f"{description}. Preferred options (use if suitable): {parsed_enum_values}. Create new specific types if none fit the entity.",
            **field_kwargs,
        )
    else:
        additional_info = _get_additional_info(input_type)
        return Field(..., description=description + additional_info, **field_kwargs)


class _Graph(BaseModel):
    nodes: Optional[List]
    relationships: Optional[List]


class UnstructuredRelation(BaseModel):
    head: str = Field(
        description=(
            "extracted head entity like Microsoft, Apple, John. "
            "Must use human-readable unique identifier."
        )
    )
    head_type: str = Field(
        description="type of the extracted head entity like Person, Company, etc"
    )
    relation: str = Field(description="relation between the head and the tail entities")
    tail: str = Field(
        description=(
            "extracted tail entity like Microsoft, Apple, John. "
            "Must use human-readable unique identifier."
        )
    )
    tail_type: str = Field(
        description="type of the extracted tail entity like Person, Company, etc"
    )


class SSTEdge(BaseModel):
    source: str = Field(description="Source statement (must match one of nodes)")
    relation: str = Field(description="Relation between statements")
    target: str = Field(description="Target statement (must match one of nodes)")


class SSTGraph(BaseModel):
    nodes: List[str] = Field(description="List of minimal, atomic statements")
    edges: Optional[List[SSTEdge]] = Field(
        default=None, description="List of edges connecting statements"
    )


def create_json_turkish_prompt(
    node_labels: Optional[List[str]] = None,
    rel_types: Optional[Union[List[str], List[Tuple[str, str, str]]]] = None,
    relationship_type: Optional[str] = None,
    additional_instructions: Optional[str] = "",
) -> ChatPromptTemplate:
    """
    Sadece JSON format talimatları + Türkçe prompt içeren basit mod
    """
    
    # Basit JSON format talimatları
    json_instructions = [
        "You must generate the output in a JSON format containing a list "
        'with JSON objects. Each object should have the keys: "head", '
        '"head_type", "relation", "tail", and "tail_type".',
        "CRITICAL LIMIT: Extract MAXIMUM 3 important relationships per document chunk.",
    ]
    
    system_prompt = "\n".join(json_instructions)
    system_message = SystemMessage(content=system_prompt)
    parser = JsonOutputParser(pydantic_object=UnstructuredRelation)

    # Türkçe prompt - yapısında değişiklik yok
    turkish_prompt = create_turkish_insurance_prompt(
        allowed_nodes=node_labels,
        allowed_relationships=rel_types
    )
    
    human_string_parts = [
        turkish_prompt,
        additional_instructions,
        "For the following text, extract entities and relations. "
        "NODE TYPES: Create most accurate types. RELATIONSHIP TYPES: Use only allowed types. "
        "{format_instructions}\nText: {input}",
    ]
    
    human_prompt_string = "\n".join(filter(None, human_string_parts))
    human_prompt = PromptTemplate(
        template=human_prompt_string,
        input_variables=["input"],
        partial_variables={
            "format_instructions": parser.get_format_instructions(),
        },
    )

    human_message_prompt = HumanMessagePromptTemplate(prompt=human_prompt)

    chat_prompt = ChatPromptTemplate.from_messages(
        [system_message, human_message_prompt]
    )
    return chat_prompt


def create_unstructured_prompt(
    node_labels: Optional[List[str]] = None,
    rel_types: Optional[Union[List[str], List[Tuple[str, str, str]]]] = None,
    relationship_type: Optional[str] = None,
    additional_instructions: Optional[str] = "",
) -> ChatPromptTemplate:
    node_labels_str = str(node_labels) if node_labels else ""
    if rel_types:
        if relationship_type == "tuple":
            rel_types_str = str(list({item[1] for item in rel_types}))
        else:
            rel_types_str = str(rel_types)
    else:
        rel_types_str = ""
    base_string_parts = [
        "You are a top-tier algorithm designed for extracting information in "
        "structured formats to build a knowledge graph. Your task is to identify "
        "the entities and relations requested with the user prompt from a given "
        "text. You must generate the output in a JSON format containing a list "
        'with JSON objects. Each object should have the keys: "head", '
        '"head_type", "relation", "tail", and "tail_type". The "head" '
        "key must contain the text of the extracted entity with one of the types "
        "from the provided list in the user prompt. "
        "CRITICAL LIMIT: Extract MAXIMUM 3 relationships per document chunk.",
        "CRITICAL FOR INSURANCE DOCUMENTS: Extract ONLY the 3 most business-essential relationships. "
        "Focus on high-level connections like Customer-Policy, Agent-Customer, Policy-Asset. "
        "DO NOT extract monetary amounts, policy numbers, legal clauses, technical jargon, "
        "administrative details, or specific financial calculations. "
        "STRICT PRIORITY: Customer relationships, Policy coverage, Agent assignments only.",
        f'The "head_type" key must contain the type of the extracted head entity. '
        f"CREATE the most accurate type for each entity! "
        f"PREFERRED types (use if suitable): {node_labels_str}. "
        f"CREATE NEW TYPES if none of the preferred types fit the entity!"
        if node_labels
        else "",
        f'The "relation" key must contain the type of relation between the "head" '
        f'and the "tail", which MUST be one of these ALLOWED relations: {rel_types_str}. '
        f"DO NOT use any other relationship types!"
        if rel_types
        else "",
        f'The "tail" key must represent the text of an extracted entity which is '
        f'the tail of the relation, and the "tail_type" key must contain the type '
        f"of the tail entity. CREATE the most accurate type for each entity! "
        f"PREFERRED types (use if suitable): {node_labels_str}. "
        f"CREATE NEW TYPES if none of the preferred types fit the entity!"
        if node_labels
        else "",
        "CRITICAL: Your task is to extract relationships from text strictly adhering "
        "to the provided schema. The relationships can ONLY appear "
        "between specific node types are presented in the schema format "
        "like: (Entity1Type, RELATIONSHIP_TYPE, Entity2Type). "
        f"ONLY use this provided schema: {rel_types}. "
        f"REJECT any relationships that don't match this schema! "
        f"BUT CREATE appropriate node types for entities (not limited to allowed)."
        if relationship_type == "tuple"
        else "",
        "Extract ONLY the 3 most critical business relationships. "
        "Focus on quality over quantity - choose only the highest-value connections. "
        "Avoid extracting redundant or minor entities. Maintain "
        "Entity Consistency: When extracting entities, it's vital to ensure "
        'consistency. If an entity, such as "John Doe", is mentioned multiple '
        "times in the text but is referred to by different names or pronouns "
        '(e.g., "Joe", "he"), always use the most complete identifier for '
        "that entity. The knowledge graph should be coherent and easily "
        "understandable, so maintaining consistency in entity references is "
        "crucial.",
        "IMPORTANT NOTES:\n"
        "- Don't add any explanation and text.\n"
        "- NODE TYPES: Create best-fitting types (not limited to allowed list).\n"
        "- RELATIONSHIP TYPES: Use ONLY allowed relationship types (strict).\n"
        "- CRITICAL: Extract MAXIMUM 3 relationships - quality over quantity.\n"
        "- It's better to extract fewer, correct entities than many incorrect ones.\n"
        "- Avoid extracting redundant, minor, or trivial entities.\n"
        "- STRICT LIMIT: Maximum 3 relationships per document chunk.",
        additional_instructions,
    ]
    system_prompt = "\n".join(filter(None, base_string_parts))

    system_message = SystemMessage(content=system_prompt)
    parser = JsonOutputParser(pydantic_object=UnstructuredRelation)

    human_string_parts = [
        "Based on the following example, extract entities and "
        "relations from the provided text.\n\n",
        "FLEXIBLE: Use the following entity types when suitable, create new types when needed:"
        "# PREFERRED ENTITY TYPES:"
        "{node_labels}"
        if node_labels
        else "",
        "MANDATORY: Use ONLY the following relation types, don't use any other relation types:"
        "# ALLOWED RELATION TYPES:"
        "{rel_types}"
        if rel_types
        else "",
        "CRITICAL: Your task is to extract relationships from text strictly adhering "
        "to the provided schema. The relationships can ONLY appear "
        "between specific node types are presented in the schema format "
        "like: (Entity1Type, RELATIONSHIP_TYPE, Entity2Type). "
        f"MANDATORY SCHEMA: {rel_types}. "
        f"REJECT any relationships that don't match this exact schema! "
        f"BUT CREATE appropriate node types for entities (not limited to preferred)."
        if relationship_type == "tuple"
        else "",
        "Below are a number of examples of text and their extracted "
        "entities and relationships."
        "{examples}\n",
        "IMPORTANT REMINDER: "
        "NODE TYPES: Create most accurate types (not limited to preferred list). "
        "RELATIONSHIP TYPES: Use ONLY allowed types (strict). "
        "Extract MAXIMUM 3 relationships that provide the highest business value.",
        additional_instructions,
        "For the following text, extract entities and relations as "
        "in the provided example. NODE TYPES: Create most accurate types. RELATIONSHIP TYPES: Use only allowed types. Limit to maximum 3 relationships."
        "{format_instructions}\nText: {input}",
    ]
    human_prompt_string = "\n".join(filter(None, human_string_parts))
    human_prompt = PromptTemplate(
        template=human_prompt_string,
        input_variables=["input"],
        partial_variables={
            "format_instructions": parser.get_format_instructions(),
            "node_labels": node_labels,
            "rel_types": rel_types,
            "examples": examples,
        },
    )

    human_message_prompt = HumanMessagePromptTemplate(prompt=human_prompt)

    chat_prompt = ChatPromptTemplate.from_messages(
        [system_message, human_message_prompt]
    )
    return chat_prompt


def create_sst_prompt(
    node_labels: Optional[List[str]] = None,
    rel_types: Optional[List[str]] = None,
    additional_instructions: Optional[str] = "",
) -> ChatPromptTemplate:
    """
    SST mode prompt: nodes are minimal statements, edges connect statements.
    Relations are strictly restricted to provided rel_types (DB), if given.
    """
    allowed_relations = (
        rel_types if rel_types else ["SIMILARITY", "LEADS_TO", "CONTAINS", "PROPERTY"]
    )
    system_parts = [
        "You are an expert in knowledge representation using the Semantic Spacetime (SST) model.",
        "Your task is to transform the given text into a knowledge graph where nodes are complete minimal statements (e.g., 'X buys Y'), not just isolated entities.",
        "Arrows (edges) connect these statement-nodes to show how statements relate in context.",
        "\nRules:",
        "1) Each node must be a minimal, self-contained, factual statement.",
        "2) Use edges only between statements with higher-level relations.",
        f"   Allowed relations (STRICT): {allowed_relations}",
        "3) Edges NEVER replace the content of a statement node.",
        "4) Output must be JSON with two fields: nodes and edges.",
        "\nOutput Schema:",
        '{\n  "nodes": ["Statement1", "Statement2", ...],\n  "edges": [\n    {"source": "Statement1", "relation": "LEADS_TO", "target": "Statement2"}\n  ]\n}',
    ]
    # Node label guidance: prefer domain terms
    if node_labels:
        system_parts.append(
            "Guidance: Prefer using these domain terms within statements when applicable: "
            + ", ".join(node_labels)
        )

    system_message = SystemMessage(content="\n".join(system_parts))

    # Human prompt
    human_parts = [
        additional_instructions,
        "Transform the following text to the SST graph JSON strictly using only the allowed relations.",
        "Text: {input}",
    ]
    human_prompt = PromptTemplate(
        template="\n".join([p for p in human_parts if p]),
        input_variables=["input"],
    )
    human_message_prompt = HumanMessagePromptTemplate(prompt=human_prompt)
    return ChatPromptTemplate.from_messages([system_message, human_message_prompt])


def create_simple_model(
    node_labels: Optional[List[str]] = None,
    rel_types: Optional[Union[List[str], List[Tuple[str, str, str]]]] = None,
    node_properties: Union[bool, List[str]] = False,
    llm_type: Optional[str] = None,
    relationship_properties: Union[bool, List[str]] = False,
    relationship_type: Optional[str] = None,
) -> Type[_Graph]:
    """
    Create a simple graph model with optional constraints on node
    and relationship types.

    Args:
        node_labels (Optional[List[str]]): Specifies the allowed node types.
            Defaults to None, allowing all node types.
        rel_types (Optional[List[str]]): Specifies the allowed relationship types.
            Defaults to None, allowing all relationship types.
        node_properties (Union[bool, List[str]]): Specifies if node properties should
            be included. If a list is provided, only properties with keys in the list
            will be included. If True, all properties are included. Defaults to False.
        relationship_properties (Union[bool, List[str]]): Specifies if relationship
            properties should be included. If a list is provided, only properties with
            keys in the list will be included. If True, all properties are included.
            Defaults to False.
        llm_type (Optional[str]): The type of the language model. Defaults to None.
            Only openai supports enum param: openai-chat.

    Returns:
        Type[_Graph]: A graph model with the specified constraints.

    Raises:
        ValueError: If 'id' is included in the node or relationship properties list.
    """

    node_fields: Dict[str, Tuple[Any, Any]] = {
        "id": (
            str,
            Field(..., description="Name or human-readable unique identifier."),
        ),
        "type": (
            str,
            optional_enum_field(
                node_labels,
                description="The type or label of the node.",
                input_type="node",
                llm_type=llm_type,
            ),
        ),
    }

    if node_properties:
        if isinstance(node_properties, list) and "id" in node_properties:
            raise ValueError("The node property 'id' is reserved and cannot be used.")
        # Map True to empty array
        node_properties_mapped: List[str] = (
            [] if node_properties is True else node_properties
        )

        class Property(BaseModel):
            """A single property consisting of key and value"""

            key: str = optional_enum_field(
                node_properties_mapped,
                description="Property key.",
                input_type="property",
                llm_type=llm_type,
            )
            value: str = Field(
                ...,
                description=(
                    "Extracted value. Any date value "
                    "should be formatted as yyyy-mm-dd."
                ),
            )

        node_fields["properties"] = (
            Optional[List[Property]],
            Field(None, description="List of node properties"),
        )
    SimpleNode = create_model("SimpleNode", **node_fields)  # type: ignore

    relationship_fields: Dict[str, Tuple[Any, Any]] = {
        "source_node_id": (
            str,
            Field(
                ...,
                description="Name or human-readable unique identifier of source node",
            ),
        ),
        "source_node_type": (
            str,
            optional_enum_field(
                node_labels,
                description="The type or label of the source node.",
                input_type="node",
                llm_type=llm_type,
            ),
        ),
        "target_node_id": (
            str,
            Field(
                ...,
                description="Name or human-readable unique identifier of target node",
            ),
        ),
        "target_node_type": (
            str,
            optional_enum_field(
                node_labels,
                description="The type or label of the target node.",
                input_type="node",
                llm_type=llm_type,
            ),
        ),
        "type": (
            str,
            optional_enum_field(
                rel_types,
                description="The type of the relationship.",
                input_type="relationship",
                llm_type=llm_type,
                relationship_type=relationship_type,
            ),
        ),
    }
    if relationship_properties:
        if (
            isinstance(relationship_properties, list)
            and "id" in relationship_properties
        ):
            raise ValueError(
                "The relationship property 'id' is reserved and cannot be used."
            )
        # Map True to empty array
        relationship_properties_mapped: List[str] = (
            [] if relationship_properties is True else relationship_properties
        )

        class RelationshipProperty(BaseModel):
            """A single property consisting of key and value"""

            key: str = optional_enum_field(
                relationship_properties_mapped,
                description="Property key.",
                input_type="property",
                llm_type=llm_type,
            )
            value: str = Field(
                ...,
                description=(
                    "Extracted value. Any date value "
                    "should be formatted as yyyy-mm-dd."
                ),
            )

        relationship_fields["properties"] = (
            Optional[List[RelationshipProperty]],
            Field(None, description="List of relationship properties"),
        )
    SimpleRelationship = create_model("SimpleRelationship", **relationship_fields)  # type: ignore
    # Add a docstring to the dynamically created model
    if relationship_type == "tuple":
        SimpleRelationship.__doc__ = (
            "Your task is to extract relationships from text strictly adhering "
            "to the provided schema. The relationships can only appear "
            "between specific node types are presented in the schema format "
            "like: (Entity1Type, RELATIONSHIP_TYPE, Entity2Type) /n"
            f"Provided schema is {rel_types}"
        )

    class DynamicGraph(_Graph):
        """Represents a graph document consisting of nodes and relationships."""

        nodes: Optional[List[SimpleNode]] = Field(description="List of nodes")  # type: ignore
        relationships: Optional[List[SimpleRelationship]] = Field(  # type: ignore
            description="List of relationships"
        )

    return DynamicGraph


def map_to_base_node(node: Any) -> Node:
    """Map the SimpleNode to the base Node."""
    properties = {}
    if hasattr(node, "properties") and node.properties:
        for p in node.properties:
            properties[format_property_key(p.key)] = p.value
    return Node(id=node.id, type=node.type, properties=properties)


def map_to_base_relationship(rel: Any) -> Relationship:
    """Map the SimpleRelationship to the base Relationship."""
    source = Node(id=rel.source_node_id, type=rel.source_node_type)
    target = Node(id=rel.target_node_id, type=rel.target_node_type)
    properties = {}
    if hasattr(rel, "properties") and rel.properties:
        for p in rel.properties:
            properties[format_property_key(p.key)] = p.value
    return Relationship(
        source=source, target=target, type=rel.type, properties=properties
    )


def _parse_and_clean_json(
    argument_json: Dict[str, Any],
) -> Tuple[List[Node], List[Relationship]]:
    nodes = []
    for node in argument_json["nodes"]:
        if not node.get("id"):  # Id is mandatory, skip this node
            continue
        node_properties = {}
        if "properties" in node and node["properties"]:
            for p in node["properties"]:
                node_properties[format_property_key(p["key"])] = p["value"]
        nodes.append(
            Node(
                id=node["id"],
                type=node.get("type", DEFAULT_NODE_TYPE),
                properties=node_properties,
            )
        )
    relationships = []
    for rel in argument_json["relationships"]:
        # Mandatory props
        if (
            not rel.get("source_node_id")
            or not rel.get("target_node_id")
            or not rel.get("type")
        ):
            continue

        # Node type copying if needed from node list
        if not rel.get("source_node_type"):
            try:
                rel["source_node_type"] = [
                    el.get("type")
                    for el in argument_json["nodes"]
                    if el["id"] == rel["source_node_id"]
                ][0]
            except IndexError:
                rel["source_node_type"] = DEFAULT_NODE_TYPE
        if not rel.get("target_node_type"):
            try:
                rel["target_node_type"] = [
                    el.get("type")
                    for el in argument_json["nodes"]
                    if el["id"] == rel["target_node_id"]
                ][0]
            except IndexError:
                rel["target_node_type"] = DEFAULT_NODE_TYPE

        rel_properties = {}
        if "properties" in rel and rel["properties"]:
            for p in rel["properties"]:
                rel_properties[format_property_key(p["key"])] = p["value"]

        source_node = Node(
            id=rel["source_node_id"],
            type=rel["source_node_type"],
        )
        target_node = Node(
            id=rel["target_node_id"],
            type=rel["target_node_type"],
        )
        relationships.append(
            Relationship(
                source=source_node,
                target=target_node,
                type=rel["type"],
                properties=rel_properties,
            )
        )
    return nodes, relationships


def _format_nodes(nodes: List[Node], allowed_nodes: Optional[List[str]] = None) -> List[Node]:
    """
    Format nodes with proper case handling for allowed node types
    """
    def get_proper_node_type(node_type: str, allowed_types: Optional[List[str]] = None) -> str:
        if not allowed_types:
            return node_type.capitalize() if node_type else DEFAULT_NODE_TYPE
        
        # Exact match kontrolü
        if node_type in allowed_types:
            return node_type
        
        # Case-insensitive match
        lower_type = node_type.lower()
        for allowed_type in allowed_types:
            if allowed_type.lower() == lower_type:
                return allowed_type
        
        # Default capitalize if no match
        return node_type.capitalize() if node_type else DEFAULT_NODE_TYPE
    
    return [
        Node(
            id=el.id.title() if isinstance(el.id, str) else el.id,
            type=get_proper_node_type(el.type, allowed_nodes),
            properties=el.properties,
        )
        for el in nodes
    ]


def _format_relationships(rels: List[Relationship], allowed_nodes: Optional[List[str]] = None) -> List[Relationship]:
    return [
        Relationship(
            source=_format_nodes([el.source], allowed_nodes)[0],
            target=_format_nodes([el.target], allowed_nodes)[0],
            type=el.type.replace(" ", "_").upper(),
            properties=el.properties,
        )
        for el in rels
    ]


def format_property_key(s: str) -> str:
    words = s.split()
    if not words:
        return s
    first_word = words[0].lower()
    capitalized_words = [word.capitalize() for word in words[1:]]
    return "".join([first_word] + capitalized_words)


def _convert_to_graph_document(
    raw_schema: Dict[Any, Any],
    allowed_nodes: Optional[List[str]] = None,
) -> Tuple[List[Node], List[Relationship]]:
    # If there are validation errors
    if not raw_schema["parsed"]:
        try:
            try:  # OpenAI type response
                argument_json = json.loads(
                    raw_schema["raw"].additional_kwargs["tool_calls"][0]["function"][
                        "arguments"
                    ]
                )
            except Exception:  # Google type response
                try:
                    argument_json = json.loads(
                        raw_schema["raw"].additional_kwargs["function_call"][
                            "arguments"
                        ]
                    )
                except Exception:  # Ollama type response
                    argument_json = raw_schema["raw"].tool_calls[0]["args"]
                    if isinstance(argument_json["nodes"], str):
                        argument_json["nodes"] = json.loads(argument_json["nodes"])
                    if isinstance(argument_json["relationships"], str):
                        argument_json["relationships"] = json.loads(
                            argument_json["relationships"]
                        )
            nodes, relationships = _parse_and_clean_json(argument_json)
        except Exception:  # If we can't parse JSON
            return ([], [])
    else:  # If there are no validation errors use parsed pydantic object
        parsed_schema: _Graph = raw_schema["parsed"]
        nodes = (
            [map_to_base_node(node) for node in parsed_schema.nodes if node.id]
            if parsed_schema.nodes
            else []
        )

        relationships = (
            [
                map_to_base_relationship(rel)
                for rel in parsed_schema.relationships
                if rel.type and rel.source_node_id and rel.target_node_id
            ]
            if parsed_schema.relationships
            else []
        )
    # Title / Capitalize with proper allowed types
    return _format_nodes(nodes, allowed_nodes), _format_relationships(relationships, allowed_nodes)


def _convert_sst_to_graph_document(
    raw_schema: Dict[Any, Any]
) -> Tuple[List[Node], List[Relationship]]:
    """
    Convert SST structured raw output to GraphDocument nodes/relationships.
    Nodes are statement strings mapped to Node(type=SST_NODE_TYPE),
    edges connect statements using the given relation.
    """
    try:
        if raw_schema.get("parsed"):
            parsed: SSTGraph = raw_schema["parsed"]  # type: ignore
            stmt_nodes = list(dict.fromkeys(parsed.nodes or []))  # de-dup, keep order
            edges = parsed.edges or []
        else:
            # Fallback: try to parse from provider specific raw
            content = None
            try:
                content = raw_schema["raw"].content
            except Exception:
                try:
                    content = str(raw_schema["raw"])
                except Exception:
                    content = None
            if not content:
                return ([], [])
            import json as _json
            payload = _json.loads(content)
            stmt_nodes = list(dict.fromkeys(payload.get("nodes", [])))
            edges = payload.get("edges", [])
    except Exception:
        return ([], [])

    node_map: Dict[str, Node] = {}
    nodes: List[Node] = []
    for s in stmt_nodes:
        if isinstance(s, str) and s.strip():
            n = Node(id=s.strip(), type=SST_NODE_TYPE)
            node_map[s.strip()] = n
            nodes.append(n)

    relationships: List[Relationship] = []
    for e in edges:
        try:
            src = e.source if isinstance(e, SSTEdge) else e.get("source")
            rel = e.relation if isinstance(e, SSTEdge) else e.get("relation")
            tgt = e.target if isinstance(e, SSTEdge) else e.get("target")
            if not (src and rel and tgt):
                continue
            # Ensure nodes exist
            if src not in node_map:
                node_map[src] = Node(id=src, type=SST_NODE_TYPE)
                nodes.append(node_map[src])
            if tgt not in node_map:
                node_map[tgt] = Node(id=tgt, type=SST_NODE_TYPE)
                nodes.append(node_map[tgt])
            relationships.append(
                Relationship(
                    source=node_map[src], target=node_map[tgt], type=str(rel)
                )
            )
        except Exception:
            continue

    return nodes, relationships


def _parse_sst_unstructured(payload: Any) -> Tuple[List[Node], List[Relationship]]:
    """Parse unstructured SST JSON payload {nodes: [...], edges: [...]}"""
    try:
        stmt_nodes = list(dict.fromkeys(payload.get("nodes", [])))
        edges = payload.get("edges", [])
    except Exception:
        return ([], [])
    nodes_map: Dict[str, Node] = {}
    nodes: List[Node] = []
    for s in stmt_nodes:
        if isinstance(s, str) and s.strip():
            n = Node(id=s.strip(), type=SST_NODE_TYPE)
            nodes_map[s.strip()] = n
            nodes.append(n)
    relationships: List[Relationship] = []
    for e in edges:
        try:
            src = e.get("source")
            rel = e.get("relation")
            tgt = e.get("target")
            if not (src and rel and tgt):
                continue
            if src not in nodes_map:
                nodes_map[src] = Node(id=src, type=SST_NODE_TYPE)
                nodes.append(nodes_map[src])
            if tgt not in nodes_map:
                nodes_map[tgt] = Node(id=tgt, type=SST_NODE_TYPE)
                nodes.append(nodes_map[tgt])
            relationships.append(
                Relationship(source=nodes_map[src], target=nodes_map[tgt], type=str(rel))
            )
        except Exception:
            continue
    return nodes, relationships


def validate_and_get_relationship_type(
    allowed_relationships: Union[List[str], List[Tuple[str, str, str]]],
    allowed_nodes: Optional[List[str]],
) -> Optional[str]:
    if allowed_relationships and not isinstance(allowed_relationships, list):
        raise ValueError("`allowed_relationships` attribute must be a list.")
    # If it's an empty list
    if not allowed_relationships:
        return None
    # Validate list of strings
    if all(isinstance(item, str) for item in allowed_relationships):
        # Valid: all items are strings, no further checks needed.
        return "string"

    # Validate list of 3-tuples and check if first/last elements are in allowed_nodes
    if all(
        isinstance(item, tuple)
        and len(item) == 3
        and all(isinstance(subitem, str) for subitem in item)
        and item[0] in allowed_nodes  # type: ignore
        and item[2] in allowed_nodes  # type: ignore
        for item in allowed_relationships
    ):
        # all items are 3-tuples, and the first/last elements are in allowed_nodes.
        return "tuple"

    # If the input doesn't match any of the valid cases, raise a ValueError
    raise ValueError(
        "`allowed_relationships` must be list of strings or a list of 3-item tuples. "
        "For tuples, the first and last elements must be in the `allowed_nodes` list."
    )


class LLMGraphTransformer:
    """Transform documents into graph-based documents using a LLM.

    It allows specifying constraints on the types of nodes and relationships to include
    in the output graph. The class supports extracting properties for both nodes and
    relationships.

    Args:
        llm (BaseLanguageModel): An instance of a language model supporting structured
          output.
        allowed_nodes (List[str], optional): Specifies which node types are
          allowed in the graph. Defaults to an empty list, allowing all node types.
        allowed_relationships (List[str], optional): Specifies which relationship types
          are allowed in the graph. Defaults to an empty list, allowing all relationship
          types.
        prompt (Optional[ChatPromptTemplate], optional): The prompt to pass to
          the LLM with additional instructions.
        strict_mode (bool, optional): Determines whether the transformer should apply
          filtering to strictly adhere to `allowed_nodes` and `allowed_relationships`.
          Defaults to True.
        node_properties (Union[bool, List[str]]): If True, the LLM can extract any
          node properties from text. Alternatively, a list of valid properties can
          be provided for the LLM to extract, restricting extraction to those specified.
        relationship_properties (Union[bool, List[str]]): If True, the LLM can extract
          any relationship properties from text. Alternatively, a list of valid
          properties can be provided for the LLM to extract, restricting extraction to
          those specified.
        ignore_tool_usage (bool): Indicates whether the transformer should
          bypass the use of structured output functionality of the language model.
          If set to True, the transformer will not use the language model's native
          function calling capabilities to handle structured output. Defaults to False.
        use_simple_json_mode (bool): If True, uses simple JSON format + Turkish prompt mode.
          This is a minimal mode with only JSON instructions and Turkish prompt structure.
          Defaults to False.
        additional_instructions (str): Allows you to add additional instructions
          to the prompt without having to change the whole prompt.

    Example:
        .. code-block:: python
            from langchain_experimental.graph_transformers import LLMGraphTransformer
            from langchain_core.documents import Document
            from langchain_openai import ChatOpenAI

            llm=ChatOpenAI(temperature=0)
            transformer = LLMGraphTransformer(
                llm=llm,
                allowed_nodes=["Person", "Organization"])

            doc = Document(page_content="Elon Musk is suing OpenAI")
            graph_documents = transformer.convert_to_graph_documents([doc])
    """

    def __init__(
        self,
        llm: BaseLanguageModel,
        allowed_nodes: List[str] = [],
        allowed_relationships: Union[List[str], List[Tuple[str, str, str]]] = [],
        prompt: Optional[ChatPromptTemplate] = None,
        strict_mode: bool = True,
        node_properties: Union[bool, List[str]] = False,
        relationship_properties: Union[bool, List[str]] = False,
        ignore_tool_usage: bool = False,
        use_simple_json_mode: bool = False,  # Yeni parametre: Basit JSON + Türkçe mod
        additional_instructions: str = "",
        use_db_schema: bool = True,  # Yeni parametre: DB schema'sını kullan
        graph: Optional[Any] = None,  # Graph instance'ı dinamik schema için
        enable_llm_logging: bool = True,  # Yeni parametre: LLM extraction logging'i açıp kapat
        use_sst_mode: bool = False,  # Yeni parametre: SST (statement-node) modu
    ) -> None:
        
        # Eğer allowed_nodes ve allowed_relationships boşsa ve use_db_schema True ise, DB'den çek
        if use_db_schema and (not allowed_nodes or not allowed_relationships):
            print("🔄 Veritabanından schema çekiliyor...")
            db_nodes, db_relationships = get_db_schema_for_prompt(graph_instance=graph)
            
            if not allowed_nodes:
                allowed_nodes = db_nodes
                print(f"✅ DB'den node tipleri alındı: {allowed_nodes}")
            
            if not allowed_relationships:
                allowed_relationships = db_relationships
                print(f"✅ DB'den relationship tipleri alındı: {allowed_relationships}")
        
        # Validate and check allowed relationships input
        self._relationship_type = validate_and_get_relationship_type(
            allowed_relationships, allowed_nodes
        )

        self.allowed_nodes = allowed_nodes
        self.allowed_relationships = allowed_relationships
        self.strict_mode = strict_mode
        self._function_call = not ignore_tool_usage
        self.use_sst_mode = use_sst_mode
        
        # Graph instance'ını store et ki process_response'da kullanabilelim
        self.graph = graph
        
        # LLM logging ayarı
        self.enable_llm_logging = enable_llm_logging
        
        print(f"🎯 Final allowed nodes: {self.allowed_nodes}")
        print(f"🔗 Final allowed relationships: {self.allowed_relationships}")
        print(f"📊 LLM Logging: {'✅ Açık' if self.enable_llm_logging else '❌ Kapalı'}")
        
        # Check if the LLM really supports structured output
        if self._function_call:
            try:
                llm.with_structured_output(_Graph)
            except NotImplementedError:
                self._function_call = False
        if not self._function_call:
            if node_properties or relationship_properties:
                raise ValueError(
                    "The 'node_properties' and 'relationship_properties' parameters "
                    "cannot be used in combination with a LLM that doesn't support "
                    "native function calling."
                )
            try:
                import json_repair  # type: ignore

                self.json_repair = json_repair
            except ImportError:
                raise ImportError(
                    "Could not import json_repair python package. "
                    "Please install it with `pip install json-repair`."
                )
            # Prompt seçimi
            if not prompt:
                if self.use_sst_mode:
                    prompt = create_sst_prompt(
                        node_labels=allowed_nodes,
                        rel_types=(allowed_relationships if isinstance(next(iter(allowed_relationships), None), str) else [r for r in []]),
                        additional_instructions=additional_instructions,
                    )
                elif use_simple_json_mode:
                    prompt = create_json_turkish_prompt(
                        allowed_nodes,
                        allowed_relationships,
                        self._relationship_type,
                        additional_instructions,
                    )
                else:
                    turkish_system_prompt = create_turkish_insurance_prompt(
                        allowed_nodes=allowed_nodes,
                        allowed_relationships=allowed_relationships
                    )
                    prompt = create_unstructured_prompt(
                        allowed_nodes,
                        allowed_relationships,
                        self._relationship_type,
                        f"{additional_instructions}\n{turkish_system_prompt}",
                    )
            self.chain = prompt | llm
        else:
            # Define chain
            try:
                llm_type = llm._llm_type  # type: ignore
            except AttributeError:
                llm_type = None
            if self.use_sst_mode:
                # Structured SST schema
                # Build relation field with enum when possible
                class _SSTEdgeModel(BaseModel):
                    source: str = Field(
                        ..., description="Source statement (must match one of nodes)"
                    )
                    relation: str = optional_enum_field(
                        list(allowed_relationships) if isinstance(next(iter(allowed_relationships), None), str) else None,
                        description="Relation between statements (STRICT)",
                        input_type="relationship",
                        llm_type=llm_type,
                    )
                    target: str = Field(
                        ..., description="Target statement (must match one of nodes)"
                    )

                class _SSTGraphModel(BaseModel):
                    nodes: List[str] = Field(
                        ..., description="List of minimal, atomic statements"
                    )
                    edges: Optional[List[_SSTEdgeModel]] = Field(
                        default=None, description="List of edges between statements"
                    )

                structured_llm = llm.with_structured_output(
                    _SSTGraphModel, include_raw=True
                )
                if not prompt:
                    prompt = create_sst_prompt(
                        node_labels=allowed_nodes,
                        rel_types=(allowed_relationships if isinstance(next(iter(allowed_relationships), None), str) else None),
                        additional_instructions=additional_instructions,
                    )
                self.chain = prompt | structured_llm
            else:
                schema = create_simple_model(
                    allowed_nodes,
                    allowed_relationships,
                    node_properties,
                    llm_type,
                    relationship_properties,
                    self._relationship_type,
                )
                structured_llm = llm.with_structured_output(schema, include_raw=True)
                
                if not prompt:
                    turkish_system_prompt = create_turkish_insurance_prompt(
                        allowed_nodes=allowed_nodes,
                        allowed_relationships=allowed_relationships
                    )
                    prompt = get_default_prompt(
                        f"{additional_instructions}\n{turkish_system_prompt}",
                        allowed_nodes,
                        allowed_relationships,
                        self._relationship_type,
                    )
                self.chain = prompt | structured_llm

    def process_response(
        self, document: Document, config: Optional[RunnableConfig] = None
    ) -> GraphDocument:
        """
        Processes a single document, transforming it into a graph document using
        an LLM based on the model's schema and constraints.
        """
        text = document.page_content
        
        # Chunk ID'yi metadata'dan al
        chunk_id = None
        document_filename = None
        if hasattr(document, 'metadata') and document.metadata:
            if isinstance(document.metadata.get('chunk_id'), list):
                chunk_id = document.metadata['chunk_id'][0] if document.metadata['chunk_id'] else None
            else:
                chunk_id = document.metadata.get('chunk_id')
            
            # Document filename'i metadata'dan al
            document_filename = (document.metadata.get('fileName') or 
                               document.metadata.get('filename') or 
                               document.metadata.get('source') or 
                               document.metadata.get('file_name') or 
                               document.metadata.get('document_name'))
        
        # Mevcut context'i al ve text'e ekle
        existing_context = ""
        if chunk_id and hasattr(self, 'graph') and self.graph:
            existing_context = get_existing_context_for_prompt(self.graph, chunk_id)
            if existing_context:
                logging.info(f"📋 LLM entity extraction context bulundu: {len(existing_context)} karakter")
                logging.info(f"🔗 LLM entity extraction context içeriği: {existing_context[:100]}...")
        
        # Context'i text'e ekle
        if existing_context:
            enhanced_text = f"{existing_context}METİN:\n{text}"
        else:
            enhanced_text = text
        
        # LOG: Input bilgileri
        logging.info("🚀 LLM Graph Transformer process_response başlıyor")
        logging.info(f"📝 LLM entity extraction input text uzunluğu: {len(enhanced_text)} karakter")
        logging.info(f"⚙️ LLM entity extraction function call modu: {self._function_call}")
        logging.info(f"🎯 LLM entity extraction allowed nodes: {self.allowed_nodes}")
        logging.info(f"🔗 LLM entity extraction allowed relationships: {self.allowed_relationships}")
        logging.info(f"� LLM entity extraction relationship type: {self._relationship_type}")
        
        # LOG: Kullanılan prompt'u logla
        logging.info("📋 LLM entity extraction PROMPT DETAYLARI:")
        
        try:
            if hasattr(self.chain, 'first'):
                prompt_template = self.chain.first
                if hasattr(prompt_template, 'format'):
                    formatted_prompt = prompt_template.format(input=text[:500] + "...")
                    print(f"📋 FULL PROMPT (structured mode):\n{formatted_prompt}")
                elif hasattr(prompt_template, 'messages'):
                    print(f"📋 Prompt mesaj sayısı: {len(prompt_template.messages)}")
                    for i, message in enumerate(prompt_template.messages):
                        if hasattr(message, 'content'):
                            print(f"📋 FULL Prompt mesajı {i}:\n{message.content}")
                            # Prompt içinde allowed değerler var mı kontrol et
                            content = str(message.content)
                            if self.allowed_nodes:
                                for node in self.allowed_nodes:
                                    if node in content:
                                        print(f"✅ '{node}' node tipi prompt'ta bulundu")
                                    else:
                                        print(f"❌ '{node}' node tipi prompt'ta bulunamadı")
                            if self.allowed_relationships:
                                for rel in (self.allowed_relationships if isinstance(self.allowed_relationships[0], str) else [r[1] for r in self.allowed_relationships]):
                                    if rel in content:
                                        print(f"✅ '{rel}' relationship tipi prompt'ta bulundu")
                                    else:
                                        print(f"❌ '{rel}' relationship tipi prompt'ta bulunamadı")
                        elif hasattr(message, 'prompt') and hasattr(message.prompt, 'template'):
                            print(f"📋 FULL Prompt template {i}:\n{message.prompt.template}")
                            # Template'i input ile format et
                            try:
                                formatted_template = message.prompt.format(
                                    input=text[:500] + "...",
                                    node_labels=self.allowed_nodes,
                                    rel_types=self.allowed_relationships
                                )
                                print(f"📋 FULL Formatted template {i}:\n{formatted_template}")
                            except Exception as format_error:
                                print(f"Template format hatası: {format_error}")
        except Exception as prompt_log_error:
            print(f"Prompt loglama hatası: {prompt_log_error}")
        
        # LOG: LLM çağrısı ve token kullanımı
        import time
        start_time = time.time()
        raw_schema = self.chain.invoke({"input": enhanced_text}, config=config)
        end_time = time.time()
        
        # Token kullanımını logla
        try:
            prompt_length = len(enhanced_text)
            response_length = 0
            
            # Token usage bilgisini al
            if hasattr(raw_schema, 'usage_metadata') and raw_schema.usage_metadata:
                usage = raw_schema.usage_metadata
                input_tokens = usage.get('input_tokens', 0)
                output_tokens = usage.get('output_tokens', 0) 
                total_tokens = usage.get('total_tokens', 0)
                
                logging.info(f"🔢 LLM Token Kullanımı - Input: {input_tokens}, Output: {output_tokens}, Total: {total_tokens}")
                logging.info(f"⏱️ LLM Çağrı süresi: {end_time - start_time:.2f} saniye")
                
            elif hasattr(raw_schema, 'response_metadata') and 'token_usage' in raw_schema.response_metadata:
                usage = raw_schema.response_metadata['token_usage']
                input_tokens = usage.get('prompt_tokens', 0)
                output_tokens = usage.get('completion_tokens', 0)
                total_tokens = usage.get('total_tokens', 0)
                
                logging.info(f"🔢 LLM Token Kullanımı - Input: {input_tokens}, Output: {output_tokens}, Total: {total_tokens}")
                logging.info(f"⏱️ LLM Çağrı süresi: {end_time - start_time:.2f} saniye")
                
            else:
                # Manuel token tahmini (yaklaşık)
                if hasattr(raw_schema, 'content'):
                    response_length = len(raw_schema.content)
                else:
                    response_length = len(str(raw_schema))
                
                estimated_input_tokens = int(prompt_length / 4)  # ~4 karakter = 1 token
                estimated_output_tokens = int(response_length / 4)
                estimated_total_tokens = estimated_input_tokens + estimated_output_tokens
                
                logging.info(f"🔢 LLM Token Tahmini - Input: ~{estimated_input_tokens}, Output: ~{estimated_output_tokens}, Total: ~{estimated_total_tokens}")
                logging.info(f"📏 Prompt uzunluğu: {prompt_length} karakter, Response uzunluğu: {response_length} karakter")
                logging.info(f"⏱️ LLM Çağrı süresi: {end_time - start_time:.2f} saniye")
                
        except Exception as token_log_error:
            logging.error(f"Token loglama hatası: {token_log_error}")
        
        
        # LOG: LLM'den dönen raw sonuç
        logging.info(f"📥 LLM entity extraction raw sonuç tipi: {type(raw_schema)}")
        if hasattr(raw_schema, 'content'):
            logging.info(f"📥 LLM entity extraction raw sonuç content başlangıcı: {str(raw_schema.content)[:200]}...")
        else:
            logging.info(f"📥 LLM entity extraction raw sonuç başlangıcı: {str(raw_schema)[:200]}...")
        
        # 🟢 INCREMENTAL JSON LOGGING - LLM çıktısını kaydet
        log_llm_output_incremental(
            raw_schema, 
            chunk_id, 
            datetime.now().isoformat(),
            document_filename,
            self.enable_llm_logging
        )
        
        if self._function_call:
            raw_schema = cast(Dict[Any, Any], raw_schema)
            logging.info("🔧 LLM entity extraction function call modunda - structured output kullanılıyor")
            if self.use_sst_mode:
                nodes, relationships = _convert_sst_to_graph_document(raw_schema)
            else:
                nodes, relationships = _convert_to_graph_document(raw_schema, self.allowed_nodes)
        else:
            logging.info("🔧 LLM entity extraction unstructured modda - JSON parsing yapılıyor")
            if not isinstance(raw_schema, str):
                raw_schema = raw_schema.content
            parsed_json = self.json_repair.loads(raw_schema)
            logging.info(f"✅ LLM entity extraction JSON parse edildi")
            if self.use_sst_mode:
                nodes, relationships = _parse_sst_unstructured(parsed_json)
            else:
                nodes_set = set()
                relationships = []
                for i, rel in enumerate(parsed_json):
                    if (
                        not isinstance(rel, dict)
                        or not rel.get("head")
                        or not rel.get("tail")
                        or not rel.get("relation")
                    ):
                        print(f"❌ Relation {i} eksik property'ler nedeniyle atlandı: {rel}")
                        continue
                    print(
                        f"✅ Relation {i}: {rel.get('head')} ({rel.get('head_type')}) --[{rel.get('relation')}]--> {rel.get('tail')} ({rel.get('tail_type')})"
                    )
                    nodes_set.add((rel["head"], rel.get("head_type", DEFAULT_NODE_TYPE)))
                    nodes_set.add((rel["tail"], rel.get("tail_type", DEFAULT_NODE_TYPE)))
                    source_node = Node(
                        id=rel["head"], type=rel.get("head_type", DEFAULT_NODE_TYPE)
                    )
                    target_node = Node(
                        id=rel["tail"], type=rel.get("tail_type", DEFAULT_NODE_TYPE)
                    )
                    relationships.append(
                        Relationship(
                            source=source_node, target=target_node, type=rel["relation"]
                        )
                    )
                nodes = [Node(id=el[0], type=el[1]) for el in list(nodes_set)]

        # LOG: Filtreleme öncesi durum
        logging.info(f"🔄 LLM entity extraction filtreleme öncesi - Entity'ler: {len(nodes)}, Relationship'ler: {len(relationships)}")
        logging.info(f"🔍 LLM entity extraction filtreleme öncesi entity tipleri: {list(set([node.type for node in nodes]))}")
        logging.info(f"🔍 LLM entity extraction filtreleme öncesi relationship tipleri: {list(set([rel.type for rel in relationships]))}")

        # Apply filtering based on allowed nodes and relationships
        # Esnek node filtering, katı relationship filtering
        if self.strict_mode and (self.allowed_nodes or self.allowed_relationships):
            logging.info("🚧 LLM entity extraction filtreleme uygulanıyor...")
            
            # Node filtreleme - ESNEK (preference-based, tüm node'ları kabul et)
            if self.allowed_nodes:
                logging.info(f"🎯 LLM entity extraction node filtreleme (ESNEK): {self.allowed_nodes}")
                nodes_before = len(nodes)
                
                # Esnek filtreleme: preferred ve other node'ları say
                preferred_count = sum(1 for node in nodes if node.type.lower() in [el.lower() for el in self.allowed_nodes])
                other_count = len(nodes) - preferred_count
                
                logging.info(f"📊 LLM entity extraction esnek node analizi - Tercih edilen: {preferred_count}, Diğer: {other_count}, Toplam: {len(nodes)} (hepsi kabul)")
                # Node'ları filtreleme - ESNEK yaklaşım, hepsini kabul et
                
            # Relationship filtreleme - KATI (strict filtering)
            if self.allowed_relationships:
                logging.info(f"🔗 LLM entity extraction relationship filtreleme (KATI): {self.allowed_relationships}")
                relationships_before = len(relationships)
                
                # Filter by type and direction
                if self._relationship_type == "tuple":
                    logging.info("🔧 LLM entity extraction tuple modunda relationship filtresi")
                    relationships = [
                        rel
                        for rel in relationships
                        if (
                            (
                                rel.source.type.lower(),
                                rel.type.lower(),
                                rel.target.type.lower(),
                            )
                            in [  # type: ignore
                                (s_t.lower(), r_t.lower(), t_t.lower())
                                for s_t, r_t, t_t in self.allowed_relationships
                            ]
                        )
                    ]
                else:  # Filter by type only
                    logging.info("🔧 LLM entity extraction string modunda relationship filtresi")
                    relationships = [
                        rel
                        for rel in relationships
                        if rel.type.lower()
                        in [el.lower() for el in self.allowed_relationships]  # type: ignore
                    ]
                
                logging.info(f"📊 LLM entity extraction relationship filtresi sonrası: {relationships_before} -> {len(relationships)}")
        else:
            logging.info("⚠️ LLM entity extraction hiç filtreleme kuralı yok - tüm node ve relationship'ler kabul ediliyor")

        # LOG: Final durum
        logging.info(f"✅ LLM entity extraction final sonuç - Entity'ler: {len(nodes)}, Relationship'ler: {len(relationships)}")
        logging.info(f"🏷️ LLM entity extraction final entity tipleri: {list(set([node.type for node in nodes]))}")
        logging.info(f"🔗 LLM entity extraction final relationship tipleri: {list(set([rel.type for rel in relationships]))}")
        
        if nodes:
            logging.info(f"📝 LLM entity extraction ilk 3 entity: {[(node.id, node.type) for node in nodes[:3]]}")
        
        if relationships:
            logging.info(f"📝 LLM entity extraction ilk 3 relationship: {[(f'{rel.source.id} ({rel.source.type}) --[{rel.type}]--> {rel.target.id} ({rel.target.type})') for rel in relationships[:3]]}")

        return GraphDocument(nodes=nodes, relationships=relationships, source=document)

    def convert_to_graph_documents(
        self, documents: Sequence[Document], config: Optional[RunnableConfig] = None
    ) -> List[GraphDocument]:
        """Convert a sequence of documents into graph documents.

        Args:
            documents (Sequence[Document]): The original documents.
            kwargs: Additional keyword arguments.

        Returns:
            Sequence[GraphDocument]: The transformed documents as graphs.
        """
        return [self.process_response(document, config) for document in documents]

    async def aprocess_response(
        self, document: Document, config: Optional[RunnableConfig] = None
    ) -> GraphDocument:
        """
        Asynchronously processes a single document, transforming it into a
        graph document.
        """
        logging.info("🔄 LLMGraphTransformer aprocess_response (ASYNC) - delegating to sync process_response")
        
        # Async fonksiyonu sync'e yönlendir - aynı mantığı kullanıyor
        return self.process_response(document, config)

    async def aconvert_to_graph_documents(
        self, documents: Sequence[Document], config: Optional[RunnableConfig] = None
    ) -> List[GraphDocument]:
        """
        Asynchronously convert a sequence of documents into graph documents.
        """
        tasks = [
            asyncio.create_task(self.aprocess_response(document, config))
            for document in documents
        ]
        results = await asyncio.gather(*tasks)
        return results
