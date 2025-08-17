# Lokal test: intelligent_agent create_llm_prompt_structure without calling LLM
import sys
import os

# ensure src is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'src')))

from src.intelligent_agent import AgentState, ChunkInfo, IntelligentAgent


def main():
    # Create an agent instance without running __init__ to avoid LLM/Neo4j calls
    agent = object.__new__(IntelligentAgent)

    # Minimal attributes used by create_llm_prompt_structure
    agent.successful_findings = [
        {"iteration": 1, "action": "entity_search", "finding": "Found entity Ayça", "relevance_score": 0.82, "timestamp": "Adım 1"},
        {"iteration": 2, "action": "vector_search", "finding": "Found 3 similar chunks", "relevance_score": 0.65, "timestamp": "Adım 2"}
    ]
    agent.token_usage = {"input_tokens": 15, "output_tokens": 8, "total_tokens": 23}
    agent.context_memory = "Previously found: Ayça -> policies; high relevance chunks present."

    # Create a fake AgentState and populate with chunks/entities
    state = AgentState(question="Ayça hanımın poliçeleri hakkında bilgi")

    c1 = ChunkInfo(chunk_id="c1", text="Bu bir örnek chunk metni. Ayça hanımın poliçe bilgileri burada.", page_number=2, document_name="Ayca_Police.pdf", relevance_score=0.82,
                   split_texts=["Ayça poliçe detayı 1", "Ayça poliçe detayı 2"], split_scores=[0.82, 0.45])
    c2 = ChunkInfo(chunk_id="c2", text="Başka bir metin örneği, DASK ile ilgili bilgiler.", page_number=5, document_name="Dask_Info.pdf", relevance_score=0.65,
                   split_texts=["DASK açıklaması 1"], split_scores=[0.65])

    state.discovered_chunks.append(c1)
    state.discovered_chunks.append(c2)

    state.discovered_entities.append({"id": "Ayca_Dinckok", "type": "Person"})
    state.discovered_entities.append({"id": "DASK", "type": "PolicyType"})

    # Call the prompt generation
    prompt = IntelligentAgent.create_llm_prompt_structure(agent, state, "Ayça hanımın 2020 yılında kaç poliçesi var?")

    # Basic checks
    checks = [
        "TOP RELEVANT CHUNKS",
        "DISCOVERED ENTITIES",
        "LLM PROMPT TEMPLATE",
        "USER QUESTION",
        "SUCCESSFUL FINDINGS"
    ]

    missing = [s for s in checks if s not in prompt.upper()]

    print("--- Generated prompt (truncated) ---\n")
    print(prompt[:2000])
    print("\n--- End of prompt ---\n")

    if missing:
        print("MISSING SECTIONS:", missing)
        raise SystemExit(2)
    else:
        print("CHECKS OK: All expected sections found in llm_prompt_structure.")


if __name__ == '__main__':
    main()
