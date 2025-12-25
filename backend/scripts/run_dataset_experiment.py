#!/usr/bin/env python3
"""
Langfuse Dataset Experiment Runner - GERÇEK REACT AGENT İLE

Bu script, Langfuse'daki dataset'leri kullanarak GERÇEK LLM cevaplarını test eder.
Farklı prompt versiyonları veya modeller arasında karşılaştırma yapabilirsiniz.

Kullanım:
    cd backend
    uv run python scripts/run_dataset_experiment.py --dataset sigorta-qa-test --dry-run
    uv run python scripts/run_dataset_experiment.py --dataset sigorta-qa-test --model gpt-4o

Gereksinimler:
    - Langfuse aktif ve yapılandırılmış olmalı
    - Dataset önceden oluşturulmuş olmalı
    - Neo4j bağlantısı çalışıyor olmalı
"""

import os
import sys
import json
import asyncio
import argparse
from datetime import datetime
from typing import Optional, Dict, Any
import uuid

# Backend modüllerini import edebilmek için path ekle
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_dir)

# .env dosyasını yükle (override=True ile mevcut env'leri ezecek)
from dotenv import load_dotenv
env_file = os.path.join(backend_dir, ".env")
if os.path.exists(env_file):
    load_dotenv(env_file, override=True)
    print(f"✅ Loaded env from: {env_file}")

# ⚠️ EXPERIMENT için cache'i devre dışı bırak (gerçek LLM çağrısı yapılsın)
os.environ["QUERY_CACHE_ENABLED"] = "false"
os.environ["REDIS_CACHE_ENABLED"] = "false"
print("🚫 Cache disabled for experiment (QUERY_CACHE_ENABLED=false)")

from src.shared.langfuse_client import (
    get_langfuse, get_dataset, get_dataset_items, is_langfuse_enabled
)


async def run_single_question(
    question: str,
    model: str,
    session_id: str,
    question_id: str,
    graph: Any,
) -> Dict[str, Any]:
    """
    Tek bir soru için react_agent çalıştır ve sonucu döndür.
    """
    from src.langchain_deepagents.react_agent import stream_react_agent_response
    
    full_answer = ""
    total_tokens = {"input": 0, "output": 0}
    sources = []
    tool_calls = []
    error = None
    
    try:
        async for chunk in stream_react_agent_response(
            question=question,
            graph=graph,
            model=model,
            session_id=session_id,
            question_id=question_id,
            reasoning_effort=os.environ.get("REACT_REASONING_EFFORT", "low"),
            user_id=f"experiment-{session_id[:8]}",
        ):
            # Chunk tipine göre işle
            if isinstance(chunk, dict):
                chunk_type = chunk.get("type", "")
                
                # DEBUG: Tüm chunk tiplerini logla
                if os.environ.get("DEBUG_CHUNKS", "").lower() == "true":
                    print(f"      📦 Chunk: {chunk_type} - {str(chunk)[:100]}...")
                
                if chunk_type == "final_response":
                    # Final cevap
                    full_answer = chunk.get("content", "")
                    # Sources da bu chunk'ta geliyor
                    chunk_sources = chunk.get("sources", {})
                    if isinstance(chunk_sources, dict):
                        sources = chunk_sources.get("documents", [])
                    # Metrics içinde token bilgisi
                    metrics = chunk.get("metrics", {})
                    if metrics:
                        total_tokens["input"] += metrics.get("input_tokens", 0)
                        total_tokens["output"] += metrics.get("output_tokens", 0)
                    # Tool calls detayları (chunk'ta ayrı alan)
                    if "tool_calls_detail" in chunk:
                        tool_calls = chunk.get("tool_calls_detail", [])
                    
                elif chunk_type == "cache_hit":
                    # Cache'den geldi - logla
                    print(f"      🎯 CACHE HIT: {chunk.get('content', '')[:50]}...")
                    
                elif chunk_type == "thinking_step":
                    # Düşünme adımı - sadece loglama için
                    pass
                    
                elif chunk_type == "tool_call":
                    # Tool çağrısı
                    pass
                    
                elif chunk_type == "error":
                    error = chunk.get("message", "Unknown error")
                    
    except Exception as e:
        error = str(e)
    
    return {
        "answer": full_answer,
        "tokens": total_tokens,
        "sources": sources,
        "tool_calls": tool_calls,
        "error": error,
    }


async def run_experiment_async(
    dataset_name: str,
    experiment_name: Optional[str] = None,
    model: str = "gpt-4o",
    dry_run: bool = False,
    limit: Optional[int] = None,
):
    """
    Dataset üzerinde experiment çalıştır (async).
    
    Args:
        dataset_name: Test edilecek dataset adı
        experiment_name: Experiment adı (opsiyonel, otomatik oluşturulur)
        model: Kullanılacak model
        dry_run: True ise sadece dataset'i göster, test çalıştırma
        limit: Test edilecek maksimum soru sayısı (opsiyonel)
    """
    if not is_langfuse_enabled():
        print("❌ Langfuse aktif değil. LANGFUSE_ENABLED=true yapın.")
        return
    
    langfuse = get_langfuse()
    if not langfuse:
        print("❌ Langfuse bağlantısı kurulamadı.")
        return
    
    # Dataset'i getir
    print(f"\n📦 Dataset yükleniyor: {dataset_name}")
    try:
        dataset = get_dataset(dataset_name)
        if not dataset:
            print(f"❌ Dataset bulunamadı: {dataset_name}")
            return
    except Exception as e:
        print(f"❌ Dataset yüklenirken hata: {e}")
        return
    
    # Item'ları getir
    items = get_dataset_items(dataset_name)
    if limit:
        items = items[:limit]
    print(f"✅ {len(items)} test sorusu bulundu\n")
    
    if dry_run:
        print("📋 Dataset İçeriği (Dry Run):\n")
        print("-" * 60)
        for i, item in enumerate(items, 1):
            item_id = item.get("id", "unknown") if isinstance(item, dict) else getattr(item, 'id', 'unknown')
            input_data = item.get("input", {}) if isinstance(item, dict) else getattr(item, 'input', {})
            expected = item.get("expected_output") if isinstance(item, dict) else getattr(item, 'expected_output', None)
            metadata = item.get("metadata") if isinstance(item, dict) else getattr(item, 'metadata', None)
            
            print(f"\n#{i} - ID: {item_id}")
            print(f"   Input: {json.dumps(input_data, ensure_ascii=False, indent=2)}")
            if expected:
                print(f"   Expected: {json.dumps(expected, ensure_ascii=False, indent=2)}")
            if metadata:
                print(f"   Metadata: {metadata}")
        print("\n" + "-" * 60)
        print(f"\n💡 Gerçek testi çalıştırmak için --dry-run flag'ini kaldırın")
        return
    
    # Neo4j bağlantısı kur
    print("🔗 Neo4j bağlantısı kuruluyor...")
    try:
        from src.shared.common_fn import create_graph_database_connection
        
        neo4j_uri = os.environ.get("NEO4J_URI")
        neo4j_username = os.environ.get("NEO4J_USERNAME")
        neo4j_password = os.environ.get("NEO4J_PASSWORD")
        neo4j_database = os.environ.get("NEO4J_DATABASE", "neo4j")
        
        if not all([neo4j_uri, neo4j_username, neo4j_password]):
            print("❌ Neo4j bağlantı bilgileri eksik!")
            print("   NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD env variable'larını kontrol edin.")
            return
        
        graph = create_graph_database_connection(
            neo4j_uri, neo4j_username, neo4j_password, neo4j_database
        )
        print(f"✅ Neo4j bağlantısı kuruldu: {neo4j_uri}")
    except Exception as e:
        print(f"❌ Neo4j bağlantı hatası: {e}")
        return
    
    # Experiment adı oluştur
    if not experiment_name:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        experiment_name = f"{dataset_name}_{model}_{timestamp}"
    
    experiment_session_id = str(uuid.uuid4())
    
    print(f"\n🧪 Experiment başlatılıyor: {experiment_name}")
    print(f"   Model: {model}")
    print(f"   Session: {experiment_session_id[:8]}...")
    print("=" * 60)
    
    results = []
    start_time = datetime.now()
    
    for i, item in enumerate(items, 1):
        # Item dict veya obje olabilir
        item_id = item.get("id", "unknown") if isinstance(item, dict) else getattr(item, 'id', 'unknown')
        input_data = item.get("input", {}) if isinstance(item, dict) else getattr(item, 'input', {})
        
        # Input içinden question'ı al
        if isinstance(input_data, dict):
            question = input_data.get("question", json.dumps(input_data, ensure_ascii=False))
        else:
            question = str(input_data)
        
        question_id = str(uuid.uuid4())
        
        print(f"\n[{i}/{len(items)}] 🔍 Testing: {question[:60]}...")
        
        try:
            # Gerçek react_agent çalıştır
            result = await run_single_question(
                question=question,
                model=model,
                session_id=experiment_session_id,
                question_id=question_id,
                graph=graph,
            )
            
            if result["error"]:
                print(f"   ❌ Hata: {result['error']}")
                status = "error"
            else:
                answer_preview = result["answer"][:100].replace("\n", " ")
                print(f"   ✅ Cevap: {answer_preview}...")
                print(f"   📊 Tokens: {result['tokens']['input']} in / {result['tokens']['output']} out")
                status = "success"
            
            # Dataset item'ı trace ile ilişkilendir
            try:
                # Son trace'i bul ve link et (Langfuse otomatik kaydetti)
                # Not: react_agent zaten Langfuse'a trace kaydediyor
                # Bu link sadece dataset ile ilişkilendirme için
                pass  # react_agent zaten trace oluşturuyor
            except Exception as link_error:
                print(f"   ⚠️ Link hatası: {link_error}")
            
            results.append({
                "item_id": item_id,
                "question": question,
                "answer": result["answer"],
                "tokens": result["tokens"],
                "sources": result["sources"],
                "tool_calls": result.get("tool_calls", []),
                "status": status,
                "error": result["error"],
            })
            
            # 🧑‍⚖️ LLM-as-Judge: Cypher sorgularını değerlendir
            evaluation = None
            if status == "success" and result.get("tool_calls"):
                try:
                    from src.shared.feedback import evaluate_cypher_queries
                    
                    evaluation = await evaluate_cypher_queries(
                        question=question,
                        tool_calls=result.get("tool_calls", []),
                        response=result["answer"],
                    )
                    
                    eval_score = evaluation.get("score", 0.5)
                    is_correct = evaluation.get("is_correct", True)
                    
                    if is_correct:
                        print(f"   🧑‍⚖️ LLM-Judge: ✅ Doğru (score={eval_score:.2f})")
                    else:
                        print(f"   🧑‍⚖️ LLM-Judge: ❌ Hatalı (score={eval_score:.2f})")
                        if evaluation.get("issues"):
                            print(f"      Sorunlar: {', '.join(evaluation['issues'][:2])}")
                        if evaluation.get("suggested_query"):
                            print(f"      Önerilen: {evaluation['suggested_query'][:100]}...")
                except Exception as eval_error:
                    print(f"   ⚠️ LLM-Judge hatası: {eval_error}")
            
            # 📝 Feedback kaydet (LLM-Judge sonucuna göre)
            try:
                from src.shared.feedback import record_feedback, FeedbackType, FEEDBACK_ENABLED
                
                if FEEDBACK_ENABLED:
                    # LLM-Judge sonucuna göre score belirle
                    if evaluation:
                        is_correct = evaluation.get("is_correct", True)
                        feedback_type = FeedbackType.POSITIVE if is_correct else FeedbackType.NEGATIVE
                        feedback_score = 1 if is_correct else -1
                        eval_info = f" [LLM-Judge: {evaluation.get('score', 0.5):.2f}]"
                    else:
                        feedback_type = FeedbackType.POSITIVE if status == "success" else FeedbackType.NEGATIVE
                        feedback_score = 1 if status == "success" else -1
                        eval_info = ""
                    
                    # Eğer hatalıysa ve önerilen sorgu varsa, düzeltme olarak kaydet
                    corrected_queries = None
                    correction_note = None
                    if evaluation and not evaluation.get("is_correct"):
                        if evaluation.get("suggested_query"):
                            corrected_queries = [evaluation["suggested_query"]]
                        if evaluation.get("explanation"):
                            correction_note = evaluation["explanation"]
                    
                    feedback = record_feedback(
                        session_id=experiment_session_id,
                        question_id=question_id,
                        feedback_type=feedback_type,
                        score=feedback_score,
                        comment=f"Auto-feedback from experiment: {experiment_name}{eval_info}",
                        question=question,
                        response=result["answer"],
                        user_id=f"experiment-{experiment_session_id[:8]}",
                        tool_calls=result.get("tool_calls", []),
                        corrected_queries=corrected_queries,
                        correction_note=correction_note,
                        metadata={
                            "experiment_name": experiment_name,
                            "model": model,
                            "dataset": dataset_name,
                            "item_id": item_id,
                            "tokens": result["tokens"],
                            "evaluation": evaluation,
                        },
                    )
                    if feedback and feedback.id:
                        print(f"   📝 Feedback kaydedildi: {feedback.id[:8]}... (score={feedback_score})")
            except Exception as fb_error:
                print(f"   ⚠️ Feedback kaydetme hatası: {fb_error}")
            
        except Exception as e:
            print(f"   ❌ Exception: {e}")
            results.append({
                "item_id": item_id,
                "question": question,
                "answer": "",
                "tokens": {"input": 0, "output": 0},
                "sources": [],
                "tool_calls": [],
                "status": "error",
                "error": str(e),
            })
    
    # Sonuçları flush et
    langfuse.flush()
    
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    
    # Özet
    print("\n" + "=" * 60)
    print("📊 Experiment Özeti")
    print("=" * 60)
    
    success_count = sum(1 for r in results if r["status"] == "success")
    error_count = sum(1 for r in results if r["status"] == "error")
    total_input_tokens = sum(r["tokens"]["input"] for r in results)
    total_output_tokens = sum(r["tokens"]["output"] for r in results)
    
    print(f"   📛 Experiment: {experiment_name}")
    print(f"   🤖 Model: {model}")
    print(f"   ⏱️ Süre: {duration:.1f} saniye")
    print(f"   ✅ Başarılı: {success_count}")
    print(f"   ❌ Hatalı: {error_count}")
    print(f"   📦 Toplam: {len(results)}")
    print(f"   📊 Tokens: {total_input_tokens} input / {total_output_tokens} output")
    print(f"   📝 Feedback: {len(results)} kayıt (few-shot için kullanılabilir)")
    
    # Sonuçları JSON olarak kaydet (experiment_logs klasörüne)
    logs_dir = os.path.join(backend_dir, "scripts", "experiment_logs")
    os.makedirs(logs_dir, exist_ok=True)
    results_file = os.path.join(logs_dir, f"experiment_{experiment_name}.json")
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump({
            "experiment_name": experiment_name,
            "model": model,
            "dataset": dataset_name,
            "duration_seconds": duration,
            "summary": {
                "success": success_count,
                "error": error_count,
                "total": len(results),
                "total_input_tokens": total_input_tokens,
                "total_output_tokens": total_output_tokens,
            },
            "results": results,
        }, f, ensure_ascii=False, indent=2)
    
    print(f"\n📁 Sonuçlar kaydedildi: {results_file}")
    print(f"\n🔗 Langfuse'da trace'leri görmek için:")
    print(f"   http://localhost:3101 → Traces → session_id: {experiment_session_id[:8]}...")
    
    # Detaylı sonuçlar
    if error_count > 0:
        print(f"\n⚠️ Hatalı sorular:")
        for r in results:
            if r["status"] == "error":
                print(f"   - {r['question'][:50]}... → {r['error']}")


def run_experiment(
    dataset_name: str,
    experiment_name: Optional[str] = None,
    model: str = "gpt-4o",
    dry_run: bool = False,
    limit: Optional[int] = None,
):
    """Sync wrapper for async experiment runner."""
    asyncio.run(run_experiment_async(
        dataset_name=dataset_name,
        experiment_name=experiment_name,
        model=model,
        dry_run=dry_run,
        limit=limit,
    ))


def main():
    parser = argparse.ArgumentParser(
        description="Langfuse Dataset üzerinde GERÇEK react_agent ile experiment çalıştır"
    )
    parser.add_argument(
        "--dataset", "-d",
        required=True,
        help="Dataset adı"
    )
    parser.add_argument(
        "--experiment", "-e",
        help="Experiment adı (opsiyonel, otomatik oluşturulur)"
    )
    parser.add_argument(
        "--model", "-m",
        default="gpt-4o",
        help="Kullanılacak model (default: gpt-4o)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Sadece dataset'i göster, test çalıştırma"
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        help="Test edilecek maksimum soru sayısı"
    )
    
    args = parser.parse_args()
    
    print("\n" + "=" * 60)
    print("🧪 Langfuse Dataset Experiment Runner")
    print("   GERÇEK REACT AGENT İLE TEST")
    print("=" * 60)
    
    run_experiment(
        dataset_name=args.dataset,
        experiment_name=args.experiment,
        model=args.model,
        dry_run=args.dry_run,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
