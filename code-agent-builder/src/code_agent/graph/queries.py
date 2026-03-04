"""Reusable Cypher query templates for common code graph operations."""

PROJECT_OVERVIEW = """
MATCH (m:Module)
WITH count(m) AS modules
MATCH (c:Class)
WITH modules, count(c) AS classes
MATCH (f:Function)
WITH modules, classes, count(f) AS functions
RETURN modules, classes, functions
"""

MODULE_STRUCTURE = """
MATCH (m:Module {path: $path})
OPTIONAL MATCH (m)-[:CONTAINS]->(f:Function)
OPTIONAL MATCH (m)-[:CONTAINS]->(c:Class)
OPTIONAL MATCH (m)-[:IMPORTS]->(imp:Module)
RETURN m.path AS module_path,
       m.name AS module_name,
       m.language AS language,
       m.line_count AS line_count,
       m.summary AS summary,
       collect(DISTINCT {name: f.name, signature: f.signature, is_async: f.is_async, purpose: f.purpose}) AS functions,
       collect(DISTINCT {name: c.name, docstring: c.docstring, design_pattern: c.design_pattern}) AS classes,
       collect(DISTINCT imp.path) AS imports
"""

FIND_CALLERS = """
MATCH (caller:Function)-[:CALLS]->(target:Function)
WHERE target.name = $name OR target.qualified_name = $name
RETURN caller.qualified_name AS caller,
       caller.signature AS signature,
       target.qualified_name AS target
"""

FIND_DEPENDENCIES = """
MATCH path = (m:Module {path: $path})-[:IMPORTS*1..%d]->(dep:Module)
RETURN [n IN nodes(path) | n.path] AS chain
"""

CLASS_HIERARCHY = """
MATCH path = (child:Class)-[:INHERITS*1..5]->(parent:Class)
WHERE child.name = $name OR parent.name = $name
RETURN [n IN nodes(path) | {name: n.name, qualified_name: n.qualified_name}] AS hierarchy
"""

SEARCH_SYMBOLS_DEFAULT = """
MATCH (n:%s)
WHERE n.name CONTAINS $query OR n.qualified_name CONTAINS $query
RETURN n.qualified_name AS qualified_name,
       n.name AS name,
       labels(n)[0] AS type
LIMIT 50
"""

SEARCH_SYMBOLS_MODULE = """
MATCH (n:Module)
WHERE n.name CONTAINS $query OR n.path CONTAINS $query
RETURN n.path AS qualified_name,
       n.name AS name,
       'Module' AS type
LIMIT 50
"""

MODULE_LIST = """
MATCH (m:Module)
RETURN m.path AS path, m.name AS name, m.language AS language, m.line_count AS line_count
ORDER BY m.path
"""
