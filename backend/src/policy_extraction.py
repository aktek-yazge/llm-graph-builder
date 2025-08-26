"""
Policy bilgilerini chunk içeriklerinden LLM ile çıkaran modül.

Upload esnasında dosya adından çıkarılamayan Policy bilgileri
(customer, policy year, insured item, policy type) chunk içeriklerinden
LLM ile çıkarılır ve eksik node'lar oluşturulur.
"""

import logging
from typing import Dict, List, Optional, Any
from src.llm import get_llm
from src.shared.common_fn import execute_graph_query

class PolicyExtractionService:
    """
    Policy bilgilerini chunk içeriklerinden çıkarma servisi
    """
    
    def __init__(self, graph):
        self.graph = graph
    
    async def extract_missing_policy_info(self, file_name: str, model: str) -> Dict[str, Any]:
        """
        Eksik policy bilgilerini chunk içeriklerinden çıkarır ve node'ları oluşturur.
        
        Args:
            file_name: İşlenecek dosya adı
            model: Kullanılacak LLM model
            
        Returns:
            Extraction sonucu
        """
        try:
            logging.info(f"🔍 Policy bilgisi extraction başlıyor: {file_name}")
            
            # Mevcut policy bilgilerini kontrol et
            existing_info = self._check_existing_policy_info(file_name)
            logging.info(f"📋 Mevcut policy bilgileri: {existing_info}")
            
            # Eksik bilgileri tespit et
            missing_fields = self._identify_missing_fields(existing_info)
            
            if not missing_fields:
                logging.info("✅ Tüm policy bilgileri mevcut, extraction atlanıyor")
                return {"status": "complete", "extracted_fields": [], "message": "All policy info exists"}
            
            logging.info(f"🎯 Eksik alanlar tespit edildi: {missing_fields}")
            
            # Chunk içeriklerini al
            chunks = self._get_document_chunks(file_name)
            if not chunks:
                logging.warning("⚠️ Chunk bulunamadı, extraction yapılamıyor")
                return {"status": "failed", "message": "No chunks found"}
            
            # LLM ile eksik bilgileri çıkar
            extracted_info = await self._extract_policy_info_from_chunks(
                chunks, missing_fields, model
            )
            
            if not extracted_info:
                logging.warning("⚠️ LLM extraction başarısız")
                return {"status": "failed", "message": "LLM extraction failed"}
            
            # Çıkarılan bilgilerle node'ları oluştur
            creation_result = await self._create_missing_policy_nodes(
                file_name, extracted_info, existing_info
            )
            
            logging.info(f"✅ Policy extraction tamamlandı: {creation_result}")
            
            return {
                "status": "success",
                "extracted_fields": list(extracted_info.keys()),
                "created_nodes": creation_result.get("created_nodes", []),
                "message": f"Successfully extracted {len(extracted_info)} fields"
            }
            
        except Exception as e:
            logging.error(f"Policy extraction hatası: {e}")
            return {"status": "error", "message": str(e)}
    
    def _check_existing_policy_info(self, file_name: str) -> Dict[str, Any]:
        """
        Mevcut policy bilgilerini kontrol eder
        """
        try:
            # Önce Document var mı kontrol et
            doc_check = """
            MATCH (d:Document {fileName: $file_name})
            RETURN d.fileName
            """
            doc_result = execute_graph_query(self.graph, doc_check, params={"file_name": file_name})
            
            if not doc_result:
                logging.warning(f"Document bulunamadı: {file_name}")
                return {}
            
            query = """
            MATCH (d:Document {fileName: $file_name})
            
            // Policy bilgilerini al - önce doğrudan Document'e bağlı Policy'leri ara
            OPTIONAL MATCH (p:Policy)-[:DOCUMENTED_IN|HAS_ENDORSEMENT|HAS_RENEWAL|HAS_CANCELLATION]->(d)
            
            // Eğer direkt ilişki yoksa, dosya adından Policy ID çıkar ve o Policy'yi ara
            WITH d, p, CASE WHEN p IS NULL THEN split($file_name, '.')[0] ELSE p.id END as potential_policy_id
            OPTIONAL MATCH (p2:Policy {id: potential_policy_id}) WHERE p IS NULL
            
            // Customer bilgisini al
            OPTIONAL MATCH (c:Customer)-[:HAS_DOC]->(d)
            OPTIONAL MATCH (c2:Customer)-[:HAS_POLICY]->(coalesce(p, p2))
            
            WITH d, coalesce(p, p2) as final_policy, coalesce(c, c2) as final_customer
            
            // İlişkili node'ları al
            OPTIONAL MATCH (final_policy)-[:HAS_YEAR]->(py:PolicyYear)
            OPTIONAL MATCH (final_policy)-[:HAS_INSURED_ITEM]->(ii:InsuredItem)
            OPTIONAL MATCH (final_policy)-[:HAS_TYPE]->(pt:PolicyType)
            
            RETURN 
                final_policy.id as policy_id,
                final_policy.name as policy_name,
                final_customer.name as customer_name,
                py.name as policy_year,
                ii.name as insured_item,
                pt.name as policy_type
            """
            
            result = execute_graph_query(self.graph, query, params={"file_name": file_name})
            
            if result and len(result) > 0:
                record = result[0]
                return {
                    "policy_id": record.get("policy_id"),
                    "policy_name": record.get("policy_name"),
                    "customer_name": record.get("customer_name"),
                    "policy_year": record.get("policy_year"),
                    "insured_item": record.get("insured_item"),
                    "policy_type": record.get("policy_type")
                }
            
            return {}
            
        except Exception as e:
            logging.error(f"Mevcut policy bilgilerini kontrol hatası: {e}")
            return {}
    
    def _identify_missing_fields(self, existing_info: Dict[str, Any]) -> List[str]:
        """
        Eksik policy alanlarını tespit eder
        """
        required_fields = ["customer_name", "policy_year", "insured_item", "policy_type"]
        missing = []
        
        for field in required_fields:
            if not existing_info.get(field):
                missing.append(field)
        
        return missing
    
    def _get_document_chunks(self, file_name: str) -> List[Dict[str, Any]]:
        """
        Dokümana ait chunk'ları alır
        """
        try:
            query = """
            MATCH (d:Document {fileName: $file_name})
            MATCH (c:Chunk)-[:PART_OF]->(d)
            RETURN c.chunkId as chunk_id, c.text as chunk_text
            ORDER BY c.chunkSeqId
            """
            
            result = execute_graph_query(self.graph, query, params={"file_name": file_name})
            
            chunks = []
            for record in result:
                chunks.append({
                    "chunk_id": record.get("chunk_id"),
                    "text": record.get("chunk_text", "")
                })
            
            logging.info(f"📄 {len(chunks)} chunk alındı")
            return chunks
            
        except Exception as e:
            logging.error(f"Chunk'ları alma hatası: {e}")
            return []
    
    async def _extract_policy_info_from_chunks(
        self, 
        chunks: List[Dict[str, Any]], 
        missing_fields: List[str], 
        model: str
    ) -> Dict[str, str]:
        """
        Chunk içeriklerinden LLM ile policy bilgilerini çıkarır
        """
        try:
            # LLM'i al
            llm, model_name = get_llm(model)
            logging.info(f"🤖 Policy extraction için {model_name} modeli kullanılıyor")
            
            # Chunk metinlerini birleştir (ilk 5 chunk yeterli olabilir)
            combined_text = "\n\n".join([
                chunk["text"] for chunk in chunks[:5] if chunk.get("text")
            ])
            
            if not combined_text.strip():
                logging.warning("⚠️ Chunk metinleri boş")
                return {}
            
            # LLM prompt'unu oluştur
            prompt = self._create_extraction_prompt(missing_fields, combined_text)
            
            # LLM'den cevap al
            response = await llm.ainvoke(prompt)
            
            # Cevabı parse et
            extracted_info = self._parse_llm_response(response.content, missing_fields)
            
            logging.info(f"🎯 LLM extraction sonucu: {extracted_info}")
            return extracted_info
            
        except Exception as e:
            logging.error(f"LLM policy extraction hatası: {e}")
            return {}
    
    def _create_extraction_prompt(self, missing_fields: List[str], text: str) -> str:
        """
        Policy bilgisi extraction için LLM prompt'u oluşturur
        """
        field_descriptions = {
            "customer_name": "Poliçe sahibinin adı soyadı (örn: Ayça Dinçkök, Mehmet Yılmaz)",
            "policy_year": "Poliçe yılı (örn: 2024, 2023)",
            "insured_item": "Sigortalı eşya/mülk tipi (örn: Konut, Araç, Daire, Ev)",
            "policy_type": "Sigorta tipi (örn: Konut Sigortası, Kasko, DASK, Yangın)"
        }
        
        field_requests = []
        for field in missing_fields:
            desc = field_descriptions.get(field, field)
            field_requests.append(f"- {field}: {desc}")
        
        prompt = f"""
Aşağıdaki Türkçe sigorta belgesi metninden eksik olan poliçe bilgilerini çıkarın.

ÇIKARILMASI GEREKEN BİLGİLER:
{chr(10).join(field_requests)}

BELGE METNİ:
{text[:2000]}...

KURALLAR:
1. Sadece metinde açıkça geçen bilgileri çıkarın
2. Tahmin yapmayın, kesin bilgi yoksa "bulunamadı" yazın
3. İsimleri tam olarak yazın (kısaltmayın)
4. Yılları 4 haneli yazın (örn: 2024)
5. Cevabınızı şu formatta verin:

customer_name: [müşteri adı veya bulunamadı]
policy_year: [yıl veya bulunamadı]
insured_item: [sigortalı eşya veya bulunamadı]
policy_type: [sigorta tipi veya bulunamadı]

CEVAP:
"""
        
        return prompt
    
    def _parse_llm_response(self, response: str, missing_fields: List[str]) -> Dict[str, str]:
        """
        LLM cevabını parse eder
        """
        extracted = {}
        
        try:
            lines = response.strip().split('\n')
            
            for line in lines:
                if ':' in line:
                    key, value = line.split(':', 1)
                    key = key.strip().lower()
                    value = value.strip()
                    
                    # "bulunamadı" değerlerini atla
                    if value.lower() in ['bulunamadı', 'bulunamadi', 'yok', 'belirsiz', 'unknown']:
                        continue
                    
                    # Geçerli field ise ve boş değilse ekle
                    if key in missing_fields and value:
                        extracted[key] = value
            
            return extracted
            
        except Exception as e:
            logging.error(f"LLM response parse hatası: {e}")
            return {}
    
    async def _create_missing_policy_nodes(
        self, 
        file_name: str, 
        extracted_info: Dict[str, str],
        existing_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Çıkarılan bilgilerle eksik policy node'larını oluşturur
        """
        try:
            created_nodes = []
            
            # Policy ID'yi al veya oluştur
            policy_id = existing_info.get("policy_id")
            if not policy_id:
                # Dosya adından policy ID oluştur
                import os
                policy_id = os.path.splitext(file_name)[0]
                
                # Policy node'unu oluştur
                await self._create_policy_node(policy_id, file_name)
                created_nodes.append({"type": "Policy", "id": policy_id})
            else:
                # Policy var ama Document ile ilişkisi yoksa ilişkiyi kur
                await self._ensure_policy_document_relationship(policy_id, file_name)
            
            # Document ve Policy node'larını extraction bilgileriyle güncelle
            await self._update_document_and_policy_with_extracted_info(
                file_name, extracted_info, policy_id
            )
            
            # Customer node'unu oluştur
            if "customer_name" in extracted_info:
                await self._create_customer_node(
                    extracted_info["customer_name"], policy_id, file_name
                )
                created_nodes.append({
                    "type": "Customer", 
                    "name": extracted_info["customer_name"]
                })
            
            # PolicyYear node'unu oluştur
            if "policy_year" in extracted_info:
                await self._create_policy_year_node(
                    extracted_info["policy_year"], policy_id
                )
                created_nodes.append({
                    "type": "PolicyYear", 
                    "year": extracted_info["policy_year"]
                })
            
            # InsuredItem node'unu oluştur
            if "insured_item" in extracted_info:
                await self._create_insured_item_node(
                    extracted_info["insured_item"], policy_id
                )
                created_nodes.append({
                    "type": "InsuredItem", 
                    "item": extracted_info["insured_item"]
                })
            
            # PolicyType node'unu oluştur
            if "policy_type" in extracted_info:
                await self._create_policy_type_node(
                    extracted_info["policy_type"], policy_id
                )
                created_nodes.append({
                    "type": "PolicyType", 
                    "name": extracted_info["policy_type"]
                })
            
            # Eksik alanları ilişkili node'lardan güncelle
            await self._sync_policy_properties_from_relationships(policy_id, file_name)
            
            return {"created_nodes": created_nodes}
            
        except Exception as e:
            logging.error(f"Policy node oluşturma hatası: {e}")
            return {"created_nodes": []}
    
    async def _create_policy_node(self, policy_id: str, file_name: str):
        """Policy node'unu oluşturur"""
        query = """
        MATCH (d:Document {fileName: $file_name})
        MERGE (p:Policy {id: $policy_id})
        ON CREATE SET 
            p.name = $policy_id,
            p.createdAt = datetime(),
            p.extractedFromContent = true
        MERGE (p)-[:DOCUMENTED_IN]->(d)
        """
        
        execute_graph_query(self.graph, query, params={
            "policy_id": policy_id,
            "file_name": file_name
        })

    async def _ensure_policy_document_relationship(self, policy_id: str, file_name: str):
        """Policy ve Document arasında DOCUMENTED_IN ilişkisini garanti eder"""
        query = """
        MATCH (p:Policy {id: $policy_id})
        MATCH (d:Document {fileName: $file_name})
        MERGE (p)-[:DOCUMENTED_IN]->(d)
        """
        
        execute_graph_query(self.graph, query, params={
            "policy_id": policy_id,
            "file_name": file_name
        })
        
        logging.info(f"✅ Policy-Document ilişkisi kontrol edildi: {policy_id} -> {file_name}")

    async def _update_document_and_policy_with_extracted_info(
        self, 
        file_name: str, 
        extracted_info: Dict[str, str], 
        policy_id: str
    ):
        """
        Document ve Policy node'larını LLM extraction sonuçlarıyla günceller
        """
        try:
            # Sadece mevcut olan alanları güncelle
            update_sets = []
            doc_params = {"file_name": file_name}
            policy_params = {"policy_id": policy_id}
            
            if "customer_name" in extracted_info:
                update_sets.append("d.customerName = $customer_name")
                doc_params["customer_name"] = extracted_info["customer_name"]
                policy_params["customer_name"] = extracted_info["customer_name"]
            
            if "policy_year" in extracted_info:
                update_sets.append("d.policyYear = $policy_year")
                doc_params["policy_year"] = extracted_info["policy_year"]
                policy_params["policy_year"] = extracted_info["policy_year"]
                
            if "insured_item" in extracted_info:
                update_sets.append("d.insuredItem = $insured_item")
                doc_params["insured_item"] = extracted_info["insured_item"]
                policy_params["insured_item"] = extracted_info["insured_item"]
                
            if "policy_type" in extracted_info:
                update_sets.append("d.policyType = $policy_type")
                doc_params["policy_type"] = extracted_info["policy_type"]
                policy_params["policy_type"] = extracted_info["policy_type"]
            
            if update_sets:
                # Document node'unu güncelle
                doc_update_query = f"""
                MATCH (d:Document {{fileName: $file_name}})
                SET {', '.join(update_sets.copy())},
                    d.updatedFromExtraction = true,
                    d.extractionUpdatedAt = datetime()
                """
                
                execute_graph_query(self.graph, doc_update_query, params=doc_params)
                
                # Policy node'unu güncelle
                policy_update_sets = [s.replace("d.", "p.") for s in update_sets]
                policy_update_query = f"""
                MATCH (p:Policy {{id: $policy_id}})
                SET {', '.join(policy_update_sets)},
                    p.updatedFromExtraction = true,
                    p.extractionUpdatedAt = datetime()
                """
                
                execute_graph_query(self.graph, policy_update_query, params=policy_params)
                
                logging.info(f"✅ Document ve Policy node'ları güncellendi: {file_name}, güncellenene alanlar: {list(extracted_info.keys())}")
            else:
                logging.info(f"⚠️ Güncellenecek alan bulunamadı: {file_name}")
            
        except Exception as e:
            logging.error(f"Document/Policy güncelleme hatası: {e}")
    
    async def _create_customer_node(self, customer_name: str, policy_id: str, file_name: str):
        """Customer node'unu oluşturur"""
        query = """
        MERGE (c:Customer {name: $customer_name})
        ON CREATE SET 
            c.fullName = $customer_name,
            c.createdAt = datetime(),
            c.extractedFromContent = true
        WITH c
        MATCH (p:Policy {id: $policy_id})
        MATCH (d:Document {fileName: $file_name})
        MERGE (c)-[:HAS_POLICY]->(p)
        MERGE (c)-[:HAS_DOC]->(d)
        """
        
        execute_graph_query(self.graph, query, params={
            "customer_name": customer_name,
            "policy_id": policy_id,
            "file_name": file_name
        })
    
    async def _create_policy_year_node(self, year: str, policy_id: str):
        """PolicyYear node'unu oluşturur"""
        query = """
        MERGE (py:PolicyYear {name: $year})
        ON CREATE SET 
            py.year = toInteger($year),
            py.createdAt = datetime(),
            py.extractedFromContent = true
        WITH py
        MATCH (p:Policy {id: $policy_id})
        MERGE (p)-[:HAS_YEAR]->(py)
        """
        
        execute_graph_query(self.graph, query, params={
            "year": year,
            "policy_id": policy_id
        })
    
    async def _create_insured_item_node(self, insured_item: str, policy_id: str):
        """InsuredItem node'unu oluşturur"""
        query = """
        MERGE (ii:InsuredItem {name: $insured_item})
        ON CREATE SET 
            ii.description = $insured_item,
            ii.createdAt = datetime(),
            ii.extractedFromContent = true
        WITH ii
        MATCH (p:Policy {id: $policy_id})
        MERGE (p)-[:HAS_INSURED_ITEM]->(ii)
        """
        
        execute_graph_query(self.graph, query, params={
            "insured_item": insured_item,
            "policy_id": policy_id
        })
    
    async def _create_policy_type_node(self, policy_type: str, policy_id: str):
        """PolicyType node'unu oluşturur"""
        query = """
        MERGE (pt:PolicyType {name: $policy_type})
        ON CREATE SET 
            pt.typeName = $policy_type,
            pt.createdAt = datetime(),
            pt.extractedFromContent = true
        WITH pt
        MATCH (p:Policy {id: $policy_id})
        MERGE (p)-[:HAS_TYPE]->(pt)
        """
        
        execute_graph_query(self.graph, query, params={
            "policy_type": policy_type,
            "policy_id": policy_id
        })

    async def _sync_policy_properties_from_relationships(self, policy_id: str, file_name: str):
        """
        Policy ve Document node'larının eksik alanlarını ilişkili node'lardan günceller
        """
        try:
            # İlişkili node'lardan bilgileri al
            query = """
            MATCH (p:Policy {id: $policy_id})
            OPTIONAL MATCH (c:Customer)-[:HAS_POLICY]->(p)
            OPTIONAL MATCH (p)-[:HAS_YEAR]->(py:PolicyYear)
            OPTIONAL MATCH (p)-[:HAS_INSURED_ITEM]->(ii:InsuredItem)
            OPTIONAL MATCH (p)-[:HAS_TYPE]->(pt:PolicyType)
            RETURN 
                c.name as customer_name,
                py.name as policy_year,
                ii.name as insured_item,
                pt.name as policy_type
            """
            
            result = execute_graph_query(self.graph, query, params={"policy_id": policy_id})
            
            if not result:
                return
                
            record = result[0]
            
            # Eksik olan alanları güncelle
            update_params = {"policy_id": policy_id, "file_name": file_name}
            policy_updates = []
            doc_updates = []
            
            if record.get("customer_name"):
                policy_updates.append("p.customerName = $customer_name")
                doc_updates.append("d.customerName = $customer_name")
                update_params["customer_name"] = record["customer_name"]
            
            if record.get("policy_year"):
                policy_updates.append("p.policyYear = $policy_year")
                doc_updates.append("d.policyYear = $policy_year")
                update_params["policy_year"] = record["policy_year"]
            
            if record.get("insured_item"):
                policy_updates.append("p.insuredItem = $insured_item")
                doc_updates.append("d.insuredItem = $insured_item")
                update_params["insured_item"] = record["insured_item"]
                
            if record.get("policy_type"):
                policy_updates.append("p.policyType = $policy_type")
                doc_updates.append("d.policyType = $policy_type")
                update_params["policy_type"] = record["policy_type"]
            
            # Policy node'unu güncelle
            if policy_updates:
                policy_query = f"""
                MATCH (p:Policy {{id: $policy_id}})
                SET {', '.join(policy_updates)},
                    p.syncedFromRelationships = true,
                    p.syncedAt = datetime()
                """
                execute_graph_query(self.graph, policy_query, params=update_params)
            
            # Document node'unu güncelle
            if doc_updates:
                doc_query = f"""
                MATCH (d:Document {{fileName: $file_name}})
                SET {', '.join(doc_updates)},
                    d.syncedFromRelationships = true,
                    d.syncedAt = datetime()
                """
                execute_graph_query(self.graph, doc_query, params=update_params)
                
            logging.info(f"✅ Policy ve Document prop'ları ilişkilerden senkronize edildi: {policy_id}")
            
        except Exception as e:
            logging.error(f"Property senkronizasyon hatası: {e}")


# Convenience function for easy import
async def extract_missing_policy_info(graph, file_name: str, model: str) -> Dict[str, Any]:
    """
    Kolaylık fonksiyonu - eksik policy bilgilerini çıkarır
    """
    service = PolicyExtractionService(graph)
    return await service.extract_missing_policy_info(file_name, model)
