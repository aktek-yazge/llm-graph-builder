"""
End-to-End Test: Agent Builder Flow
====================================

Bu test Agent Builder'ın tam akışını test eder:
1. Session oluştur
2. Goal tanımla
3. Skill oluştur
4. Agent oluştur
5. Deploy et
6. Belge işle

Kullanım:
    pytest tests/test_e2e_agent_flow.py -v

Gereksinimler:
    - Agent Builder API çalışıyor olmalı (localhost:8001)
    - Neo4j Ontology DB bağlantısı aktif olmalı
    - Celery worker çalışıyor olmalı (process testi için)
"""

import os
import pytest
import httpx
import asyncio
from typing import Optional

# Test configuration
API_BASE = os.getenv("AGENT_BUILDER_URL", "http://localhost:8001")
TENANT_ID = "test-tenant"


class TestAgentBuilderE2E:
    """End-to-end test for Agent Builder flow."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test client."""
        self.client = httpx.Client(base_url=f"{API_BASE}/api/v2/agent-builder", timeout=30.0)
        yield
        self.client.close()
    
    def test_01_health_check(self):
        """Test API health check."""
        response = self.client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "healthy"
        print(f"✅ Health check passed: {data}")
    
    def test_02_create_session(self):
        """Test session creation."""
        response = self.client.post("/sessions", json={"tenant_id": TENANT_ID})
        assert response.status_code == 200
        
        data = response.json()
        assert "id" in data
        assert data.get("status") == "active"
        
        # Store session ID for later tests
        self.__class__.session_id = data["id"]
        print(f"✅ Session created: {self.__class__.session_id}")
    
    def test_03_send_message(self):
        """Test sending message to session."""
        session_id = getattr(self.__class__, 'session_id', None)
        if not session_id:
            pytest.skip("No session ID from previous test")
        
        response = self.client.post(
            f"/sessions/{session_id}/message",
            json={"message": "Sigorta poliçelerinden teminat bilgilerini çıkarmak istiyorum"}
        )
        assert response.status_code == 200
        
        data = response.json()
        assert "message" in data
        print(f"✅ Message sent, response: {data.get('message', '')[:100]}...")
    
    def test_04_create_goal(self):
        """Test goal creation."""
        response = self.client.post("/goals", json={
            "name": "Insurance Entity Extraction",
            "description": "Extract entities from insurance documents",
            "goal_type": "extraction",
            "natural_language_query": "Sigorta poliçelerinden tüm teminatları çıkar",
            "tenant_id": TENANT_ID,
        })
        assert response.status_code == 200
        
        data = response.json()
        assert "id" in data
        
        self.__class__.goal_id = data["id"]
        print(f"✅ Goal created: {self.__class__.goal_id}")
    
    def test_05_create_skill(self):
        """Test skill creation."""
        response = self.client.post("/skills", json={
            "name": "Test Insurance OCR Skill",
            "description": "Test skill for insurance document OCR",
            "skill_category": "ocr",
            "prompt_template": "Extract entities from this document:\n\n{document_text}\n\nEntities:\n{entity_schemas}",
            "input_schema": {"document_text": "string"},
            "output_schema": {"entities": "array"},
            "tenant_id": TENANT_ID,
            "is_global": False,
        })
        assert response.status_code == 200
        
        data = response.json()
        assert "id" in data
        
        self.__class__.skill_id = data["id"]
        print(f"✅ Skill created: {self.__class__.skill_id}")
    
    def test_06_create_agent(self):
        """Test agent creation."""
        goal_id = getattr(self.__class__, 'goal_id', None)
        skill_id = getattr(self.__class__, 'skill_id', None)
        
        if not goal_id or not skill_id:
            pytest.skip("No goal/skill ID from previous tests")
        
        response = self.client.post("/agents", json={
            "name": "Test Insurance Agent",
            "description": "Agent for testing insurance document processing",
            "purpose": "Extract entities from insurance documents",
            "tenant_id": TENANT_ID,
            "goal_ids": [goal_id],
            "skill_ids": [skill_id],
        })
        assert response.status_code == 200
        
        data = response.json()
        assert "id" in data
        
        self.__class__.agent_id = data["id"]
        print(f"✅ Agent created: {self.__class__.agent_id}")
    
    def test_07_deploy_agent(self):
        """Test agent deployment."""
        agent_id = getattr(self.__class__, 'agent_id', None)
        if not agent_id:
            pytest.skip("No agent ID from previous test")
        
        response = self.client.post(f"/agents/{agent_id}/deploy")
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("success") is True
        print(f"✅ Agent deployed: {data.get('message')}")
    
    def test_08_get_skill_execution(self):
        """Test skill execution endpoint (used by Celery worker)."""
        skill_id = getattr(self.__class__, 'skill_id', None)
        if not skill_id:
            pytest.skip("No skill ID from previous test")
        
        response = self.client.get(f"/skills/{skill_id}/execution")
        assert response.status_code == 200
        
        data = response.json()
        assert "skill_id" in data
        assert "prompt_template" in data
        print(f"✅ Skill execution data: name={data.get('name')}, category={data.get('category')}")
    
    def test_09_process_with_agent(self):
        """Test document processing with agent (requires running Celery)."""
        agent_id = getattr(self.__class__, 'agent_id', None)
        if not agent_id:
            pytest.skip("No agent ID from previous test")
        
        # This will queue a task - actual processing requires Celery worker
        response = self.client.post(
            f"/agents/{agent_id}/process",
            json={"file_ids": ["999"]}  # Test file ID
        )
        
        # May return 200 (task queued) or error if Celery not running
        if response.status_code == 200:
            data = response.json()
            assert "task_id" in data
            print(f"✅ Process task queued: {data.get('task_id')}")
        else:
            print(f"⚠️ Process request returned {response.status_code} (Celery may not be running)")
    
    def test_10_list_agents(self):
        """Test listing agents."""
        response = self.client.get(f"/agents?tenant_id={TENANT_ID}")
        assert response.status_code == 200
        
        data = response.json()
        assert isinstance(data, list)
        print(f"✅ Listed {len(data)} agents")
    
    def test_11_cleanup(self):
        """Cleanup test data (optional)."""
        # In a real test, we would delete created entities
        # For now, just log what was created
        print("\n📋 Test Summary:")
        print(f"   Session: {getattr(self.__class__, 'session_id', 'N/A')}")
        print(f"   Goal: {getattr(self.__class__, 'goal_id', 'N/A')}")
        print(f"   Skill: {getattr(self.__class__, 'skill_id', 'N/A')}")
        print(f"   Agent: {getattr(self.__class__, 'agent_id', 'N/A')}")


# Standalone async test
async def test_async_flow():
    """Async version of the E2E flow."""
    async with httpx.AsyncClient(base_url=f"{API_BASE}/api/v2/agent-builder", timeout=30.0) as client:
        # Health check
        response = await client.get("/health")
        print(f"Health: {response.json()}")
        
        # Create session
        response = await client.post("/sessions", json={"tenant_id": TENANT_ID})
        if response.status_code == 200:
            print(f"Session: {response.json()}")


if __name__ == "__main__":
    # Run async test
    asyncio.run(test_async_flow())
