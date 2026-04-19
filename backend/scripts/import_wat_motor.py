"""
WAT Motor Maintenance Data Import Script

Bu script WAT Motor bakım Excel verilerini Neo4j'ye aktarır.

Graph Şeması:
- Task (+ embedding) - Bakım görevi
- Person - Kişi (talep eden / teknisyen)
- Equipment - Ekipman
- CostCenter - Maliyet merkezi
- Status - Durum
- ImpactType - Duruş etkisi

İlişkiler:
- (Person)-[:CREATED_REQUEST]->(Task)
- (Person)-[:WORKED_ON]->(Task)
- (Task)-[:FOR_EQUIPMENT]->(Equipment)
- (Task)-[:HAS_STATUS]->(Status)
- (Task)-[:BELONGS_TO]->(CostCenter)
- (Task)-[:HAS_IMPACT]->(ImpactType)
- (Equipment)-[:IN_DEPARTMENT]->(CostCenter)

Kullanım:
    cd backend
    python scripts/import_wat_motor.py --file data.xlsx --dry-run
    python scripts/import_wat_motor.py --file data.xlsx
    python scripts/import_wat_motor.py --file data.xlsx --merge-duplicates
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from typing import Any, Dict, List

# .env dosyasını yükle
from dotenv import load_dotenv
load_dotenv()

# Neo4j driver
from neo4j import GraphDatabase

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.excel_parser import MaintenanceTask, parse_maintenance_excel, print_parse_summary

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Environment variables
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Embedding settings
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSION = 1536
BATCH_SIZE = 100  # Embedding batch size


# =============================================================================
# EMBEDDING FUNCTIONS
# =============================================================================

def get_embeddings(texts: List[str]) -> List[List[float]]:
    """Get embeddings for a list of texts using OpenAI API"""
    if not OPENAI_API_KEY:
        logger.warning("⚠️ OPENAI_API_KEY not set, skipping embeddings")
        return [[] for _ in texts]
    
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        # Filter empty texts
        valid_texts = []
        valid_indices = []
        for i, text in enumerate(texts):
            if text and text.strip():
                valid_texts.append(text)
                valid_indices.append(i)
        
        if not valid_texts:
            return [[] for _ in texts]
        
        # Get embeddings in batches
        all_embeddings = [[] for _ in texts]
        
        for start in range(0, len(valid_texts), BATCH_SIZE):
            batch = valid_texts[start:start + BATCH_SIZE]
            response = client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=batch
            )
            
            for j, data in enumerate(response.data):
                idx = valid_indices[start + j]
                all_embeddings[idx] = data.embedding
        
        return all_embeddings
        
    except Exception as e:
        logger.error(f"❌ Embedding error: {e}")
        return [[] for _ in texts]


# =============================================================================
# NEO4J IMPORTER
# =============================================================================

class WatMotorImporter:
    """WAT Motor data importer to Neo4j"""
    
    def __init__(self, uri: str, username: str, password: str, database: str):
        self.driver = GraphDatabase.driver(uri, auth=(username, password))
        self.database = database
        self.import_timestamp = datetime.now().isoformat()
    
    def close(self):
        self.driver.close()
    
    def test_connection(self) -> bool:
        """Test Neo4j connection"""
        try:
            with self.driver.session(database=self.database) as session:
                result = session.run("RETURN 1 as test")
                result.single()
            logger.info("✅ Neo4j connection successful")
            return True
        except Exception as e:
            logger.error(f"❌ Neo4j connection failed: {e}")
            return False
    
    def create_constraints(self) -> None:
        """Create unique constraints for nodes"""
        constraints = [
            ("Task", "task_id"),
            ("Person", "name"),
            ("Equipment", "name"),
            ("CostCenter", "name"),
            ("Status", "name"),
            ("ImpactType", "name"),
        ]
        
        with self.driver.session(database=self.database) as session:
            for label, prop in constraints:
                try:
                    session.run(f"""
                        CREATE CONSTRAINT IF NOT EXISTS
                        FOR (n:{label})
                        REQUIRE n.{prop} IS UNIQUE
                    """)
                    logger.info(f"✅ Constraint created: {label}.{prop}")
                except Exception as e:
                    logger.warning(f"⚠️ Constraint {label}.{prop}: {e}")
    
    def create_vector_index(self) -> None:
        """Create vector index for Task embeddings"""
        with self.driver.session(database=self.database) as session:
            try:
                session.run(f"""
                    CREATE VECTOR INDEX task_embedding_index IF NOT EXISTS
                    FOR (t:Task)
                    ON t.embedding
                    OPTIONS {{
                        indexConfig: {{
                            `vector.dimensions`: {EMBEDDING_DIMENSION},
                            `vector.similarity_function`: 'cosine'
                        }}
                    }}
                """)
                logger.info("✅ Vector index created: task_embedding_index")
            except Exception as e:
                logger.warning(f"⚠️ Vector index: {e}")
    
    def import_tasks(self, tasks: List[MaintenanceTask], with_embeddings: bool = True) -> Dict[str, Any]:
        """Import tasks to Neo4j"""
        
        stats = {
            "tasks_created": 0,
            "tasks_updated": 0,
            "persons_created": 0,
            "equipment_created": 0,
            "cost_centers_created": 0,
            "statuses_created": 0,
            "impacts_created": 0,
            "embeddings_created": 0,
            "errors": []
        }
        
        # Get embeddings if enabled
        embeddings = []
        if with_embeddings:
            logger.info("🔄 Generating embeddings...")
            embedding_texts = [task.get_embedding_text() for task in tasks]
            embeddings = get_embeddings(embedding_texts)
            stats["embeddings_created"] = sum(1 for e in embeddings if e)
            logger.info(f"✅ Generated {stats['embeddings_created']} embeddings")
        
        with self.driver.session(database=self.database) as session:
            for i, task in enumerate(tasks):
                try:
                    embedding = embeddings[i] if embeddings and i < len(embeddings) else []
                    result = self._import_single_task(session, task, embedding)
                    
                    # Update stats
                    for key in ["tasks_created", "tasks_updated", "persons_created", 
                               "equipment_created", "cost_centers_created", 
                               "statuses_created", "impacts_created"]:
                        stats[key] += result.get(key, 0)
                    
                    if (i + 1) % 100 == 0:
                        logger.info(f"📊 Progress: {i + 1}/{len(tasks)} tasks imported")
                        
                except Exception as e:
                    stats["errors"].append({
                        "task_id": task.task_id,
                        "error": str(e)
                    })
                    logger.error(f"❌ Task {task.task_id}: {e}")
        
        return stats
    
    def _import_single_task(self, session, task: MaintenanceTask, embedding: List[float]) -> Dict[str, int]:
        """Import a single task with all related nodes"""
        
        result = {
            "tasks_created": 0,
            "tasks_updated": 0,
            "persons_created": 0,
            "equipment_created": 0,
            "cost_centers_created": 0,
            "statuses_created": 0,
            "impacts_created": 0,
        }
        
        # Build task properties
        task_props = {
            "task_id": task.task_id,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "assigned_at": task.assigned_at.isoformat() if task.assigned_at else None,
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "closed_at": task.closed_at.isoformat() if task.closed_at else None,
            "total_downtime_min": task.total_downtime_min,
            "total_time_min": task.total_time_min,
            "request_note": task.request_note,
            "technician_note": task.technician_note,
            "embedding_text": task.get_embedding_text() if embedding else None,
            "import_timestamp": self.import_timestamp,
        }
        
        # Add embedding if available
        if embedding:
            task_props["embedding"] = embedding
        
        # Create/Update Task
        task_result = session.run("""
            MERGE (t:Task {task_id: $task_id})
            ON CREATE SET t += $props, t._created = true
            ON MATCH SET t += $props, t._created = false
            RETURN t._created as created
        """, task_id=task.task_id, props=task_props).single()
        
        if task_result["created"]:
            result["tasks_created"] = 1
        else:
            result["tasks_updated"] = 1
        
        # Create Equipment and relationship
        if task.equipment_name:
            eq_result = session.run("""
                MERGE (e:Equipment {name: $name})
                ON CREATE SET e._created = true
                ON MATCH SET e._created = false
                WITH e
                MATCH (t:Task {task_id: $task_id})
                MERGE (t)-[:FOR_EQUIPMENT]->(e)
                RETURN e._created as created
            """, name=task.equipment_name, task_id=task.task_id).single()
            if eq_result and eq_result["created"]:
                result["equipment_created"] = 1
        
        # Create Status and relationship
        if task.status:
            st_result = session.run("""
                MERGE (s:Status {name: $name})
                ON CREATE SET s._created = true
                ON MATCH SET s._created = false
                WITH s
                MATCH (t:Task {task_id: $task_id})
                MERGE (t)-[:HAS_STATUS]->(s)
                RETURN s._created as created
            """, name=task.status, task_id=task.task_id).single()
            if st_result and st_result["created"]:
                result["statuses_created"] = 1
        
        # Create CostCenter and relationships
        if task.cost_center:
            cc_result = session.run("""
                MERGE (cc:CostCenter {name: $name})
                ON CREATE SET cc._created = true
                ON MATCH SET cc._created = false
                WITH cc
                MATCH (t:Task {task_id: $task_id})
                MERGE (t)-[:BELONGS_TO]->(cc)
                RETURN cc._created as created
            """, name=task.cost_center, task_id=task.task_id).single()
            if cc_result and cc_result["created"]:
                result["cost_centers_created"] = 1
            
            # Link Equipment to CostCenter
            if task.equipment_name:
                session.run("""
                    MATCH (e:Equipment {name: $eq_name})
                    MATCH (cc:CostCenter {name: $cc_name})
                    MERGE (e)-[:IN_DEPARTMENT]->(cc)
                """, eq_name=task.equipment_name, cc_name=task.cost_center)
        
        # Create ImpactType and relationship
        if task.impact_type:
            it_result = session.run("""
                MERGE (it:ImpactType {name: $name})
                ON CREATE SET it._created = true
                ON MATCH SET it._created = false
                WITH it
                MATCH (t:Task {task_id: $task_id})
                MERGE (t)-[:HAS_IMPACT]->(it)
                RETURN it._created as created
            """, name=task.impact_type, task_id=task.task_id).single()
            if it_result and it_result["created"]:
                result["impacts_created"] = 1
        
        # Create Requester Person and relationship
        if task.requester_name:
            req_result = session.run("""
                MERGE (p:Person {name: $name})
                ON CREATE SET p._created = true
                ON MATCH SET p._created = false
                WITH p
                MATCH (t:Task {task_id: $task_id})
                MERGE (p)-[:CREATED_REQUEST]->(t)
                RETURN p._created as created
            """, name=task.requester_name, task_id=task.task_id).single()
            if req_result and req_result["created"]:
                result["persons_created"] += 1
        
        # Create Technician Person and relationship
        if task.technician_name:
            tech_result = session.run("""
                MERGE (p:Person {name: $name})
                ON CREATE SET p._created = true
                ON MATCH SET p._created = false
                WITH p
                MATCH (t:Task {task_id: $task_id})
                MERGE (p)-[:WORKED_ON]->(t)
                RETURN p._created as created
            """, name=task.technician_name, task_id=task.task_id).single()
            if tech_result and tech_result["created"]:
                result["persons_created"] += 1
        
        return result
    
    def get_import_stats(self) -> Dict[str, Any]:
        """Get current database statistics"""
        with self.driver.session(database=self.database) as session:
            # Count each label separately to handle empty database
            stats = {}
            for label, key in [("Task", "tasks"), ("Person", "persons"), 
                              ("Equipment", "equipment"), ("CostCenter", "cost_centers"),
                              ("Status", "statuses"), ("ImpactType", "impacts")]:
                result = session.run(f"MATCH (n:{label}) RETURN count(n) as cnt").single()
                stats[key] = result["cnt"] if result else 0
            
            return stats


# =============================================================================
# DUPLICATE MERGE (Person nodes)
# =============================================================================

def merge_person_duplicates(importer: WatMotorImporter) -> Dict[str, Any]:
    """
    Merge duplicate Person nodes using semantic similarity.
    Uses the existing merge_duplicate_entities infrastructure.
    """
    logger.info("🔄 Starting Person duplicate merge...")
    
    # Simple text-based duplicate merge for Person
    # (Exact match after normalization)
    with importer.driver.session(database=importer.database) as session:
        # Find duplicates (case-insensitive, trimmed)
        duplicates = session.run("""
            MATCH (p:Person)
            WITH toLower(trim(p.name)) as normalized, collect(p) as persons
            WHERE size(persons) > 1
            RETURN normalized, [p in persons | {id: id(p), name: p.name}] as duplicates
            ORDER BY size(persons) DESC
        """).data()
        
        if not duplicates:
            logger.info("✅ No Person duplicates found")
            return {"merged": 0, "groups": 0}
        
        logger.info(f"📊 Found {len(duplicates)} duplicate groups")
        
        merged_count = 0
        for group in duplicates:
            persons = group["duplicates"]
            master = persons[0]  # First one becomes master
            
            for duplicate in persons[1:]:
                # Merge relationships to master
                session.run("""
                    MATCH (master:Person) WHERE id(master) = $master_id
                    MATCH (dup:Person) WHERE id(dup) = $dup_id
                    
                    // Transfer CREATED_REQUEST relationships
                    OPTIONAL MATCH (dup)-[r1:CREATED_REQUEST]->(t:Task)
                    FOREACH (_ IN CASE WHEN r1 IS NOT NULL THEN [1] ELSE [] END |
                        MERGE (master)-[:CREATED_REQUEST]->(t)
                    )
                    
                    // Transfer WORKED_ON relationships
                    OPTIONAL MATCH (dup)-[r2:WORKED_ON]->(t2:Task)
                    FOREACH (_ IN CASE WHEN r2 IS NOT NULL THEN [1] ELSE [] END |
                        MERGE (master)-[:WORKED_ON]->(t2)
                    )
                    
                    // Delete duplicate
                    DETACH DELETE dup
                """, master_id=master["id"], dup_id=duplicate["id"])
                
                merged_count += 1
                logger.info(f"   ✅ Merged: '{duplicate['name']}' -> '{master['name']}'")
        
        logger.info(f"🎉 Merged {merged_count} duplicate Person nodes")
        return {"merged": merged_count, "groups": len(duplicates)}


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="WAT Motor Maintenance Data Import Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python scripts/import_wat_motor.py --file data.xlsx --dry-run
    python scripts/import_wat_motor.py --file data.xlsx
    python scripts/import_wat_motor.py --file data.xlsx --merge-duplicates
    python scripts/import_wat_motor.py --file data.xlsx --no-embeddings
        """
    )
    
    parser.add_argument("--file", "-f", required=True, help="Excel file path")
    parser.add_argument("--dry-run", action="store_true", help="Only analyze, don't import")
    parser.add_argument("--merge-duplicates", action="store_true", help="Merge duplicate Person nodes after import")
    parser.add_argument("--no-embeddings", action="store_true", help="Skip embedding generation")
    parser.add_argument("--sheet", default=0, help="Sheet name or index (default: 0)")
    parser.add_argument("--skip-rows", type=int, default=0, help="Rows to skip at beginning")
    
    args = parser.parse_args()
    
    print("\n" + "=" * 60)
    print("🏭 WAT MOTOR MAINTENANCE DATA IMPORT")
    print("=" * 60)
    
    # Parse Excel
    print(f"\n📂 File: {args.file}")
    
    try:
        result = parse_maintenance_excel(
            args.file,
            sheet_name=args.sheet if isinstance(args.sheet, int) or args.sheet.isdigit() else args.sheet,
            skip_rows=args.skip_rows
        )
    except Exception as e:
        print(f"❌ Excel parse error: {e}")
        sys.exit(1)
    
    print_parse_summary(result)
    
    if args.dry_run:
        print("\n🔍 DRY RUN - No changes will be made")
        print("=" * 60)
        return
    
    # Connect to Neo4j
    print(f"\n🔌 Connecting to Neo4j: {NEO4J_URI}")
    
    importer = WatMotorImporter(
        uri=NEO4J_URI,
        username=NEO4J_USERNAME,
        password=NEO4J_PASSWORD,
        database=NEO4J_DATABASE
    )
    
    try:
        if not importer.test_connection():
            sys.exit(1)
        
        # Get stats before import
        stats_before = importer.get_import_stats()
        print(f"\n📊 Database before import:")
        for key, value in stats_before.items():
            print(f"   {key}: {value}")
        
        # Create constraints and indexes
        print("\n🔧 Creating constraints and indexes...")
        importer.create_constraints()
        importer.create_vector_index()
        
        # Import tasks
        print(f"\n📥 Importing {len(result.tasks)} tasks...")
        import_stats = importer.import_tasks(
            result.tasks,
            with_embeddings=not args.no_embeddings
        )
        
        # Print import stats
        print("\n" + "=" * 60)
        print("📊 IMPORT RESULTS")
        print("=" * 60)
        print(f"   Tasks created: {import_stats['tasks_created']}")
        print(f"   Tasks updated: {import_stats['tasks_updated']}")
        print(f"   Persons created: {import_stats['persons_created']}")
        print(f"   Equipment created: {import_stats['equipment_created']}")
        print(f"   Cost Centers created: {import_stats['cost_centers_created']}")
        print(f"   Statuses created: {import_stats['statuses_created']}")
        print(f"   Impacts created: {import_stats['impacts_created']}")
        print(f"   Embeddings created: {import_stats['embeddings_created']}")
        
        if import_stats["errors"]:
            print(f"\n⚠️ Errors: {len(import_stats['errors'])}")
            for err in import_stats["errors"][:5]:
                print(f"   Task {err['task_id']}: {err['error'][:60]}")
        
        # Merge duplicates if requested
        if args.merge_duplicates:
            print("\n" + "-" * 60)
            merge_result = merge_person_duplicates(importer)
            print(f"   Duplicate groups: {merge_result['groups']}")
            print(f"   Persons merged: {merge_result['merged']}")
        
        # Get stats after import
        stats_after = importer.get_import_stats()
        print(f"\n📊 Database after import:")
        for key, value in stats_after.items():
            diff = value - stats_before.get(key, 0)
            diff_str = f" (+{diff})" if diff > 0 else ""
            print(f"   {key}: {value}{diff_str}")
        
        print("\n✅ Import completed successfully!")
        print("=" * 60)
        
    finally:
        importer.close()


if __name__ == "__main__":
    main()
