#!/usr/bin/env python3
from neo4j import GraphDatabase
import os, json

NEO_URL = os.getenv("NEO_URL", "bolt://localhost:7687")
NEO_USER = os.getenv("NEO_USER", "neo4j")
NEO_PASS = os.getenv("NEO_PASS", "qwerty5555")

DRV = GraphDatabase.driver(NEO_URL, auth=(NEO_USER, NEO_PASS))
Q = "ayça"

def sanitize(obj):
    """Recursively convert non-JSON-serializable objects to serializable forms.
    - neo4j DateTime / Python datetime -> ISO string
    - bytes -> decode as utf-8 with replacement
    - other unknown objects -> str(obj)
    """
    # primitives
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    # dict-like
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    # list/tuple
    if isinstance(obj, (list, tuple)):
        return [sanitize(v) for v in obj]
    # bytes
    if isinstance(obj, (bytes, bytearray)):
        try:
            return obj.decode('utf-8')
        except Exception:
            return obj.decode('utf-8', errors='replace')
    # objects with isoformat (datetime-like)
    if hasattr(obj, 'isoformat'):
        try:
            return obj.isoformat()
        except Exception:
            return str(obj)
    # fallback to str
    return str(obj)

out = []
with DRV.session() as s:
    query = '''
    MATCH (d:Document)
    WHERE toLower(coalesce(d.fileName, '')) CONTAINS toLower($q)
       OR toLower(coalesce(d.documentName, '')) CONTAINS toLower($q)
       OR toLower(coalesce(d.owner, '')) CONTAINS toLower($q)
    RETURN properties(d) AS props, id(d) AS id LIMIT 5000
    '''
    res = s.run(query, q=Q)
    for r in res:
        props = r.get('props') or {}
        props['_id'] = r.get('id')
        out.append(sanitize(props))

DRV.close()

out_path = os.path.join(os.path.dirname(__file__), 'ayca_documents.json')
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print(f"Wrote {len(out)} documents to {out_path}")
