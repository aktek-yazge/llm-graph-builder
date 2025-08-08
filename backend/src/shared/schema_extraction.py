from typing import List
from pydantic.v1 import BaseModel, Field
from src.llm import get_llm
from langchain_core.prompts import ChatPromptTemplate
import logging

class Schema(BaseModel):
    """Knowledge Graph Schema."""

    triplets: List[str] = Field(description="list of node labels and relationship types in a graph schema in <NodeType1>-<RELATIONSHIP_TYPE>-><NodeType2> format")

# PROMPT_TEMPLATE_WITH_SCHEMA = (
#     "You are an expert in schema extraction, especially for extracting graph schema information from various formats."
#     "Generate the generalized graph schema based on input text. Identify key entities and their relationships and "
#     "provide a generalized label for the overall context"
#     "Schema representations formats can contain extra symbols, quotes, or comments. Ignore all that extra markup."
#     "Only return the string types for nodes and relationships. Don't return attributes."
# )

PROMPT_TEMPLATE_WITH_SCHEMA = (
    "You are an expert in schema extraction and knowledge graph modeling. "
    "Your task is to extract a highly detailed, fine-grained graph schema from the given text. "
    "All node and relationship names MUST be in English, even if the source text is not. "
    "For each entity, try to break down into as many meaningful subtypes as possible (e.g., instead of just 'Person', use 'InsuredPerson', 'Beneficiary', 'Agent', etc. if context allows). "
    "For relationships, use descriptive English verbs or phrases (e.g., 'HAS_POLICY', 'COVERED_BY', 'ISSUED_BY'). "
    "If the text allows, extract hierarchical or part-of relationships (e.g., 'Policy-HAS_CLAUSE->Clause'). "
    "Do not infer relationships that are not clearly stated, but be as specific as possible with what is explicit. "
    "If a node or relationship can be further split or categorized, do so. "
    "Output should be as detailed as possible, maximizing the number of unique node and relationship types. "
    "Return only the schema, not instance data. "
    "Format: {\"triplets\": [\"<NodeType1>-<RELATIONSHIP_TYPE>-><NodeType2>\"]} "
    "Example: {\"triplets\": [\"InsuredPerson-HAS_POLICY->InsurancePolicy\", \"InsurancePolicy-HAS_CLAUSE->Clause\", \"Clause-COVERS->Risk\"]} "
    "Ignore all extra markup, comments, or non-schema information. "
)

# PROMPT_TEMPLATE_WITHOUT_SCHEMA = ( """
# You are an expert in schema extraction, especially in identifying node and relationship types from example texts.
# Analyze the following text and extract only the types of entities (node types) and their relationship types.
# Do not return specific instances or attributes — only abstract schema information.
# Return the result in the following format:
# {{"triplets": ["<NodeType1>-<RELATIONSHIP_TYPE>-><NodeType2>"]}}
# For example, if the text says “John works at Microsoft”, the output should be:
# {{"triplets": ["Person-WORKS_AT->Company"]}}"
# """
# )

PROMPT_TEMPLATE_WITHOUT_SCHEMA = (
    """
You are an expert in schema extraction and knowledge graph modeling.\n"
"Analyze the following text (from a single page of an insurance policy document or similar).\n"
"Extract only the types of entities (node types) and their relationship types that are explicitly and directly mentioned in the text.\n"
"All node and relationship names MUST be in English, regardless of the input language.\n"
"For each entity, break down into as many specific subtypes as possible (e.g., 'InsuredPerson', 'Beneficiary', 'Agent', etc.).\n"
"For relationships, use descriptive English verbs or phrases (e.g., 'HAS_POLICY', 'COVERED_BY', 'ISSUED_BY').\n"
"If the text allows, extract hierarchical or part-of relationships (e.g., 'Policy-HAS_CLAUSE->Clause').\n"
"Do NOT infer or assume relationships that are not clearly stated, but be as specific as possible with what is explicit.\n"
"If a node or relationship can be further split or categorized, do so.\n"
"Do not return specific instances or attributes — only abstract schema information.\n"
"Return the result in the following format:\n"
"{{\"triplets\": [\"<NodeType1>-<RELATIONSHIP_TYPE>-><NodeType2>\"]}}\n"
"For example, if the text says 'John works at Microsoft', the output should be:\n"
"{{\"triplets\": [\"Person-WORKS_AT->Company\"]}}\n"
"The more fine-grained and detailed the schema, the better.\n"
"""
)


PROMPT_TEMPLATE_FOR_LOCAL_STORAGE = ("""
You are an expert in knowledge graph modeling.
The user will provide a JSON input with two keys:
- "nodes": a list of objects with "label" and "value" representing node types in the schema.
- "rels": a list of objects with "label" and "value" representing relationship types in the schema.
Your task:
1. Understand the meaning of each node and relationship label.
2. Use them to generate logical triplets in the format:
<NodeType1>-<RELATIONSHIP_TYPE>-><NodeType2>
3. Only return a JSON list of strings like:
["User-ANSWERED->Question", "Question-ACCEPTED->Answer"]
Make sure each triplet is semantically meaningful.
"""
)

def get_schema_local_storage(input_text,llm):
    prompt = ChatPromptTemplate.from_messages(
    [("system", PROMPT_TEMPLATE_FOR_LOCAL_STORAGE), ("user", "{text}")]
    )
    
    runnable = prompt | llm.with_structured_output(
        schema=Schema,
        method="function_calling",
        include_raw=False,
    )
    
    raw_schema = runnable.invoke({"text": input_text})
    return raw_schema


def schema_extraction_from_text(input_text:str, model:str, is_schema_description_checked:bool,is_local_storage:bool):
    try:
        llm, model_name = get_llm(model)
        if str(is_local_storage).lower().strip() == "true":
            raw_schema = get_schema_local_storage(input_text,llm)
            return raw_schema
        if str(is_schema_description_checked).lower().strip() == "true":
            schema_prompt = PROMPT_TEMPLATE_WITH_SCHEMA
        else:
            schema_prompt = PROMPT_TEMPLATE_WITHOUT_SCHEMA
        prompt = ChatPromptTemplate.from_messages(
        [("system", schema_prompt), ("user", "{text}")]
        )
        
        runnable = prompt | llm.with_structured_output(
            schema=Schema,
            method="function_calling",
            include_raw=False,
        )

        raw_schema = runnable.invoke({"text": input_text})
        if raw_schema:
            return raw_schema
        else:
            raise Exception("Unable to get schema from text for given model")
    except Exception as e:
        logging.info(str(e))
        raise Exception(str(e))