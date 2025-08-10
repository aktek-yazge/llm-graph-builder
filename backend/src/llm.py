import logging
from langchain.docstore.document import Document
import os
from langchain_openai import ChatOpenAI, AzureChatOpenAI
from langchain_google_vertexai import ChatVertexAI
from langchain_groq import ChatGroq
from langchain_google_vertexai import HarmBlockThreshold, HarmCategory
from langchain_experimental.graph_transformers.diffbot import DiffbotGraphTransformer
from langchain_experimental.graph_transformers import LLMGraphTransformer
from langchain_anthropic import ChatAnthropic
from langchain_fireworks import ChatFireworks
from langchain_aws import ChatBedrock
from langchain_community.chat_models import ChatOllama
import boto3
import google.auth
from src.shared.constants import ADDITIONAL_INSTRUCTIONS, POST_PROCESSING_PROMPT
from src.shared.llm_graph_builder_exception import LLMGraphBuilderException
import re
from typing import List
import json

def get_llm(model: str):
    """Retrieve the specified language model based on the model name."""
    model = model.lower().strip()
    env_key = f"LLM_MODEL_CONFIG_{model}"
    print("env_key",env_key)
    env_value = os.environ.get(env_key)

    if not env_value:
        err = f"Environment variable '{env_key}' is not defined as per format or missing"
        logging.error(err)
        raise Exception(err)
    
    logging.info("Model: {}".format(env_key))
    logging.info(f"get_llm çağrısı: model={model}, env_key={env_key}, env_value={env_value}")
    
    try:
        if "gemini" in model:
            model_name = env_value
            credentials, project_id = google.auth.default()
            llm = ChatVertexAI(
                model_name=model_name,
                #convert_system_message_to_human=True,
                credentials=credentials,
                project=project_id,
                temperature=0,
                safety_settings={
                    HarmCategory.HARM_CATEGORY_UNSPECIFIED: HarmBlockThreshold.BLOCK_NONE,
                    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
                    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                },
            )
        elif "openai" in model:
            model_name, api_key = env_value.split(",")
            logging.info(f"OpenAI model kontrolü: model={model}, model_name={model_name}")
            if "o3-mini" in model:
                logging.info("O3-mini tespit edildi, temperature parametresi olmadan LLM oluşturuluyor")
                llm= ChatOpenAI(
                api_key=api_key,
                model=model_name)
            else:
                logging.info("Normal OpenAI model, temperature=0 ile LLM oluşturuluyor")
                llm = ChatOpenAI(
                api_key=api_key,
                model=model_name,
                temperature=0,
                )

        elif "azure" in model:
            model_name, api_endpoint, api_key, api_version = env_value.split(",")
            llm = AzureChatOpenAI(
                api_key=api_key,
                azure_endpoint=api_endpoint,
                azure_deployment=model_name,  # takes precedence over model parameter
                api_version=api_version,
                temperature=0,
                max_tokens=None,
                timeout=None,
            )

        elif "anthropic" in model:
            model_name, api_key = env_value.split(",")
            llm = ChatAnthropic(
                api_key=api_key, model=model_name, temperature=0, timeout=None
            )

        elif "fireworks" in model:
            model_name, api_key = env_value.split(",")
            llm = ChatFireworks(api_key=api_key, model=model_name)

        elif "groq" in model:
            model_name, base_url, api_key = env_value.split(",")
            llm = ChatGroq(api_key=api_key, model_name=model_name, temperature=0)

        elif "bedrock" in model:
            model_name, aws_access_key, aws_secret_key, region_name = env_value.split(",")
            bedrock_client = boto3.client(
                service_name="bedrock-runtime",
                region_name=region_name,
                aws_access_key_id=aws_access_key,
                aws_secret_access_key=aws_secret_key,
            )

            llm = ChatBedrock(
                client=bedrock_client,region_name=region_name, model_id=model_name, model_kwargs=dict(temperature=0)
            )

        elif "ollama" in model:
            model_name, base_url = env_value.split(",")
            llm = ChatOllama(base_url=base_url, model=model_name)

        elif "diffbot" in model:
            #model_name = "diffbot"
            model_name, api_key = env_value.split(",")
            llm = DiffbotGraphTransformer(
                diffbot_api_key=api_key,
                extract_types=["entities", "facts"],
            )
        
        else: 
            model_name, api_endpoint, api_key = env_value.split(",")
            llm = ChatOpenAI(
                api_key=api_key,
                base_url=api_endpoint,
                model=model_name,
                temperature=0,
            )
    except Exception as e:
        err = f"Error while creating LLM '{model}': {str(e)}"
        logging.error(err)
        raise Exception(err)
 
    logging.info(f"Model created - Model Version: {model}")
    return llm, model_name

def get_llm_model_name(llm):
    """Extract name of llm model from llm object"""
    for attr in ["model_name", "model", "model_id"]:
        model_name = getattr(llm, attr, None)
        if model_name:
            return model_name.lower()
    print("Could not determine model name; defaulting to empty string")
    return ""

def get_combined_chunks(chunkId_chunkDoc_list, chunks_to_combine):
    combined_chunk_document_list = []
    combined_chunks_page_content = [
        "".join(
            document["chunk_doc"].page_content
            for document in chunkId_chunkDoc_list[i : i + chunks_to_combine]
        )
        for i in range(0, len(chunkId_chunkDoc_list), chunks_to_combine)
    ]
    combined_chunks_ids = [
        [
            document["chunk_id"]
            for document in chunkId_chunkDoc_list[i : i + chunks_to_combine]
        ]
        for i in range(0, len(chunkId_chunkDoc_list), chunks_to_combine)
    ]

    for i in range(len(combined_chunks_page_content)):
        combined_chunk_document_list.append(
            Document(
                page_content=combined_chunks_page_content[i],
                metadata={"combined_chunk_ids": combined_chunks_ids[i]},
            )
        )
    return combined_chunk_document_list

def get_chunk_id_as_doc_metadata(chunkId_chunkDoc_list):
    combined_chunk_document_list = [
       Document(
           page_content=document["chunk_doc"].page_content,
           metadata={"chunk_id": [document["chunk_id"]]},
       )
       for document in chunkId_chunkDoc_list
   ]
    return combined_chunk_document_list
      

async def get_graph_document_list(
    llm, combined_chunk_document_list, allowedNodes, allowedRelationship, additional_instructions=None
):
    if additional_instructions:
        additional_instructions = sanitize_additional_instruction(additional_instructions)
    graph_document_list = []
    if "diffbot_api_key" in dir(llm):
        llm_transformer = llm
    else:
        if "get_name" in dir(llm) and llm.get_name() != "ChatOpenAI" or llm.get_name() != "ChatVertexAI" or llm.get_name() != "AzureChatOpenAI":
            node_properties = False
            relationship_properties = False
        else:
            node_properties = ["description"]
            relationship_properties = ["description"]
        TOOL_SUPPORTED_MODELS = {"qwen3", "deepseek"} 
        model_name = get_llm_model_name(llm)
        ignore_tool_usage = not any(pattern in model_name for pattern in TOOL_SUPPORTED_MODELS)
        logging.info(f"Keeping ignore tool usage parameter as {ignore_tool_usage}")
        llm_transformer = LLMGraphTransformer(
            llm=llm,
            node_properties=node_properties,
            relationship_properties=relationship_properties,
            allowed_nodes=allowedNodes,
            allowed_relationships=allowedRelationship,
            ignore_tool_usage=ignore_tool_usage,
            additional_instructions=ADDITIONAL_INSTRUCTIONS+ (additional_instructions if additional_instructions else "")
        )
    
    if isinstance(llm,DiffbotGraphTransformer):
        graph_document_list = llm_transformer.convert_to_graph_documents(combined_chunk_document_list)
    else:
        graph_document_list = await llm_transformer.aconvert_to_graph_documents(combined_chunk_document_list)
    return graph_document_list

async def get_graph_from_llm(model, chunkId_chunkDoc_list, allowedNodes, allowedRelationship, chunks_to_combine, file_name=None, additional_instructions=None, graph=None):
   try:
       llm, model_name = get_llm(model)
       logging.info(f"Using model: {model_name}")
    
       combined_chunk_document_list = get_combined_chunks(chunkId_chunkDoc_list, chunks_to_combine)
       logging.info(f"Combined {len(combined_chunk_document_list)} chunks")
    
       allowed_nodes = [node.strip() for node in allowedNodes.split(',') if node.strip()]
       logging.info(f"Allowed nodes: {allowed_nodes}")
    
       allowed_relationships = []
       if allowedRelationship:
           items = [item.strip() for item in allowedRelationship.split(',') if item.strip()]
           if len(items) % 3 != 0:
               raise LLMGraphBuilderException("allowedRelationship must be a multiple of 3 (source, relationship, target)")
           for i in range(0, len(items), 3):
               source, relation, target = items[i:i + 3]
               if source not in allowed_nodes or target not in allowed_nodes:
                   raise LLMGraphBuilderException(
                       f"Invalid relationship ({source}, {relation}, {target}): "
                       f"source or target not in allowedNodes"
                   )
               allowed_relationships.append((source, relation, target))
           logging.info(f"Allowed relationships: {allowed_relationships}")
       else:
           logging.info("No allowed relationships provided")

       graph_document_list = await get_graph_document_list(
           llm,
           combined_chunk_document_list,
           allowed_nodes,
           allowed_relationships,
           additional_instructions
       )
       logging.info(f"Generated {len(graph_document_list)} graph documents")
       return graph_document_list
   except Exception as e:
       logging.error(f"Error in get_graph_from_llm: {e}", exc_info=True)
       raise LLMGraphBuilderException(f"Error in getting graph from llm: {e}")

def sanitize_additional_instruction(instruction: str) -> str:
   """
   Sanitizes additional instruction by:
   - Replacing curly braces `{}` with `[]` to prevent variable interpretation.
   - Removing potential injection patterns like `os.getenv()`, `eval()`, `exec()`.
   - Stripping problematic special characters.
   - Normalizing whitespace.
   Args:
       instruction (str): Raw additional instruction input.
   Returns:
       str: Sanitized instruction safe for LLM processing.
   """
   logging.info("Sanitizing additional instructions")
   instruction = instruction.replace("{", "[").replace("}", "]")  # Convert `{}` to `[]` for safety
   # Step 2: Block dangerous function calls
   injection_patterns = [r"os\.getenv\(", r"eval\(", r"exec\(", r"subprocess\.", r"import os", r"import subprocess"]
   for pattern in injection_patterns:
       instruction = re.sub(pattern, "[BLOCKED]", instruction, flags=re.IGNORECASE)
   # Step 4: Normalize spaces
   instruction = re.sub(r'\s+', ' ', instruction).strip()
   return instruction


def extract_json_from_response(response_text):
    """
    LLM response'undan JSON kısmını çıkarır.
    LLM bazen JSON'dan önce/sonra açıklama ekler, bunları temizler.
    """
    try:
        # Response'u temizle
        response_text = response_text.strip()
        
        logging.info(f"JSON extraction başlıyor. Response uzunluğu: {len(response_text)}")
        logging.info(f"Response başlangıcı: {response_text[:200]}...")
        
        # Eğer direkt JSON ise (hiç açıklama yoksa)
        if response_text.startswith('{') and response_text.endswith('}'):
            json.loads(response_text)  # Validate
            logging.info("✅ Response zaten temiz JSON formatında")
            return response_text
        
        # JSON başlangıcını bul
        json_start = -1
        for i, char in enumerate(response_text):
            if char == '{':
                json_start = i
                break
        
        if json_start == -1:
            # Eğer { bulunamazsa, belki array formatında dönmüştür
            json_start = response_text.find('[')
            if json_start == -1:
                logging.error("JSON başlangıcı bulunamadı")
                raise ValueError("JSON başlangıcı bulunamadı")
        
        # JSON sonunu bul
        brace_count = 0
        json_end = -1
        start_char = response_text[json_start]
        end_char = '}' if start_char == '{' else ']'
        
        for i in range(json_start, len(response_text)):
            char = response_text[i]
            if char == start_char:
                brace_count += 1
            elif char == end_char:
                brace_count -= 1
                if brace_count == 0:
                    json_end = i + 1
                    break
        
        if json_end == -1:
            logging.error("JSON sonu bulunamadı")
            raise ValueError("JSON sonu bulunamadı")
        
        # JSON kısmını çıkar
        json_text = response_text[json_start:json_end]
        
        logging.info(f"Çıkarılan JSON uzunluğu: {len(json_text)}")
        logging.info(f"Çıkarılan JSON başlangıcı: {json_text[:200]}...")
        
        # JSON'u validate et
        parsed = json.loads(json_text)
        logging.info("✅ JSON başarıyla parse edildi")
        
        return json_text
        
    except Exception as e:
        logging.error(f"JSON extraction hatası: {e}")
        logging.error(f"Response text (ilk 1000 karakter): {response_text[:1000]}...")
        # Son çare olarak orijinal text'i döndür
        return response_text


def apply_dynamic_entity_post_processing(graph, rules_list, target_file_names=None):
    """
    Dinamik entity relationship post-processing fonksiyonu.
    Kullanıcının belirlediği kurallara göre entity'leri target node'lara bağlar.
    
    Args:
        graph: Neo4j graph objesi
        rules_list: Post-processing kurallarının listesi
        target_file_names: İşlenecek dosya isimlerinin listesi (None ise tüm dosyalar)
        
    Returns:
        İşlem sonucu raporu
    """
    try:
        logging.info(f"Dinamik entity post-processing başlıyor")
        if target_file_names:
            logging.info(f"Hedef dosyalar: {target_file_names}")
        else:
            logging.info("Tüm dosyalar üzerinde global işlem")
        
        # Document node'larını al - hedef dosyalara göre filtrele
        try:
            if target_file_names:
                # Sadece belirtilen dosyalar için Document node'ları al
                all_docs_query = """
                MATCH (d:Document) 
                WHERE d.fileName IN $target_files
                RETURN d.fileName as fileName, 
                       d.id as documentId, 
                       elementId(d) as elementId,
                       d.fileSource as fileSource,
                       d.status as status,
                       d.url as url,
                       properties(d) as allProperties
                ORDER BY d.fileName
                """
                processing_docs_result = graph.query(all_docs_query, params={"target_files": target_file_names})
                logging.info(f"Hedef dosyalar için Document sayısı: {len(processing_docs_result)}")
            else:
                # Tüm Document node'larını al (eski davranış)
                all_docs_query = """
                MATCH (d:Document) 
                RETURN d.fileName as fileName, 
                       d.id as documentId, 
                       elementId(d) as elementId,
                       d.fileSource as fileSource,
                       d.status as status,
                       d.url as url,
                       properties(d) as allProperties
                ORDER BY d.fileName
                """
                processing_docs_result = graph.query(all_docs_query)
                logging.info(f"Graph'daki toplam Document sayısı: {len(processing_docs_result)}")
            
            # Status dağılımını göster
            status_distribution = {}
            for doc in processing_docs_result:
                status = doc.get('status', 'NULL')
                status_distribution[status] = status_distribution.get(status, 0) + 1
            
            logging.info(f"Document status dağılımı: {status_distribution}")
            
            logging.info(f"İşlenecek Document node'ları:")
            for doc in processing_docs_result:  # TÜM dosyaları logla
                logging.info(f"  - fileName: {doc.get('fileName')}")
                logging.info(f"    status: {doc.get('status')}")
                logging.info(f"    documentId: {doc.get('documentId')}")
                logging.info(f"    elementId: {doc.get('elementId')}")
                logging.info(f"    fileSource: {doc.get('fileSource')}")
                logging.info("  ---")
            
            processing_files = [row['fileName'] for row in processing_docs_result if row['fileName']]
            document_id_map = {
                row['fileName']: {
                    'documentId': row.get('documentId'),
                    'elementId': row.get('elementId'),
                    'fileSource': row.get('fileSource'),
                    'status': row.get('status'),
                    'url': row.get('url'),
                    'properties': row.get('allProperties', {})
                }
                for row in processing_docs_result if row['fileName']
            }
            
            logging.info(f"İşlenecek dosya sayısı: {len(processing_files)}")
            logging.info(f"İşlenecek dosya isimleri:")
            for i, file_name in enumerate(processing_files, 1):
                logging.info(f"  {i}. {file_name}")
            logging.info(f"Document ID mapping hazırlandı: {len(document_id_map)} dosya")
            
        except Exception as e:
            logging.error(f"Document node'ları alma hatası: {e}")
            processing_files = []
            document_id_map = {}
        
        logging.info(f"Kural sayısı: {len(rules_list)}")
        
        if not rules_list:
            logging.warning("Post-processing kuralları boş")
            return {"status": "warning", "message": "No rules provided", "processed_files": 0, "applied_rules": 0}
        
        if not processing_files:
            logging.warning("Hiç Document node'u bulunamadı")
            return {"status": "warning", "message": "No documents found in the graph", "processed_files": 0, "applied_rules": 0}
        
        total_processed = 0
        total_relationships_created = 0
        rule_results = []
        
        # Her kural için tüm graf üzerinde işlem yap (dosya bazında değil, global)
        for rule_index, rule in enumerate(rules_list):
            try:
                # Frontend formatını destekle
                if 'sourceNodeType' in rule:
                    # Yeni frontend format
                    source_node_type = rule.get('sourceNodeType')
                    target_node_type = rule.get('targetNodeType')
                    relationship_type = rule.get('relationshipType')
                    relationship_types = [relationship_type] if relationship_type else []
                    remove_existing = rule.get('removeExistingRelationships', False)
                    target_selection = 'document'  # Frontend sadece document target destekliyor
                else:
                    # Eski backend format (backward compatibility)
                    source_node_type = rule.get('source_node_type')
                    target_node_type = rule.get('target_node_type') 
                    relationship_types = rule.get('relationship_types', [])
                    target_selection = rule.get('target_selection', 'document')
                    remove_existing = rule.get('remove_existing_relationships', False)
                    
                logging.info(f"Kural {rule_index + 1} uygulanıyor: {source_node_type} -> {target_node_type}")
                logging.info(f"İlişki tipi: {relationship_types}")
                logging.info(f"Mevcut ilişkileri sil: {remove_existing}")
                logging.info(f"Target selection: {target_selection} (Document mı: {target_node_type == 'Document'})")
                
                # Validation
                if not source_node_type or not target_node_type or not relationship_types:
                    logging.warning(f"Eksik kural parametreleri: source={source_node_type}, target={target_node_type}, rels={relationship_types}")
                    continue
                
                rule_processed = 0
                rule_relationships = 0
                
                # Mevcut ilişkileri sil (eğer istenirse) - Sadece işlenen dosyaların Document node'ları için
                if remove_existing:
                    for rel_type in relationship_types:
                        if target_node_type == 'Document':
                            # İşlenen dosyaların Document node'larını hedefle
                            file_names = list(document_id_map.keys())
                            remove_query = f"""
                            MATCH (s)-[r:{rel_type}]->(t:Document)
                            WHERE $source_type IN labels(s) 
                              AND t.fileName IN $file_names
                            DELETE r
                            RETURN COUNT(r) as deleted
                            """
                            
                            remove_result = graph.query(remove_query, params={
                                "source_type": source_node_type,
                                "file_names": file_names
                            })
                        else:
                            # Entity-to-entity relationships için genel temizlik
                            remove_query = f"""
                            MATCH (s)-[r:{rel_type}]->(t)
                            WHERE $source_type IN labels(s) 
                              AND $target_type IN labels(t)
                            DELETE r
                            RETURN COUNT(r) as deleted
                            """
                            
                            remove_result = graph.query(remove_query, params={
                                "source_type": source_node_type,
                                "target_type": target_node_type
                            })
                        
                        try:
                            deleted_count = remove_result[0]['deleted'] if remove_result else 0
                            logging.info(f"Silinen mevcut {rel_type} ilişkileri: {deleted_count}")
                            
                        except Exception as remove_error:
                            logging.error(f"Mevcut ilişkileri silme hatası: {remove_error}")
                
                # Tüm graf üzerinde source ve target node'ları eşleştir ve relationship oluştur
                for rel_type in relationship_types:
                    try:
                        # Dinamik sorgu oluştur - target_node_type'a göre
                        if target_node_type == 'Document':
                            # Document target ise, SADECE işlenen dosyanın Document node'unu hedefle
                            # Önce işlenen dosyanın Document ID'sini al
                            target_doc_query = """
                            MATCH (target_doc:Document)
                            WHERE target_doc.fileName IN $file_names
                            RETURN target_doc.fileName as fileName, 
                                   elementId(target_doc) as documentElementId,
                                   target_doc.id as documentId
                            """
                            
                            # İşlenen dosya isimlerini al
                            file_names = list(document_id_map.keys())
                            target_docs_result = graph.query(target_doc_query, params={"file_names": file_names})
                            
                            if not target_docs_result:
                                logging.warning(f"İşlenen dosyalar için Document node bulunamadı: {file_names}")
                                continue
                            
                            # GÜVENL İ DOCUMENT SEÇİM İ: Sadece işlenen dosyayla eşleşeni seç
                            target_doc_info = None
                            for doc_result in target_docs_result:
                                doc_file_name = doc_result['fileName']
                                if any(target_file in doc_file_name or doc_file_name in target_file for target_file in file_names):
                                    target_doc_info = doc_result
                                    break
                            
                            if not target_doc_info:
                                logging.error(f"Hedef dosya için Document node bulunamadı! Aranan: {file_names}, Bulunan: {[r['fileName'] for r in target_docs_result]}")
                                continue
                            
                            target_doc_element_id = target_doc_info['documentElementId']
                            target_file_name = target_doc_info['fileName']
                            
                            logging.info(f"Hedef Document node: {target_file_name} (ID: {target_doc_element_id})")
                            logging.info(f"Aranan dosya isimleri: {file_names}")
                            logging.info(f"Seçilen Document eşleşmesi: {target_file_name}")
                            
                            # Önce yapıyı analiz edelim - Graph'daki entity'leri incele
                            structure_analysis_query = f"""
                            MATCH (target_doc:Document)<-[:PART_OF]-(c:Chunk)-[:HAS_ENTITY]->(e)
                            WHERE elementId(target_doc) = $target_doc_id
                            RETURN COUNT(DISTINCT e) as total_entities,
                                   COUNT(DISTINCT c) as total_chunks,
                                   COLLECT(DISTINCT labels(e)) as all_entity_labels,
                                   COLLECT(DISTINCT e.id)[0..5] as sample_entity_ids,
                                   COLLECT(DISTINCT e.name)[0..5] as sample_entity_names,
                                   COLLECT(DISTINCT {{
                                     entityId: e.id,
                                     entityName: e.name,
                                     entityLabels: labels(e),
                                     chunkId: c.id
                                   }})[0..10] as entity_chunk_mapping
                            """
                            
                            analysis_result = graph.query(structure_analysis_query, params={"target_doc_id": target_doc_element_id})
                            
                            if analysis_result:
                                analysis = analysis_result[0]
                                logging.info(f"🔍 Graph yapısı analizi:")
                                logging.info(f"  - Toplam entity sayısı: {analysis['total_entities']}")
                                logging.info(f"  - Toplam chunk sayısı: {analysis['total_chunks']}")
                                logging.info(f"  - Tüm entity label'ları: {analysis['all_entity_labels']}")
                                logging.info(f"  - Örnek entity ID'leri: {analysis['sample_entity_ids']}")
                                logging.info(f"  - Örnek entity isimleri: {analysis['sample_entity_names']}")
                                logging.info(f"  - Entity-Chunk mapping örnekleri:")
                                for mapping in analysis['entity_chunk_mapping']:
                                    logging.info(f"    * Entity: {mapping['entityId']} ({mapping['entityName']}) - Labels: {mapping['entityLabels']} - Chunk: {mapping['chunkId']}")
                            
                            # Spesifik source_type entity'lerini kontrol et
                            source_type_analysis_query = f"""
                            MATCH (target_doc:Document)<-[:PART_OF]-(c:Chunk)-[:HAS_ENTITY]->(e)
                            WHERE elementId(target_doc) = $target_doc_id
                              AND $source_type IN labels(e)
                            RETURN COUNT(e) as matching_entities,
                                   COLLECT(DISTINCT {{
                                     entityId: e.id,
                                     entityName: e.name,
                                     entityLabels: labels(e),
                                     chunkId: c.id,
                                     chunkText: substring(c.text, 0, 100) + "..."
                                   }})[0..5] as matching_entity_details
                            """
                            
                            source_analysis_result = graph.query(source_type_analysis_query, params={
                                "target_doc_id": target_doc_element_id,
                                "source_type": source_node_type
                            })
                            
                            if source_analysis_result:
                                source_analysis = source_analysis_result[0]
                                logging.info(f"🎯 {source_node_type} entity analizi:")
                                logging.info(f"  - Eşleşen {source_node_type} entity sayısı: {source_analysis['matching_entities']}")
                                logging.info(f"  - {source_node_type} entity detayları:")
                                for entity_detail in source_analysis['matching_entity_details']:
                                    logging.info(f"    * ID: {entity_detail['entityId']}")
                                    logging.info(f"      Name: {entity_detail['entityName']}")
                                    logging.info(f"      Labels: {entity_detail['entityLabels']}")
                                    logging.info(f"      Chunk ID: {entity_detail['chunkId']}")
                                    logging.info(f"      Chunk Text: {entity_detail['chunkText']}")
                                    logging.info(f"      ---")
                            
                            # Mevcut relationship'leri kontrol et
                            existing_rel_check_query = f"""
                            MATCH (target_doc:Document)<-[:PART_OF]-(c:Chunk)-[:HAS_ENTITY]->(e)
                            WHERE elementId(target_doc) = $target_doc_id
                              AND $source_type IN labels(e)
                            OPTIONAL MATCH (e)-[existing_rel:{rel_type}]->(target_doc)
                            RETURN COUNT(e) as total_source_entities,
                                   COUNT(existing_rel) as existing_relationships,
                                   COLLECT(CASE WHEN existing_rel IS NOT NULL THEN {{
                                     entityId: e.id,
                                     entityName: e.name,
                                     relationshipType: type(existing_rel),
                                     targetFileName: target_doc.fileName
                                   }} END) as entities_with_existing_rel,
                                   COLLECT(CASE WHEN existing_rel IS NULL THEN {{
                                     entityId: e.id,
                                     entityName: e.name,
                                     entityLabels: labels(e)
                                   }} END) as entities_without_rel
                            """
                            
                            existing_rel_result = graph.query(existing_rel_check_query, params={
                                "target_doc_id": target_doc_element_id,
                                "source_type": source_node_type
                            })
                            
                            if existing_rel_result:
                                rel_check = existing_rel_result[0]
                                logging.info(f"🔗 Mevcut {rel_type} relationship durumu:")
                                logging.info(f"  - Toplam {source_node_type} entity: {rel_check['total_source_entities']}")
                                logging.info(f"  - Mevcut {rel_type} relationship: {rel_check['existing_relationships']}")
                                
                                entities_with_rel = [x for x in rel_check['entities_with_existing_rel'] if x]
                                entities_without_rel = [x for x in rel_check['entities_without_rel'] if x]
                                
                                if entities_with_rel:
                                    logging.info(f"  - Zaten {rel_type} relationship'i olan entity'ler:")
                                    for entity in entities_with_rel[:3]:
                                        logging.info(f"    * {entity['entityId']} ({entity['entityName']}) -> {entity['targetFileName']}")
                                
                                if entities_without_rel:
                                    logging.info(f"  - {rel_type} relationship'i olmayan entity'ler:")
                                    for entity in entities_without_rel[:3]:
                                        logging.info(f"    * {entity['entityId']} ({entity['entityName']}) - Labels: {entity['entityLabels']}")
                                else:
                                    logging.warning(f"  ⚠️ Hiç relationship olmayan {source_node_type} entity bulunamadı!")
                            
                            # DETAYLI DUPLICATE ENTITY KONTROLÜ VE ANALİZİ
                            duplicate_entity_analysis_query = f"""
                            MATCH (target_doc:Document)<-[:PART_OF]-(c:Chunk)-[:HAS_ENTITY]->(e)
                            WHERE elementId(target_doc) = $target_doc_id
                              AND $source_type IN labels(e)
                            WITH e, target_doc, 
                                 COLLECT(DISTINCT c.id) as chunk_ids,
                                 COUNT(DISTINCT c) as chunk_count
                            RETURN e.id as entityId,
                                   e.name as entityName,
                                   elementId(e) as entityElementId,
                                   labels(e) as entityLabels,
                                   chunk_ids,
                                   chunk_count,
                                   properties(e) as entityProperties,
                                   EXISTS((e)-[:{rel_type}]->(target_doc)) as has_existing_relationship
                            ORDER BY e.name, e.id
                            """
                            
                            duplicate_analysis_result = graph.query(duplicate_entity_analysis_query, params={
                                "target_doc_id": target_doc_element_id,
                                "source_type": source_node_type
                            })
                            
                            if duplicate_analysis_result:
                                logging.info(f"🔍 DETAYLI {source_node_type} ENTITY ANALİZİ:")
                                entity_id_groups = {}
                                for entity in duplicate_analysis_result:
                                    # Entity ID'sine göre grupla (name null olabilir)
                                    entity_id = entity['entityId'] 
                                    entity_name = entity['entityName'] or f"ID_{entity_id}"  # null ise ID kullan
                                    
                                    if entity_id not in entity_id_groups:
                                        entity_id_groups[entity_id] = entity
                                
                                # Her entity için ayrı log
                                for entity_id, entity in entity_id_groups.items():
                                    entity_name = entity['entityName'] or f"ID_{entity_id}"
                                    logging.info(f"  ✅ ENTITY - Name: '{entity_name}' (ID: {entity_id})")
                                    logging.info(f"    ElementID: {entity['entityElementId']}")
                                    logging.info(f"    Labels: {entity['entityLabels']}")
                                    logging.info(f"    Chunk IDs: {entity['chunk_ids']}")
                                    logging.info(f"    Has Relationship: {entity['has_existing_relationship']}")
                                    logging.info(f"    Properties: {entity['entityProperties']}")
                                
                                # Gerçek duplicate kontrolü (aynı ID'ye sahip birden fazla entity)
                                duplicate_count = len(duplicate_analysis_result) - len(entity_id_groups)
                                if duplicate_count > 0:
                                    logging.warning(f"  ⚠️ GERÇEK DUPLICATE VAR: {duplicate_count} adet tekrar eden entity ID'si")
                                else:
                                    logging.info(f"  ✅ DUPLICATE YOK: {len(entity_id_groups)} unique entity bulundu")
                            
                            # SADECE bu Document node'unu hedefle ve mevcut Document node'unu güncelleme
                            # Entity'ler SADECE kendi chunk'larından geldikleri Document'a bağlanmalı
                            # HER UNIQUE ENTITY ID İÇİN relationship oluştur (name'e değil ID'ye bak)
                            create_relationships_query = f"""
                            MATCH (target_doc:Document)<-[:PART_OF]-(c:Chunk)-[:HAS_ENTITY]->(e)
                            WHERE elementId(target_doc) = $target_doc_id
                              AND $source_type IN labels(e)
                              AND NOT EXISTS((e)-[:{rel_type}]->(target_doc))
                            // Her unique entity ID için relationship oluştur (name değil ID önemli)
                            MERGE (e)-[r:{rel_type}]->(target_doc)
                            SET r.created_by = 'post_processing'
                            SET r.created_at = datetime()
                            RETURN COUNT(r) as created_relationships, 
                                   COUNT(DISTINCT e) as processed_entities,
                                   COUNT(DISTINCT target_doc) as target_documents,
                                   COLLECT(DISTINCT {{ 
                                     fileName: target_doc.fileName, 
                                     documentId: target_doc.id, 
                                     elementId: elementId(target_doc),
                                     fileSource: target_doc.fileSource,
                                     status: target_doc.status
                                   }})[0..1] as sample_documents,
                                   COLLECT(DISTINCT {{
                                     entityId: e.id,
                                     entityName: COALESCE(e.name, 'ID_' + e.id),
                                     entityElementId: elementId(e),
                                     relationshipType: '{rel_type}'
                                   }}) as processed_entity_details
                            """
                            
                            # Sorguyu çalıştır
                            result = graph.query(create_relationships_query, params={
                                "source_type": source_node_type,
                                "target_doc_id": target_doc_element_id
                            })
                            
                        else:
                            # Diğer entity tiplerine bağlanma - aynı Document'tan gelen entity'ler arası
                            create_relationships_query = f"""
                            MATCH (d:Document)<-[:PART_OF]-(c1:Chunk)-[:HAS_ENTITY]->(e1)
                            MATCH (d)<-[:PART_OF]-(c2:Chunk)-[:HAS_ENTITY]->(e2)
                            WHERE $source_type IN labels(e1) 
                              AND $target_type IN labels(e2)
                              AND e1 <> e2
                            WITH e1, e2, d
                            MERGE (e1)-[r:{rel_type}]->(e2)
                            RETURN COUNT(r) as created_relationships, 
                                   COUNT(DISTINCT e1) as processed_entities,
                                   COUNT(DISTINCT d) as target_documents,
                                   COLLECT(DISTINCT {{ 
                                     fileName: d.fileName, 
                                     documentId: d.id, 
                                     elementId: elementId(d),
                                     fileSource: d.fileSource,
                                     status: d.status
                                   }})[0..5] as sample_documents
                            """
                            
                            # Sorguyu çalıştır
                            result = graph.query(create_relationships_query, params={
                                "source_type": source_node_type,
                                "target_type": target_node_type
                            })
                        
                        logging.debug(f"Relationship oluşturma sorgusu ({target_node_type} target): {create_relationships_query}")
                        logging.debug(f"Parametreler: source_type={source_node_type}, target_type={target_node_type}")
                        
                        if result:
                            created_rels = result[0]['created_relationships']
                            processed_entities = result[0]['processed_entities'] 
                            target_docs = result[0]['target_documents']
                            sample_docs = result[0]['sample_documents']
                            processed_entity_details = result[0].get('processed_entity_details', [])
                            
                            rule_processed += processed_entities
                            rule_relationships += created_rels
                            
                            logging.info(f"📊 Relationship oluşturma sonuçları:")
                            logging.info(f"  - İşlenen entity sayısı: {processed_entities}")
                            logging.info(f"  - Oluşturulan relationship sayısı: {created_rels}")
                            logging.info(f"  - Hedeflenen Document sayısı: {target_docs}")
                            
                            # İşlenen entity'lerin detaylarını logla
                            if processed_entity_details:
                                logging.info(f"  - İşlenen entity detayları:")
                                for detail in processed_entity_details:
                                    logging.info(f"    * {detail['entityName']} (ID: {detail['entityId']}, ElementID: {detail['entityElementId']}) -> {detail['relationshipType']}")
                            
                            if target_node_type == 'Document':
                                if created_rels == 0 and processed_entities > 0:
                                    logging.error(f"❌ SORUN: {processed_entities} {source_node_type} entity bulundu ama hiç {rel_type} relationship oluşturulamadı!")
                                    
                                    # Kısa debug - sadece kritik bilgiler
                                    debug_query = f"""
                                    MATCH (target_doc:Document)<-[:PART_OF]-(c:Chunk)-[:HAS_ENTITY]->(e)
                                    WHERE elementId(target_doc) = $target_doc_id
                                      AND $source_type IN labels(e)
                                    RETURN COUNT(e) as total_entities,
                                           COUNT(CASE WHEN EXISTS((e)-[:{rel_type}]->(target_doc)) THEN 1 END) as entities_with_existing_rel,
                                           COUNT(CASE WHEN NOT EXISTS((e)-[:{rel_type}]->(target_doc)) THEN 1 END) as entities_without_rel
                                    """
                                    debug_result = graph.query(debug_query, params={
                                        "target_doc_id": target_doc_element_id,
                                        "source_type": source_node_type
                                    })
                                    if debug_result:
                                        debug = debug_result[0]
                                        logging.error(f"  🔍 Kısa Debug:")
                                        logging.error(f"    - Toplam {source_node_type}: {debug['total_entities']}")
                                        logging.error(f"    - Mevcut rel. olan: {debug['entities_with_existing_rel']}")
                                        logging.error(f"    - Rel. olmayan: {debug['entities_without_rel']}")
                                    
                                elif created_rels != processed_entities:
                                    logging.warning(f"⚠️ UYARI: {processed_entities} entity bulundu ama sadece {created_rels} relationship oluşturuldu!")
                                    logging.warning(f"  Bu genellikle duplicate entity'ler veya mevcut relationship'ler nedeniyle olur.")
                                elif created_rels > 0:
                                    logging.info(f"✅ Başarılı: {created_rels} {rel_type} relationship oluşturuldu!")
                                
                                logging.info(f"Kural {rule_index + 1} - {rel_type}: {processed_entities} source entity işlendi, {created_rels} relationship oluşturuldu, {target_docs} Document hedeflendi")
                            else:
                                logging.info(f"Kural {rule_index + 1} - {rel_type}: {processed_entities} source entity işlendi, {created_rels} relationship oluşturuldu ({source_node_type} -> {target_node_type})")
                            
                            logging.info(f"İşlenen Document örnekleri: {sample_docs}")
                            
                            # Document ID mapping'den detay bilgi al
                            for sample_doc in sample_docs:
                                file_name = sample_doc.get('fileName')
                                if file_name in document_id_map:
                                    doc_info = document_id_map[file_name]
                                    logging.info(f"  Document: {file_name}")
                                    logging.info(f"    Status: {sample_doc.get('status')}")
                                    logging.info(f"    ID: {doc_info.get('documentId')}")
                                    logging.info(f"    ElementID: {doc_info.get('elementId')}")
                                    logging.info(f"    Source: {doc_info.get('fileSource')}")
                        else:
                            logging.error(f"❌ Relationship sorgusu hiç sonuç döndürmedi!")
                            logging.error(f"  - Source type: {source_node_type}")
                            logging.error(f"  - Relationship type: {rel_type}")
                            logging.error(f"  - Target doc ID: {target_doc_element_id}")
                            logging.error(f"  - Query: {create_relationships_query}")
                        
                    except Exception as rel_error:
                        logging.error(f"Relationship oluşturma hatası: {rel_error}")
                        import traceback
                        logging.error(f"Hata detayı:\n{traceback.format_exc()}")
                
                rule_results.append({
                    "rule_index": rule_index + 1,
                    "source_node_type": source_node_type,
                    "target_node_type": target_node_type,
                    "relationship_types": relationship_types,
                    "processed_entities": rule_processed,
                    "created_relationships": rule_relationships
                })
                
                total_processed += rule_processed
                total_relationships_created += rule_relationships
                
                logging.info(f"Kural {rule_index + 1} tamamlandı: {rule_processed} entity, {rule_relationships} relationship")
                        
            except Exception as rule_error:
                logging.error(f"Kural {rule_index + 1} uygulama hatası: {rule_error}")
                continue
        
        # Post-processing sonrası orphan Document node'larını temizle
        try:
            logging.info("Orphan Document node'larını temizleme başlıyor...")
            
            # Hiçbir chunk'a bağlı olmayan Document node'larını bul ve sil
            cleanup_query = """
            MATCH (d:Document)
            WHERE NOT EXISTS((d)<-[:PART_OF]-(:Chunk))
            WITH d, count{(d)<-[:PART_OF]-(:Chunk)} as chunk_count
            WHERE chunk_count = 0
            DETACH DELETE d
            RETURN count(d) as deleted_orphan_documents
            """
            
            cleanup_result = graph.query(cleanup_query)
            deleted_orphans = cleanup_result[0]['deleted_orphan_documents'] if cleanup_result else 0
            
            logging.info(f"Silinen orphan Document node sayısı: {deleted_orphans}")
            
        except Exception as cleanup_error:
            logging.error(f"Orphan Document temizleme hatası: {cleanup_error}")
        
        
        result = {
            "status": "success",
            "message": f"Post-processing completed successfully",
            "total_processed_entities": total_processed,
            "total_created_relationships": total_relationships_created,
            "processed_files": len(processing_files),  # Tüm dosya sayısı
            "applied_rules": len(rules_list),
            "rule_details": rule_results,
            "document_details": {
                "total_documents": len(document_id_map),
                "document_mapping": document_id_map
            }
        }
        
        logging.info(f"✅ Dinamik post-processing tamamlandı: {total_processed} entity, {total_relationships_created} relationship, {len(processing_files)} dosya üzerinde {len(rules_list)} kural uygulandı")
        return result
        
    except Exception as e:
        logging.error(f"❌ Dinamik post-processing hatası: {e}")
        import traceback
        logging.error(f"Hata detayı:\n{traceback.format_exc()}")
        return {
            "status": "error",
            "message": f"Post-processing failed: {str(e)}",
            "total_processed_entities": 0,
            "total_created_relationships": 0,
            "processed_files": 0,
            "applied_rules": 0
        }


def get_document_node_id_from_graph(graph, file_name):
    """
    Neo4j graph'dan Document node'unun ID'sini alır
    Document yapısını detaylı analiz eder
    """
    logging.info(f"Document node aranıyor - fileName: {file_name}")
    logging.info(f"Graph object: {graph is not None}")
    
    try:
        if graph:
            # Önce tüm Document node'larının yapısını analiz et
            analysis_query = """
            MATCH (d:Document) 
            RETURN d.fileName as fileName, 
                   d.id as id, 
                   elementId(d) as element_id, 
                   d.fileSource as fileSource,
                   d.url as url,
                   labels(d) as labels,
                   keys(d) as properties,
                   properties(d) as allProps
            LIMIT 10
            """
            all_docs = graph.query(analysis_query)
            logging.info(f"Graph'daki Document node yapısı analizi:")
            for doc in all_docs:
                logging.info(f"  Document: {doc}")
            
            # Belirli dosyayı ara
            query = """
            MATCH (d:Document {fileName: $fileName})
            RETURN d.id as id, 
                   elementId(d) as element_id, 
                   d.fileName as fileName,
                   d.fileSource as fileSource,
                   d.url as url,
                   properties(d) as allProperties
            LIMIT 1
            """
            logging.debug(f"Document sorgusu çalıştırılıyor: {query}")
            logging.debug(f"Sorgu parametreleri: fileName = '{file_name}'")
            
            result = graph.query(query, params={"fileName": file_name})
            
            logging.info(f"Sorgu sonucu: {result}")
            logging.info(f"Sonuç uzunluğu: {len(result) if result else 0}")
            
            if result and len(result) > 0:
                row = result[0]
                
                # Öncelik sırası: id property > element_id > fileName
                doc_id = row.get('id') or row.get('element_id') or file_name
                
                logging.info(f"✅ Graph'dan Document node detayları:")
                logging.info(f"  fileName: {row.get('fileName')}")
                logging.info(f"  id property: {row.get('id')}")
                logging.info(f"  elementId: {row.get('element_id')}")
                logging.info(f"  fileSource: {row.get('fileSource')}")
                logging.info(f"  url: {row.get('url')}")
                logging.info(f"  allProperties: {row.get('allProperties')}")
                logging.info(f"  Seçilen ID: {doc_id}")
                
                return doc_id
            else:
                logging.warning(f"❌ Document node bulunamadı: fileName='{file_name}'")
                
                # Dosya ismini farklı şekillerde arayalım
                fuzzy_search_query = """
                MATCH (d:Document) 
                WHERE d.fileName CONTAINS $partial_name 
                   OR $partial_name CONTAINS d.fileName
                RETURN d.fileName as fileName, 
                       d.id as id, 
                       elementId(d) as element_id
                LIMIT 5
                """
                fuzzy_result = graph.query(fuzzy_search_query, params={"partial_name": file_name})
                if fuzzy_result:
                    logging.info(f"Benzer dosya isimleri bulundu:")
                    for doc in fuzzy_result:
                        logging.info(f"  - {doc}")
                
        else:
            logging.warning("❌ Graph objesi None")
    except Exception as e:
        logging.error(f"❌ Graph'dan Document node ID alınamadı: {e}")
        import traceback
        logging.error(f"Hata detayı:\n{traceback.format_exc()}")
    
    # Fallback: file name'i kullan
    logging.info(f"🔄 Fallback: file name kullanılıyor: {file_name}")
    return file_name


async def apply_llm_post_processing(model, graph_documents, file_name, graph=None):
    """
    LLM tabanlı post-processing ile entity'leri düzeltir.
    
    Args:
        model: LLM model string
        graph_documents: LLMGraphTransformer'dan gelen GraphDocument listesi 
        file_name: İşlenen dosya adı
        
    Returns:
        Düzeltilmiş GraphDocument listesi
    """
    try:
        logging.info(f"LLM post-processing başlıyor: {file_name}")
        
        # LLM modelini al
        llm, _ = get_llm(model)
        
        # Her GraphDocument için post-processing uygula
        corrected_documents = []
        
        for doc in graph_documents:
            # Orijinal entity'leri say
            original_entities_count = len(doc.nodes)
            original_relationships_count = len(doc.relationships)
            
            logging.info(f"Orijinal GraphDocument: {original_entities_count} entity, {original_relationships_count} relationship")
            
            # Document ID'yi graph'dan al veya file name'den oluştur
            document_node_id = get_document_node_id_from_graph(graph, file_name)
            if document_node_id == file_name:
                # File name'den Document ID oluştur - Neo4j için güvenli format
                document_node_id = file_name.replace(" ", "_").replace(".", "_").replace("/", "_").replace("\\", "_")
            
            logging.info(f"Document node ID: {document_node_id}")
            
            # Mevcut entity'leri JSON formatına çevir
            entities_data = []
            relationships_data = []
            
            # Node'ları ekle
            for node in doc.nodes:
                entities_data.append({
                    "id": node.id,
                    "type": node.type,
                    "properties": dict(node.properties) if node.properties else {}
                })
            
            # Relationship'leri ekle  
            for rel in doc.relationships:
                relationships_data.append({
                    "source": rel.source.id,
                    "target": rel.target.id,
                    "type": rel.type,
                    "properties": dict(rel.properties) if rel.properties else {}
                })
            
            # Orijinal entity'lerin detaylarını logla
            logging.info(f"Orijinal entities: {[{'id': e['id'], 'type': e['type']} for e in entities_data[:5]]}")
            logging.info(f"Orijinal relationships: {[{'source': r['source'], 'type': r['type'], 'target': r['target']} for r in relationships_data[:5]]}")
            
            # JSON formatında hazırla
            entities_json = json.dumps({
                "entities": entities_data,
                "relationships": relationships_data,
                "document_name": file_name
            }, ensure_ascii=False, indent=2)
            
            # Post-processing prompt'u ile LLM'ye gönder
            full_prompt = POST_PROCESSING_PROMPT.format(document_id=document_node_id) + "\n\n" + entities_json
            
            logging.info(f"LLM'ye gönderilen prompt uzunluğu: {len(full_prompt)} karakter")
            logging.info(f"Document ID: {document_node_id}")
            logging.info(f"=== LLM'YE GÖNDERİLEN TAM PROMPT ===")
            logging.info(f"POST_PROCESSING_PROMPT:\n{POST_PROCESSING_PROMPT.format(document_id=document_node_id)}")
            logging.info(f"=== DATA KISMI ===")
            logging.info(f"Gönderilen JSON (ilk 500 karakter): {entities_json[:500]}...")
            if len(entities_json) > 500:
                logging.info(f"JSON'un sonu (son 200 karakter): ...{entities_json[-200:]}")
            logging.info(f"=== PROMPT BİTTİ ===")
            
            # LLM'den düzeltilmiş entity'leri al
            try:
                response = await llm.ainvoke(full_prompt)
                corrected_json = response.content
                
                logging.info(f"=== LLM TAM CEVABI ===")
                logging.info(f"LLM Response uzunluğu: {len(corrected_json)} karakter")
                logging.info(f"LLM Response:\n{corrected_json}")
                logging.info(f"=== LLM CEVABI BİTTİ ===")
                
                # JSON'u temizle ve extract et
                corrected_json = extract_json_from_response(corrected_json)
                logging.info(f"Temizlenmiş JSON: {corrected_json[:300]}..." if len(corrected_json) > 300 else f"Temizlenmiş JSON: {corrected_json}")
                
                # JSON parse et
                corrected_data = json.loads(corrected_json)
                
                logging.info(f"Post-processing başarılı: {len(corrected_data.get('entities', []))} entity, {len(corrected_data.get('relationships', []))} relationship")
                
                # Düzeltilmiş entity'lerin detaylarını logla
                corrected_entities = corrected_data.get('entities', [])
                corrected_relationships = corrected_data.get('relationships', [])
                
                logging.info(f"Düzeltilmiş entities: {[{'id': e['id'], 'type': e['type']} for e in corrected_entities[:5]]}")
                logging.info(f"Düzeltilmiş relationships: {[{'source': r['source'], 'type': r['type'], 'target': r['target']} for r in corrected_relationships[:5]]}")
                
                # Değişiklikleri analiz et
                original_entity_types = [e['type'] for e in entities_data]
                corrected_entity_types = [e['type'] for e in corrected_entities]
                
                added_entities = len(corrected_entities) - len(entities_data)
                removed_entities = len(entities_data) - len(corrected_entities)
                
                logging.info(f"Entity değişiklikleri: +{added_entities}, -{removed_entities}")
                logging.info(f"Orijinal entity tipleri: {set(original_entity_types)}")
                logging.info(f"Düzeltilmiş entity tipleri: {set(corrected_entity_types)}")
                
                # LLM'nin gerçekten düzeltme yapıp yapmadığını kontrol et
                if corrected_entities != entities_data:
                    logging.info("✅ LLM entity'lerde değişiklik yaptı!")
                else:
                    logging.info("❌ LLM entity'lerde herhangi bir değişiklik yapmadı")
                
                # Düzeltilmiş GraphDocument oluştur
                from langchain_experimental.graph_transformers.llm import GraphDocument
                from langchain_community.graphs.graph_document import Node, Relationship
                
                corrected_nodes = []
                corrected_relationships = []
                
                # Düzeltilmiş node'ları ekle
                for entity in corrected_data.get("entities", []):
                    node = Node(
                        id=entity["id"],
                        type=entity["type"],
                        properties=entity.get("properties", {})
                    )
                    corrected_nodes.append(node)
                
                # Düzeltilmiş relationship'leri ekle
                for rel in corrected_data.get("relationships", []):
                    # Source ve target node'ları bul
                    source_node = next((n for n in corrected_nodes if n.id == rel["source"]), None)
                    target_node = next((n for n in corrected_nodes if n.id == rel["target"]), None)
                    
                    if source_node and target_node:
                        relationship = Relationship(
                            source=source_node,
                            target=target_node,
                            type=rel["type"],
                            properties=rel.get("properties", {})
                        )
                        corrected_relationships.append(relationship)
                
                # Eğer LLM hiçbir entity döndürmediyse orijinalini kullan
                if not corrected_nodes and doc.nodes:
                    logging.warning("LLM hiç entity döndürmedi, orijinal document kullanılıyor")
                    corrected_documents.append(doc)
                else:
                    # Düzeltilmiş GraphDocument oluştur
                    corrected_doc = GraphDocument(
                        nodes=corrected_nodes,
                        relationships=corrected_relationships,
                        source=doc.source
                    )
                    corrected_documents.append(corrected_doc)
                
            except json.JSONDecodeError as e:
                logging.error(f"❌ LLM post-processing JSON parse hatası: {e}")
                logging.error(f"Problematic JSON (ilk 500 karakter): {corrected_json[:500]}...")
                logging.error(f"Orijinal entity'ler korunuyor: {original_entities_count} entity, {original_relationships_count} relationship")
                # Hata durumunda orijinal document'ı kullan
                corrected_documents.append(doc)
            except Exception as e:
                logging.error(f"❌ LLM post-processing genel hatası: {e}")
                logging.error(f"Response content (ilk 500 karakter): {response.content[:500] if 'response' in locals() else 'No response'}")
                logging.error(f"Orijinal entity'ler korunuyor: {original_entities_count} entity, {original_relationships_count} relationship")
                # Hata durumunda orijinal document'ı kullan  
                corrected_documents.append(doc)
        
        logging.info(f"LLM post-processing tamamlandı: {file_name}")
        return corrected_documents
        
    except Exception as e:
        logging.error(f"LLM post-processing genel hatası: {e}")
        return graph_documents  # Hata durumunda orijinal döndür



def detect_document_domain(file_name: str, first_chunk: str = "") -> str:
    """
    Dosya adı ve içeriğinden domain'i otomatik tespit eder
    
    Args:
        file_name: Dosya adı
        first_chunk: İlk chunk içeriği (opsiyonel)
        
    Returns:
        Domain adı (insurance, legal, financial, general)
    """
    file_name_lower = file_name.lower()
    content_lower = first_chunk.lower() if first_chunk else ""
    
    # Sigorta tespiti - geliştirilmiş keywords
    insurance_keywords = [
        "poliçe", "sigorta", "kasko", "trafik", "dask", "konut", "işyeri",
        "policy", "insurance", "coverage", "premium", "claim", "teminat",
        "prim", "sigortalı", "acente", "yangın", "deprem", "doğa sigorta",
        "türk sigorta", "aksigorta", "anadolu sigorta", "allianz",
        "mali sorumluluk", "ferdi kaza", "cam kırılması", "riziko"
    ]
    
    # Hukuki tespiti  
    legal_keywords = [
        "dava", "mahkeme", "avukat", "hukuk", "sözleşme", "anlaşma",
        "court", "legal", "lawsuit", "attorney", "contract", "agreement",
        "icra", "iflas", "temyiz", "karar", "hüküm", "dilekçe"
    ]
    
    # Finansal tespiti
    financial_keywords = [
        "banka", "kredi", "ödeme", "fatura", "hesap", "para", "tl", "usd", "eur",
        "bank", "credit", "payment", "invoice", "account", "money", "financial",
        "faiz", "kar", "zarar", "bilanço", "mali", "muhasebe"
    ]
    
    # Dosya adından domain tespit et
    if any(keyword in file_name_lower for keyword in insurance_keywords):
        return "insurance"
    elif any(keyword in file_name_lower for keyword in legal_keywords):
        return "legal"  
    elif any(keyword in file_name_lower for keyword in financial_keywords):
        return "financial"
    
    # İçerikten domain tespit et
    if content_lower:
        insurance_score = sum(1 for keyword in insurance_keywords if keyword in content_lower)
        legal_score = sum(1 for keyword in legal_keywords if keyword in content_lower)
        financial_score = sum(1 for keyword in financial_keywords if keyword in content_lower)
        
        max_score = max(insurance_score, legal_score, financial_score)
        
        if max_score > 2:  # En az 3 anahtar kelime eşleşmesi gerekli
            if insurance_score == max_score:
                return "insurance"
            elif legal_score == max_score:
                return "legal"
            elif financial_score == max_score:
                return "financial"
    
    return "general"
