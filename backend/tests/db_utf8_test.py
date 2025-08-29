# check_utf8_documents.py
from neo4j import GraphDatabase
import os, json, sys

NEO_URL = os.getenv("NEO_URL", "bolt://localhost:7687")
NEO_USER = os.getenv("NEO_USER", "neo4j")
NEO_PASS = os.getenv("NEO_PASS", "qwerty5555")

drv = GraphDatabase.driver(NEO_URL, auth=(NEO_USER, NEO_PASS))
bad = []
with drv.session() as s:
    res = s.run("MATCH (d:Document) RETURN d.fileName AS fn, id(d) AS id LIMIT 5000")
    for r in res:
        fn = r["fn"]
        nid = r["id"]
        if fn is None:
            continue
        if not isinstance(fn, str):
            bad.append((nid, fn, "not-a-str"))
            continue
        # Literal \u sequences stored as text?
        if "\\u" in fn:
            bad.append((nid, fn, "contains-literal-backslash-u"))
            continue
        # Try encoding to UTF-8
        try:
            fn.encode("utf-8")
        except UnicodeEncodeError as e:
            bad.append((nid, fn, f"encode-error:{e}"))
        # Optional: print human-readable form
        print(json.dumps({"id": nid, "fileName": fn}, ensure_ascii=False))
drv.close()
if bad:
    print("PROBLEMS FOUND:")
    for b in bad[:50]:
        print(b)
    sys.exit(2)
print("All checked fileName values encoded to UTF-8 and no literal \\u sequences found.")