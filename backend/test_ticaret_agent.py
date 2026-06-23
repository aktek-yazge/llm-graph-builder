#!/usr/bin/env python3
"""
Ticaret Sicili ReAct Agent — kademeli smoke test.

Çalıştırma:
    cd backend && .venv/bin/python test_ticaret_agent.py
    cd backend && .venv/bin/python test_ticaret_agent.py --ask "Aksa kaç belgeye sahip?"

Adımlar:
  1) import (react_agent + ticaret prompt'ları)
  2) Neo4j (neo4j-tsg) bağlantısı + canlı şema çıkarımı
  3) ticaret domain system prompt'unun kurulması (şema enjekte edilmiş)
  4) (opsiyonel) MCP sunucusu + OPENAI_API_KEY varsa uçtan uca soru
"""
import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()


def ok(msg): print(f"✅ {msg}")
def info(msg): print(f"ℹ️  {msg}")
def fail(msg): print(f"❌ {msg}")


def step1_imports():
    from src.langchain_deepagents.react_agent import ReactAgent, stream_react_agent_response  # noqa
    from src.langchain_deepagents.prompts import get_domain_prompts
    p = get_domain_prompts("ticaret", "cypher")
    assert {"system_base", "tool_usage", "content"} <= set(p.keys())
    assert "ticaret" in ReactAgent.VALID_DOMAINS
    ok("import + ticaret domain kayıtlı + prompt'lar yüklendi")
    return ReactAgent, stream_react_agent_response


def step2_graph():
    # Üretim yolu (chat_bot_stream) ile aynı: refresh_schema=False
    # (neo4j-tsg konteynerinde APOC yok; şemayı schema_cache lightweight fallback çıkarır)
    from src.shared.common_fn import create_graph_database_connection
    graph = create_graph_database_connection(
        os.environ["NEO4J_URI"],
        os.environ["NEO4J_USERNAME"],
        os.environ["NEO4J_PASSWORD"],
        os.environ.get("NEO4J_DATABASE", "neo4j"),
    )
    cnt = graph.query("MATCH (d:Document) RETURN count(d) AS c")[0]["c"]
    ok(f"Neo4j bağlantısı OK — {cnt} Document düğümü")
    return graph


def step3_prompt(ReactAgent, graph):
    agent = ReactAgent(graph, domain="ticaret")
    # _build_system_prompt'a verilecek şema metnini agent kendi çekiyor;
    # burada doğrudan şema çıkarımını ve prompt kurulumunu deniyoruz.
    schema_info = ""
    try:
        from src.shared.schema_cache import get_cached_schema
        schema_info = get_cached_schema(os.environ["NEO4J_URI"], graph) or ""
    except Exception as e:
        info(f"get_cached_schema atlandı: {e}")
    if not schema_info:
        from src.schema_extractor import get_compact_schema
        schema_info = get_compact_schema(graph)
    prompt = agent._build_system_prompt(schema_info)
    assert "TİCARET SİCİLİ" in prompt and "VERİTABANI ŞEMASI" in prompt
    ok(f"System prompt kuruldu — {len(prompt)} karakter")
    info("Şema çıkarımı (ilk 400 krk):\n" + schema_info[:400])
    return agent


async def step4_e2e(stream_fn, graph, question):
    if not os.environ.get("OPENAI_API_KEY") or "SENIN_" in os.environ.get("OPENAI_API_KEY", ""):
        info("OPENAI_API_KEY yok → e2e atlanıyor (prompt/şema testi yeterli)")
        return
    info(f"E2E soru: {question}")
    final = ""
    async for chunk in stream_fn(
        question=question, graph=graph, session_id="smoke-test",
        domain="ticaret", model=os.environ.get("REACT_MODEL", "gpt-4.1"),
    ):
        t = chunk.get("type")
        if t == "thinking_step":
            info(chunk.get("message", ""))
        elif t == "final_response":
            final = str(chunk.get("content") or "")
        elif t == "message_chunk" and not final:
            final += str(chunk.get("content") or "")
    print("\n🎯 CEVAP:\n" + (final[:1500] if final else "(boş)"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ask", default="Sistemde toplam kaç belge ve kaç şirket var?")
    args = ap.parse_args()

    ReactAgent, stream_fn = step1_imports()
    graph = step2_graph()
    step3_prompt(ReactAgent, graph)
    asyncio.run(step4_e2e(stream_fn, graph, args.ask))
    print("\n✅ Smoke test tamamlandı.")


if __name__ == "__main__":
    sys.exit(main())
