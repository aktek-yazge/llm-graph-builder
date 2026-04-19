"""
DSL → Cypher Compiler

Graph DSL'i Neo4j Cypher sorgusuna dönüştürür.
Bu dönüşüm deterministik ve hatasızdır.
"""

import logging
from typing import Dict, List, Any, Tuple, Optional, Set
from dataclasses import dataclass, field

from .graph_dsl import (
    GraphDSL,
    QueryIntent,
    TraversalStep,
    Filter,
    FilterOperator,
    ReturnSpec,
    AggregateSpec,
    AggregateFunction,
    OrderBySpec,
)

logger = logging.getLogger(__name__)


@dataclass
class CompilationResult:
    """Derleme sonucu"""
    cypher: str                         # Üretilen Cypher sorgusu
    params: Dict[str, Any]              # Parameterized query için parametreler
    requires_embedding: bool = False    # Embedding gerekiyor mu?
    query_text: Optional[str] = None    # Embedding için query text
    warnings: List[str] = field(default_factory=list)  # Uyarılar


class DSLCompiler:
    """
    Graph DSL → Cypher Compiler
    
    DSL'i Cypher sorgusuna dönüştürür. Tüm Cypher kuralları burada uygulanır:
    - İlişki yönleri doğru
    - toLower() kullanımı
    - DISTINCT kullanımı
    - LIMIT kullanımı
    - Parameterized queries
    """
    
    def __init__(self):
        self._param_counter = 0
        self._node_aliases: Dict[str, str] = {}  # NodeLabel -> alias
        self._relation_aliases: Dict[Tuple[str, str, str], str] = {}  # (from_node, relation, to_node) -> alias
    
    def compile(self, dsl: GraphDSL) -> CompilationResult:
        """
        DSL'i Cypher'a derle
        
        Args:
            dsl: Graph DSL objesi
            
        Returns:
            CompilationResult: Cypher sorgusu ve parametreler
        """
        self._reset()
        
        # Intent'e göre derleme stratejisi seç
        if dsl.intent == QueryIntent.SEARCH_CONTENT:
            return self._compile_semantic_search(dsl)
        elif dsl.intent == QueryIntent.EXPLORE_SCHEMA:
            return self._compile_schema_exploration(dsl)
        elif dsl.intent in [QueryIntent.COUNT_NODES, QueryIntent.AGGREGATE_VALUES, QueryIntent.GROUP_BY]:
            return self._compile_aggregation(dsl)
        else:
            return self._compile_standard_query(dsl)
    
    def _reset(self):
        """Compiler state'ini sıfırla"""
        self._param_counter = 0
        self._node_aliases = {}
        self._relation_aliases = {}
    
    def _get_next_param_name(self, prefix: str = "p") -> str:
        """Benzersiz parametre adı üret"""
        self._param_counter += 1
        return f"{prefix}_{self._param_counter}"
    
    def _get_node_alias(self, label: str) -> str:
        """Node için alias al veya oluştur"""
        if label not in self._node_aliases:
            # Label'ın ilk harfini küçük yap
            alias = label[0].lower() + label[1:] if label else "n"
            # Çakışma varsa numara ekle
            base_alias = alias
            counter = 1
            while alias in self._node_aliases.values() or alias in self._relation_aliases.values():
                alias = f"{base_alias}{counter}"
                counter += 1
            self._node_aliases[label] = alias
        return self._node_aliases[label]
    
    def _get_relation_based_alias(self, from_node: str, relation: str, to_node: str) -> str:
        """
        Relation'a göre unique alias oluştur.
        
        Aynı to_node'a farklı relation'larla erişildiğinde farklı alias'lar gerekir.
        Örnek:
            - HAS_START_DATE -> Date = startDate
            - HAS_END_DATE -> Date = endDate
        """
        key = (from_node, relation, to_node)
        
        if key not in self._relation_aliases:
            # Relation adından alias türet
            # HAS_START_DATE -> startDate
            # HAS_END_DATE -> endDate  
            # ISSUED_BY -> issuedBy
            
            # Relation'daki prefix'leri temizle ve camelCase yap
            alias = self._relation_to_camel_case(relation, to_node)
            
            # Çakışma kontrolü
            base_alias = alias
            counter = 1
            all_aliases = set(self._node_aliases.values()) | set(self._relation_aliases.values())
            while alias in all_aliases:
                alias = f"{base_alias}{counter}"
                counter += 1
            
            self._relation_aliases[key] = alias
            # Node alias'ına da ekle (filter ve return'de kullanılabilmesi için)
            # Ancak sadece eğer bu label daha önce kaydedilmediyse veya farklı bir alias değilse
            # Bu durumda label -> alias mapping'i güncellemiyoruz, çünkü relation-based alias'ı kullanıyoruz
        
        return self._relation_aliases[key]
    
    def _relation_to_camel_case(self, relation: str, to_node: str) -> str:
        """
        Relation adını camelCase alias'a çevir.
        
        Örnekler:
            HAS_START_DATE, Date -> startDate
            HAS_END_DATE, Date -> endDate
            ISSUED_BY, InsuranceCompany -> issuedByInsuranceCompany (veya insuranceCompany)
            HAS_POLICY, Policy -> policy
        """
        # Common prefix'leri temizle
        prefixes_to_remove = ["HAS_", "IS_", "BELONGS_TO_", "RELATES_TO_"]
        clean_relation = relation
        for prefix in prefixes_to_remove:
            if clean_relation.startswith(prefix):
                clean_relation = clean_relation[len(prefix):]
                break
        
        # Eğer temizlenmiş relation, to_node ile aynıysa (case-insensitive)
        # Sadece to_node'u küçük harfle döndür
        if clean_relation.replace("_", "").upper() == to_node.upper():
            return to_node[0].lower() + to_node[1:]
        
        # Relation'ı camelCase'e çevir
        # START_DATE -> startDate
        parts = clean_relation.lower().split("_")
        if len(parts) == 1:
            return parts[0]
        
        return parts[0] + "".join(p.capitalize() for p in parts[1:])
    
    def _compile_standard_query(self, dsl: GraphDSL) -> CompilationResult:
        """Standart MATCH sorgusu derle"""
        parts = []
        params = {}
        warnings = []
        
        # 1. MATCH clause
        match_clause = self._build_match_clause(dsl)
        parts.append(match_clause)
        
        # 2. WHERE clause
        where_clause, where_params = self._build_where_clause(dsl.filters)
        if where_clause:
            parts.append(where_clause)
            params.update(where_params)
        
        # 3. RETURN clause
        return_clause = self._build_return_clause(dsl.return_spec, dsl.aggregate)
        parts.append(return_clause)
        
        # 4. ORDER BY clause
        if dsl.order_by:
            order_clause = self._build_order_clause(dsl.order_by)
            parts.append(order_clause)
        
        # 5. LIMIT clause
        if dsl.limit > 0:
            parts.append(f"LIMIT {dsl.limit}")
        
        # 6. SKIP clause
        if dsl.skip > 0:
            parts.append(f"SKIP {dsl.skip}")
        
        cypher = "\n".join(parts)
        
        return CompilationResult(
            cypher=cypher,
            params=params,
            requires_embedding=False,
            warnings=warnings
        )
    
    def _compile_semantic_search(self, dsl: GraphDSL) -> CompilationResult:
        """
        Semantic (embedding) arama sorgusu derle
        
        İki mod destekler:
        1. VECTOR INDEX modu: Filter/traversal yoksa → db.index.vector.queryNodes() kullanır (HIZLI)
        2. FILTERED modu: Filter/traversal varsa → Önce filter, sonra similarity (context-aware)
        
        DSL Örneği:
        {
            "intent": "search_content",
            "semantic_search": {
                "query_text": "kira kaybı",
                "similarity_threshold": 0.80
            },
            "traversal": [...],  # Opsiyonel - varsa filtered mod
            "filters": [...]     # Opsiyonel - varsa filtered mod
        }
        """
        if not dsl.semantic_search:
            return CompilationResult(
                cypher="// ERROR: semantic_search spec missing",
                params={},
                requires_embedding=True,
                warnings=["semantic_search spesifikasyonu eksik"]
            )
        
        ss = dsl.semantic_search
        params = {}
        warnings = []
        
        # Chunk alias
        chunk_alias = self._get_node_alias(ss.target_node)
        
        # Filter veya traversal var mı kontrol et
        has_filters = bool(dsl.filters) or bool(ss.context_filters)
        has_traversal = bool(dsl.traversal)
        
        # === MOD SEÇİMİ ===
        if not has_filters and not has_traversal:
            # 🚀 VECTOR INDEX MODU - Çok hızlı!
            # Filter yoksa db.index.vector.queryNodes() kullan
            return self._compile_vector_index_search(dsl, ss, chunk_alias)
        else:
            # 🔍 FILTERED MODU - Context-aware ama daha yavaş
            return self._compile_filtered_semantic_search(dsl, ss, chunk_alias, has_traversal)
    
    def _compile_vector_index_search(self, dsl: GraphDSL, ss, chunk_alias: str) -> CompilationResult:
        """
        Vector index kullanarak hızlı semantic arama.
        db.index.vector.queryNodes() Neo4j 5.11+ için.
        
        Bu metod filter/traversal OLMADAN çağrılır.
        """
        # Vector index adı - Chunk embedding için 'vector' index'i kullanıyoruz
        vector_index_name = "vector"  # Neo4j'deki index adı
        
        # Limit - vector search için daha fazla sonuç al, sonra threshold uygula
        search_limit = min(ss.limit * 2, 100)  # En fazla 100
        
        parts = []
        
        # CALL db.index.vector.queryNodes
        parts.append(f"CALL db.index.vector.queryNodes('{vector_index_name}', {search_limit}, $embedding_vector)")
        parts.append(f"YIELD node AS {chunk_alias}, score")
        
        # WHERE - threshold filtresi
        parts.append(f"WHERE score > {ss.similarity_threshold}")
        
        # === RETURN CLAUSE ===
        return_parts = [f"{chunk_alias}.{ss.text_property} AS text"]
        
        if ss.return_score:
            return_parts.append("score")
        
        # Chunk bilgileri
        return_parts.append(f"{chunk_alias}.page_link AS page_link")
        return_parts.append(f"{chunk_alias}.fileName AS fileName")
        
        # Return spec'teki ek property'ler
        if dsl.return_spec:
            for node_label, props in dsl.return_spec.properties.items():
                if node_label == ss.target_node:
                    for prop in props:
                        if prop not in ["text", "page_link", "fileName"]:
                            return_parts.append(f"{chunk_alias}.{prop} AS {node_label}_{prop}")
        
        parts.append("RETURN " + ", ".join(return_parts))
        
        # ORDER BY score
        parts.append("ORDER BY score DESC")
        
        # LIMIT
        parts.append(f"LIMIT {ss.limit}")
        
        cypher = "\n".join(parts)
        
        return CompilationResult(
            cypher=cypher,
            params={},
            requires_embedding=True,
            query_text=ss.query_text,
            warnings=["🚀 Vector index kullanılıyor (hızlı mod)"]
        )
    
    def _compile_filtered_semantic_search(self, dsl: GraphDSL, ss, chunk_alias: str, has_traversal: bool) -> CompilationResult:
        """
        POST-FILTERING yaklaşımı ile semantic arama.
        
        1. Vector index ile hızlıca aday chunk'ları bul (O(log n))
        2. Sonra MATCH ile traversal/filter uygula
        3. Bu sayede gds.similarity.cosine() kullanmadan hızlı arama yapılır
        """
        params = {}
        warnings = ["🔍 Post-filtering modu: Vector index → Traversal filter"]
        
        # Daha fazla aday al, sonra filtrele (traversal bazıları eleyebilir)
        candidate_limit = ss.limit * 5 if ss.limit else 100
        
        # Vector index adı - Chunk embedding için 'vector' index'i kullanıyoruz
        vector_index_name = "vector"
        
        parts = []
        
        # === STEP 1: VECTOR INDEX SEARCH ===
        parts.append(f"CALL db.index.vector.queryNodes('{vector_index_name}', {candidate_limit}, $embedding_vector)")
        parts.append(f"YIELD node AS {chunk_alias}, score")
        
        # Similarity threshold - ilk WHERE
        parts.append(f"WHERE score > {ss.similarity_threshold}")
        
        # === STEP 2: TRAVERSAL MATCH ===
        if has_traversal:
            # Traversal'ı ters yönde kur - Chunk'tan başlayarak diğer node'lara bağlan
            # Örnek: Chunk -> Document <- Policy
            traversal_match = self._build_post_filter_traversal(dsl.traversal, chunk_alias)
            if traversal_match:
                parts.append(traversal_match)
        
        # === STEP 3: ADDITIONAL FILTERS ===
        filter_conditions = []
        
        # DSL filters (Customer, Policyholder, Coverage vb. filtreleri)
        for filter_obj in dsl.filters:
            condition, filter_params = self._build_filter_condition(filter_obj)
            filter_conditions.append(condition)
            params.update(filter_params)
        
        # Semantic search context filters
        for filter_obj in ss.context_filters:
            condition, filter_params = self._build_filter_condition(filter_obj)
            filter_conditions.append(condition)
            params.update(filter_params)
        
        if filter_conditions:
            parts.append("WHERE " + " AND ".join(filter_conditions))
        
        # === RETURN CLAUSE ===
        return_parts = [f"{chunk_alias}.{ss.text_property} AS text"]
        
        if ss.return_score:
            return_parts.append("score")
        
        # Source info - Document bilgisi
        doc_alias = self._node_aliases.get("Document")
        if doc_alias:
            return_parts.append(f"{doc_alias}.fileName AS fileName")
        
        # Chunk page_link
        return_parts.append(f"{chunk_alias}.page_link AS page_link")
        
        # Return spec'teki ek property'ler
        if dsl.return_spec:
            for node_label, props in dsl.return_spec.properties.items():
                node_alias = self._node_aliases.get(node_label)
                if node_alias:
                    for prop in props:
                        return_parts.append(f"{node_alias}.{prop} AS {node_label}_{prop}")
        
        parts.append("RETURN " + ", ".join(return_parts))
        
        # ORDER BY score
        if ss.return_score:
            parts.append("ORDER BY score DESC")
        
        # LIMIT
        parts.append(f"LIMIT {ss.limit}")
        
        cypher = "\n".join(parts)
        
        # Filter yoksa uyarı ekle
        if not dsl.filters and not ss.context_filters:
            warnings.append("⚠️ Filter olmadan semantic arama yavaş olabilir. Text filter eklemeyi düşünün.")
        
        return CompilationResult(
            cypher=cypher,
            params=params,
            requires_embedding=True,
            query_text=ss.query_text,
            warnings=warnings
        )
    
    def _compile_schema_exploration(self, dsl: GraphDSL) -> CompilationResult:
        """Schema keşif sorgusu derle"""
        # APOC meta kullan
        cypher = """CALL apoc.meta.schema() YIELD value
RETURN value"""
        
        return CompilationResult(
            cypher=cypher,
            params={},
            requires_embedding=False,
            warnings=[]
        )
    
    def _compile_aggregation(self, dsl: GraphDSL) -> CompilationResult:
        """Aggregation sorgusu derle"""
        parts = []
        params = {}
        
        # MATCH clause
        match_clause = self._build_match_clause(dsl)
        parts.append(match_clause)
        
        # WHERE clause
        where_clause, where_params = self._build_where_clause(dsl.filters)
        if where_clause:
            parts.append(where_clause)
            params.update(where_params)
        
        # RETURN with aggregation
        if dsl.aggregate:
            agg = dsl.aggregate
            node_alias = self._get_node_alias(agg.node)
            
            # Group by varsa WITH kullan
            if agg.group_by:
                group_parts = []
                for gb in agg.group_by:
                    gb_alias = self._get_node_alias(gb["node"])
                    group_parts.append(f"{gb_alias}.{gb['property']}")
                
                # Aggregate expression
                agg_expr = self._build_aggregate_expression(agg, node_alias)
                
                parts.append(f"RETURN {', '.join(group_parts)}, {agg_expr} AS {agg.alias}")
                parts.append(f"ORDER BY {agg.alias} DESC")
            else:
                # Sadece aggregate
                agg_expr = self._build_aggregate_expression(agg, node_alias)
                parts.append(f"RETURN {agg_expr} AS {agg.alias}")
        else:
            # COUNT(*)
            parts.append("RETURN COUNT(*) AS count")
        
        # LIMIT
        if dsl.limit > 0:
            parts.append(f"LIMIT {dsl.limit}")
        
        cypher = "\n".join(parts)
        
        return CompilationResult(
            cypher=cypher,
            params=params,
            requires_embedding=False,
            warnings=[]
        )
    
    def _build_match_clause(self, dsl: GraphDSL) -> str:
        """MATCH clause oluştur"""
        if dsl.traversal:
            pattern = self._build_traversal_pattern(dsl.traversal)
            keyword = "OPTIONAL MATCH" if dsl.use_optional_match else "MATCH"
            return f"{keyword} {pattern}"
        elif dsl.start_node:
            alias = self._get_node_alias(dsl.start_node)
            return f"MATCH ({alias}:{dsl.start_node})"
        else:
            return "MATCH (n)"
    
    def _build_traversal_pattern(self, traversal: List[TraversalStep]) -> str:
        """
        Traversal pattern oluştur - Star pattern ve duplicate node type desteği ile
        
        DSL'de her traversal'ın from_node'u farklı olabilir:
        - Zincir: A→B→C (her step öncekinin to_node'undan başlar)
        - Star: A→B, A→C, A→D (hepsi aynı node'dan çıkar)
        - Karma: A→B, B→C, A→D (hem zincir hem star)
        
        Aynı node type'a farklı relation'larla erişim:
        - A→HAS_START_DATE→Date, A→HAS_END_DATE→Date
        - Bu durumda: startDate, endDate olarak farklı alias'lar kullanılır
        """
        if not traversal:
            return "(n)"
        
        # Traversal analizi: aynı (from_node, to_node) çifti farklı relation'larla mı kullanılıyor?
        # Bu durumda relation-based alias gerekir (örn: Policy->startDate, Policy->endDate)
        # Farklı from_node'lar aynı to_node'a gidiyorsa → PAYLAŞILAN alias (pattern birleştirme)
        from_to_relations: Dict[Tuple[str, str], List[str]] = {}
        for step in traversal:
            key = (step.from_node, step.to_node)
            if key not in from_to_relations:
                from_to_relations[key] = []
            from_to_relations[key].append(step.relation)
        
        # Aynı (from, to) çiftine birden fazla relation varsa relation-based alias gerekir
        needs_relation_alias: Set[Tuple[str, str]] = set()
        for key, relations in from_to_relations.items():
            if len(relations) > 1:
                needs_relation_alias.add(key)
        
        # İlk traversal için pattern oluştur
        first_step = traversal[0]
        from_alias = self._get_node_alias(first_step.from_node)
        
        # to_node için alias: sadece aynı (from, to) çiftine farklı relation'larla gidiliyorsa relation-based
        first_key = (first_step.from_node, first_step.to_node)
        if first_key in needs_relation_alias:
            to_alias = self._get_relation_based_alias(first_step.from_node, first_step.relation, first_step.to_node)
        else:
            to_alias = self._get_node_alias(first_step.to_node)
        
        if first_step.direction == "outgoing":
            rel_pattern = f"-[:{first_step.relation}]->"
        elif first_step.direction == "incoming":
            rel_pattern = f"<-[:{first_step.relation}]-"
        else:
            rel_pattern = f"-[:{first_step.relation}]-"
        
        # Ana pattern ve ek pattern'ler
        main_patterns = [f"({from_alias}:{first_step.from_node}){rel_pattern}({to_alias}:{first_step.to_node})"]
        
        # Önceki traversal'ın to_node'unu takip et
        last_to_node = first_step.to_node
        last_to_alias = to_alias
        
        for step in traversal[1:]:
            # from_alias: from_node için zaten var olan alias'ı bul
            if step.from_node in self._node_aliases:
                from_alias = self._node_aliases[step.from_node]
            else:
                from_alias = self._get_node_alias(step.from_node)
            
            # to_alias belirleme - ÖNEMLİ: pattern'leri birleştirmek için
            # Önce to_node zaten tanımlı mı kontrol et
            to_node_already_exists = step.to_node in self._node_aliases
            step_key = (step.from_node, step.to_node)
            
            if to_node_already_exists:
                # to_node zaten var - AYNI NODE'A BAĞLAN (pattern birleştirme)
                # Bu sayede Chunk->Document ve Policy->Document aynı Document'a bağlanır
                to_alias = self._node_aliases[step.to_node]
            elif step_key in needs_relation_alias:
                # Aynı from_node'dan aynı to_node'a farklı relation'larla gidiliyor
                # Örn: Policy->HAS_START_DATE->Date, Policy->HAS_END_DATE->Date
                to_alias = self._get_relation_based_alias(step.from_node, step.relation, step.to_node)
            else:
                to_alias = self._get_node_alias(step.to_node)
            
            if step.direction == "outgoing":
                rel_pattern = f"-[:{step.relation}]->"
            elif step.direction == "incoming":
                rel_pattern = f"<-[:{step.relation}]-"
            else:
                rel_pattern = f"-[:{step.relation}]-"
            
            # Pattern oluşturma
            if step.from_node == last_to_node:
                # Zincir devam: önceki to_node'dan devam et
                main_patterns[0] += f"{rel_pattern}({to_alias}:{step.to_node})"
                last_to_node = step.to_node
                last_to_alias = to_alias
            elif to_node_already_exists:
                # to_node zaten var - yeni from_node'u mevcut to_node'a bağla
                # Bu CARTESIAN PRODUCT'ı önler!
                if step.from_node in self._node_aliases:
                    main_patterns.append(f"({from_alias}){rel_pattern}({to_alias})")
                else:
                    main_patterns.append(f"({from_alias}:{step.from_node}){rel_pattern}({to_alias})")
            elif step.from_node in self._node_aliases:
                # Star pattern: mevcut node'dan yeni node'a
                main_patterns.append(f"({from_alias}){rel_pattern}({to_alias}:{step.to_node})")
            else:
                # Tamamen yeni bir başlangıç noktası
                main_patterns.append(f"({from_alias}:{step.from_node}){rel_pattern}({to_alias}:{step.to_node})")
                last_to_node = step.to_node
        
        return ",\n      ".join(main_patterns)
    
    def _build_post_filter_traversal(self, traversal: List[TraversalStep], chunk_alias: str) -> Optional[str]:
        """
        Post-filtering için traversal pattern oluştur.
        
        Vector index'ten gelen chunk'tan başlayarak diğer node'lara bağlan.
        Örnek: chunk zaten var → MATCH (chunk)-[:PART_OF]->(doc:Document)<-[:DOCUMENTED_IN]-(policy:Policy)
        """
        if not traversal:
            return None
        
        # Chunk'tan başlayan path'leri bul
        chunk_steps = [s for s in traversal if s.from_node == "Chunk" or s.to_node == "Chunk"]
        
        if not chunk_steps:
            # Traversal Chunk'ı içermiyorsa, doğrudan pattern kur
            # Bu durumda muhtemelen Policy->Document->Chunk gibi bir yapı var
            # Chunk'tan geriye doğru git
            return self._build_reverse_traversal(traversal, chunk_alias)
        
        # Chunk'tan başlayan traversal pattern
        # ÖNEMLİ: Önce Chunk ile ilgili step'leri işle, sonra diğerlerini
        patterns = []
        visited = {"Chunk"}
        remaining_steps = list(traversal)
        
        # PASS 1: Chunk ile doğrudan bağlantılı step'ler
        for step in list(remaining_steps):
            if step.from_node == "Chunk":
                # Chunk -> X yönünde
                to_alias = self._get_node_alias(step.to_node)
                if step.direction == "outgoing":
                    rel_pattern = f"-[:{step.relation}]->"
                else:
                    rel_pattern = f"<-[:{step.relation}]-"
                patterns.append(f"({chunk_alias}){rel_pattern}({to_alias}:{step.to_node})")
                visited.add(step.to_node)
                remaining_steps.remove(step)
            elif step.to_node == "Chunk":
                # X -> Chunk yönünde (ters: incoming direction demek Chunk<-X)
                from_alias = self._get_node_alias(step.from_node)
                if step.direction == "incoming":
                    # Document -[:PART_OF]-> Chunk (incoming) = Chunk <-[:PART_OF]- Document
                    # Ama biz chunk'tan başlıyoruz: (chunk)-[:PART_OF]->(document)
                    rel_pattern = f"-[:{step.relation}]->"
                else:
                    rel_pattern = f"<-[:{step.relation}]-"
                patterns.append(f"({chunk_alias}){rel_pattern}({from_alias}:{step.from_node})")
                visited.add(step.from_node)
                remaining_steps.remove(step)
        
        # PASS 2+: Zinciri genişlet - visited node'lara bağlı step'leri ekle
        max_iterations = len(remaining_steps) + 1
        for _ in range(max_iterations):
            if not remaining_steps:
                break
            
            for step in list(remaining_steps):
                if step.from_node in visited and step.to_node not in visited:
                    # Visited node'dan yeni node'a
                    from_alias = self._node_aliases.get(step.from_node, step.from_node.lower())
                    to_alias = self._get_node_alias(step.to_node)
                    if step.direction == "outgoing":
                        rel_pattern = f"-[:{step.relation}]->"
                    else:
                        rel_pattern = f"<-[:{step.relation}]-"
                    patterns.append(f"({from_alias}){rel_pattern}({to_alias}:{step.to_node})")
                    visited.add(step.to_node)
                    remaining_steps.remove(step)
                elif step.to_node in visited and step.from_node not in visited:
                    # Yeni node'dan visited node'a
                    to_alias = self._node_aliases.get(step.to_node, step.to_node.lower())
                    from_alias = self._get_node_alias(step.from_node)
                    if step.direction == "outgoing":
                        # Policy -[:DOCUMENTED_IN]-> Document = (document)<-[:DOCUMENTED_IN]-(policy)
                        rel_pattern = f"<-[:{step.relation}]-"
                    else:
                        rel_pattern = f"-[:{step.relation}]->"
                    patterns.append(f"({to_alias}){rel_pattern}({from_alias}:{step.from_node})")
                    visited.add(step.from_node)
                    remaining_steps.remove(step)
        
        if patterns:
            return "MATCH " + ", ".join(patterns)
        return None
    
    def _build_reverse_traversal(self, traversal: List[TraversalStep], chunk_alias: str) -> Optional[str]:
        """
        Policy->Document->Chunk gibi yapılarda Chunk'tan geriye doğru traversal.
        
        Örnek DSL: Policy -[:DOCUMENTED_IN]-> Document <-[:PART_OF]- Chunk
        Sonuç: MATCH (chunk)-[:PART_OF]->(document:Document)<-[:DOCUMENTED_IN]-(policy:Policy)
        """
        # Document'a bağlanan step'i bul
        doc_step = next((s for s in traversal if s.to_node == "Document" or s.from_node == "Document"), None)
        
        if not doc_step:
            return None
        
        # Chunk -> Document bağlantısı (varsayılan PART_OF)
        doc_alias = self._get_node_alias("Document")
        pattern_parts = [f"({chunk_alias})-[:PART_OF]->({doc_alias}:Document)"]
        
        # Document'a bağlanan diğer node'lar
        for step in traversal:
            if step.to_node == "Document" and step.from_node != "Chunk":
                from_alias = self._get_node_alias(step.from_node)
                if step.direction == "outgoing":
                    # Policy -[:DOCUMENTED_IN]-> Document  =>  (doc)<-[:DOCUMENTED_IN]-(policy:Policy)
                    pattern_parts.append(f"({doc_alias})<-[:{step.relation}]-({from_alias}:{step.from_node})")
                else:
                    pattern_parts.append(f"({doc_alias})-[:{step.relation}]->({from_alias}:{step.from_node})")
        
        return "MATCH " + ", ".join(pattern_parts)
    
    def _build_where_clause(self, filters: List[Filter]) -> Tuple[str, Dict[str, Any]]:
        """WHERE clause oluştur"""
        if not filters:
            return "", {}
        
        conditions = []
        params = {}
        
        for i, filter_obj in enumerate(filters):
            condition, filter_params = self._build_filter_condition(filter_obj)
            
            if i == 0:
                conditions.append(condition)
            else:
                conditions.append(f"{filter_obj.logical_op} {condition}")
            
            params.update(filter_params)
        
        return "WHERE " + " ".join(conditions), params
    
    def _build_filter_condition(self, filter_obj: Filter) -> Tuple[str, Dict[str, Any]]:
        """Tek bir filter condition oluştur"""
        node_alias = self._get_node_alias(filter_obj.node)
        prop = f"{node_alias}.{filter_obj.property}"
        params = {}
        
        op = filter_obj.operator
        param_name = self._get_next_param_name()
        
        # String arama operatörleri için apoc.text.clean() kullan (fuzzy matching)
        # Bu boşlukları, özel karakterleri ve case'i normalize eder
        is_string_search = op in [
            FilterOperator.CONTAINS, 
            FilterOperator.STARTS_WITH, 
            FilterOperator.ENDS_WITH
        ]
        
        if is_string_search and isinstance(filter_obj.value, str):
            # apoc.text.clean() ile fuzzy matching
            # apoc.text.clean(): lowercase + özel karakter/boşluk temizleme
            prop_expr = f"apoc.text.clean({prop})"
            value = filter_obj.value.lower()  # Value'yu da lowercase yap
            params[param_name] = value
            
            if op == FilterOperator.CONTAINS:
                return f"{prop_expr} CONTAINS apoc.text.clean(${param_name})", params
            elif op == FilterOperator.STARTS_WITH:
                return f"{prop_expr} STARTS WITH apoc.text.clean(${param_name})", params
            elif op == FilterOperator.ENDS_WITH:
                return f"{prop_expr} ENDS WITH apoc.text.clean(${param_name})", params
        
        # EQUALS ve NOT_EQUALS için de apoc.text.clean() kullan (string ise)
        if op in [FilterOperator.EQUALS, FilterOperator.NOT_EQUALS]:
            if isinstance(filter_obj.value, str) and not filter_obj.case_sensitive:
                # apoc.text.clean() ile fuzzy matching - CONTAINS ile tutarlı
                prop_expr = f"apoc.text.clean({prop})"
                value = filter_obj.value.lower()
                params[param_name] = value
                operator_symbol = "=" if op == FilterOperator.EQUALS else "<>"
                return f"{prop_expr} {operator_symbol} apoc.text.clean(${param_name})", params
            else:
                params[param_name] = filter_obj.value
                operator_symbol = "=" if op == FilterOperator.EQUALS else "<>"
                return f"{prop} {operator_symbol} ${param_name}", params
        
        # Diğer operatörler için standart işlem (sayısal değerler vb.)
        value = filter_obj.value
        
        if op == FilterOperator.GREATER_THAN:
            params[param_name] = value
            return f"{prop} > ${param_name}", params
        
        elif op == FilterOperator.LESS_THAN:
            params[param_name] = value
            return f"{prop} < ${param_name}", params
        
        elif op == FilterOperator.GREATER_EQUAL:
            params[param_name] = value
            return f"{prop} >= ${param_name}", params
        
        elif op == FilterOperator.LESS_EQUAL:
            params[param_name] = value
            return f"{prop} <= ${param_name}", params
        
        elif op == FilterOperator.IN:
            params[param_name] = value  # value should be a list
            return f"{prop} IN ${param_name}", params
        
        elif op == FilterOperator.IS_NULL:
            return f"{prop} IS NULL", params
        
        elif op == FilterOperator.IS_NOT_NULL:
            return f"{prop} IS NOT NULL", params
        
        elif op == FilterOperator.REGEX:
            params[param_name] = value
            return f"{prop} =~ ${param_name}", params
        
        else:
            # Fallback to equals with apoc.text.clean for strings
            if isinstance(filter_obj.value, str) and not filter_obj.case_sensitive:
                params[param_name] = filter_obj.value.lower()
                return f"apoc.text.clean({prop}) = apoc.text.clean(${param_name})", params
            else:
                params[param_name] = value
                return f"{prop} = ${param_name}", params
    
    def _build_return_clause(self, return_spec: Optional[ReturnSpec], 
                             aggregate: Optional[AggregateSpec]) -> str:
        """RETURN clause oluştur"""
        if aggregate:
            # Aggregation return'ü ayrı handle edilir
            return ""
        
        if not return_spec:
            return "RETURN *"
        
        parts = []
        
        for node in return_spec.nodes:
            # Relation-based alias'lar var mı kontrol et
            # (Bu node için birden fazla farklı alias olabilir: startDate, endDate gibi)
            relation_based_aliases = self._get_all_aliases_for_node(node)
            
            if relation_based_aliases:
                # Tüm relation-based alias'ları kullan
                for alias, relation_hint in relation_based_aliases:
                    props = return_spec.properties.get(node, [])
                    if not props:
                        parts.append(alias)
                    else:
                        for prop in props:
                            # Alias prefix ekle (örn: startDate.year vs endDate.year)
                            prop_ref = f"{alias}.{prop}"
                            parts.append(prop_ref)
            else:
                # Standart alias kullan
                alias = self._get_node_alias(node)
                props = return_spec.properties.get(node, [])
                
                if not props:
                    # Tüm node'u döndür
                    parts.append(alias)
                else:
                    # Sadece belirtilen property'leri döndür
                    for prop in props:
                        prop_ref = f"{alias}.{prop}"
                        # Alias varsa kullan
                        prop_alias = return_spec.aliases.get(f"{node}.{prop}")
                        if prop_alias:
                            parts.append(f"{prop_ref} AS {prop_alias}")
                        else:
                            parts.append(prop_ref)
        
        distinct = "DISTINCT " if return_spec.distinct else ""
        return f"RETURN {distinct}{', '.join(parts)}"
    
    def _get_all_aliases_for_node(self, node: str) -> List[Tuple[str, str]]:
        """
        Bir node için tüm relation-based alias'ları döndür.
        
        Returns:
            List of (alias, relation) tuples.
            Örnek: [("startDate", "HAS_START_DATE"), ("endDate", "HAS_END_DATE")]
        """
        aliases = []
        for (from_node, relation, to_node), alias in self._relation_aliases.items():
            if to_node == node:
                aliases.append((alias, relation))
        return aliases
    
    def _build_aggregate_expression(self, agg: AggregateSpec, node_alias: str) -> str:
        """Aggregate expression oluştur"""
        func = agg.function
        
        if func == AggregateFunction.COUNT:
            if agg.property:
                return f"COUNT({node_alias}.{agg.property})"
            else:
                return f"COUNT(DISTINCT {node_alias})"
        
        elif func == AggregateFunction.SUM:
            return f"SUM({node_alias}.{agg.property})"
        
        elif func == AggregateFunction.AVG:
            return f"AVG({node_alias}.{agg.property})"
        
        elif func == AggregateFunction.MIN:
            return f"MIN({node_alias}.{agg.property})"
        
        elif func == AggregateFunction.MAX:
            return f"MAX({node_alias}.{agg.property})"
        
        elif func == AggregateFunction.COLLECT:
            if agg.property:
                return f"COLLECT({node_alias}.{agg.property})"
            else:
                return f"COLLECT({node_alias})"
        
        else:
            return f"COUNT({node_alias})"
    
    def _build_order_clause(self, order_by: OrderBySpec) -> str:
        """ORDER BY clause oluştur"""
        alias = self._get_node_alias(order_by.node)
        return f"ORDER BY {alias}.{order_by.property} {order_by.direction}"


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def compile_dsl(dsl: GraphDSL) -> CompilationResult:
    """DSL'i derle (convenience function)"""
    compiler = DSLCompiler()
    return compiler.compile(dsl)


def compile_json(json_str: str) -> CompilationResult:
    """JSON string'den DSL derle"""
    dsl = GraphDSL.from_json(json_str)
    return compile_dsl(dsl)


# ============================================================================
# TEST / DEMO
# ============================================================================

if __name__ == "__main__":
    from .graph_dsl import EXAMPLE_DSLS
    
    compiler = DSLCompiler()
    
    print("=" * 80)
    print("DSL → CYPHER COMPILER TEST")
    print("=" * 80)
    
    for name, dsl in EXAMPLE_DSLS.items():
        print(f"\n📋 {name}")
        print(f"   Intent: {dsl.intent.value}")
        print(f"   Description: {dsl.description}")
        
        result = compiler.compile(dsl)
        
        print(f"\n   📝 Generated Cypher:")
        for line in result.cypher.split("\n"):
            print(f"      {line}")
        
        if result.params:
            print(f"\n   📦 Parameters: {result.params}")
        
        if result.requires_embedding:
            print(f"   🧠 Requires Embedding: {result.query_text}")
        
        if result.warnings:
            print(f"   ⚠️ Warnings: {result.warnings}")
        
        print("-" * 80)

