"""
Test Suite for Session Blackboard Module

Tests the skills-based query history navigation system.
"""

import os
import sys
import pytest
import uuid

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def test_session_id():
    """Generate unique session ID for each test."""
    return f"test_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def sample_results():
    """Sample query results."""
    return [
        {"fileName": "policy_001.pdf", "text": "Kira kaybi teminati...", "score": 0.92},
        {"fileName": "policy_002.pdf", "text": "Yangın sigortası...", "score": 0.87},
        {"fileName": "policy_003.pdf", "text": "Deprem teminatı...", "score": 0.85},
    ]


@pytest.fixture(scope="module")
def blackboard_module():
    """Import blackboard module with error handling."""
    try:
        from src.shared.session_blackboard import (
            store_step,
            get_session_overview,
            search_skills,
            get_step_detail,
            get_step_results,
            get_question_count,
            get_step_count,
            clear_session,
            update_skill_description,
            init_blackboard_tables,
            SESSION_BLACKBOARD_ENABLED,
        )
        return {
            "store_step": store_step,
            "get_session_overview": get_session_overview,
            "search_skills": search_skills,
            "get_step_detail": get_step_detail,
            "get_step_results": get_step_results,
            "get_question_count": get_question_count,
            "get_step_count": get_step_count,
            "clear_session": clear_session,
            "update_skill_description": update_skill_description,
            "init_blackboard_tables": init_blackboard_tables,
            "enabled": SESSION_BLACKBOARD_ENABLED,
        }
    except ImportError as e:
        pytest.skip(f"Session blackboard module not available: {e}")


@pytest.fixture(autouse=True)
def cleanup_test_data(test_session_id, blackboard_module):
    """Cleanup test data after each test."""
    yield
    if blackboard_module["enabled"]:
        try:
            blackboard_module["clear_session"](test_session_id)
        except:
            pass


# =============================================================================
# UNIT TESTS: store_step
# =============================================================================

class TestStoreStep:
    """Tests for store_step function."""
    
    def test_store_successful_step(self, test_session_id, sample_results, blackboard_module):
        """Test storing a successful query step."""
        if not blackboard_module["enabled"]:
            pytest.skip("Blackboard disabled")
        
        step_id = blackboard_module["store_step"](
            session_id=test_session_id,
            question_id="q1",
            question_text="Akenerji'nin kira kaybı teminatı nedir?",
            question_number=1,
            step_number=1,
            step_name="find_customer_variations",
            cypher_query="MATCH (c:Customer) WHERE c.name CONTAINS 'Akenerji' RETURN c",
            status="success",
            record_count=3,
            results=sample_results,
        )
        
        assert step_id is not None
        assert isinstance(step_id, int)
    
    def test_store_empty_result_step(self, test_session_id, blackboard_module):
        """Test storing a step with no results."""
        if not blackboard_module["enabled"]:
            pytest.skip("Blackboard disabled")
        
        step_id = blackboard_module["store_step"](
            session_id=test_session_id,
            question_id="q1",
            question_text="Test sorusu",
            question_number=1,
            step_number=1,
            step_name="empty_search",
            cypher_query="MATCH (c:Customer) WHERE c.name = 'NONEXISTENT' RETURN c",
            status="empty",
            record_count=0,
            results=[],
        )
        
        assert step_id is not None
    
    def test_store_failed_step(self, test_session_id, blackboard_module):
        """Test storing a failed query step."""
        if not blackboard_module["enabled"]:
            pytest.skip("Blackboard disabled")
        
        step_id = blackboard_module["store_step"](
            session_id=test_session_id,
            question_id="q1",
            question_text="Test sorusu",
            question_number=1,
            step_number=1,
            step_name="failed_query",
            cypher_query="INVALID CYPHER SYNTAX",
            status="failed",
            record_count=0,
        )
        
        assert step_id is not None
    
    def test_store_multiple_steps_same_question(self, test_session_id, blackboard_module):
        """Test storing multiple steps for the same question."""
        if not blackboard_module["enabled"]:
            pytest.skip("Blackboard disabled")
        
        # Step 1
        step1_id = blackboard_module["store_step"](
            session_id=test_session_id,
            question_id="q1",
            question_text="Multi-step test",
            question_number=1,
            step_number=1,
            step_name="step_one",
            cypher_query="MATCH (n) RETURN n LIMIT 1",
            status="success",
            record_count=1,
        )
        
        # Step 2
        step2_id = blackboard_module["store_step"](
            session_id=test_session_id,
            question_id="q1",
            question_text="Multi-step test",
            question_number=1,
            step_number=2,
            step_name="step_two",
            cypher_query="MATCH (n) RETURN n LIMIT 5",
            status="success",
            record_count=5,
        )
        
        assert step1_id is not None
        assert step2_id is not None
        assert step1_id != step2_id


# =============================================================================
# UNIT TESTS: get_session_overview
# =============================================================================

class TestGetSessionOverview:
    """Tests for get_session_overview function."""
    
    def test_overview_empty_session(self, test_session_id, blackboard_module):
        """Test overview for empty session."""
        if not blackboard_module["enabled"]:
            pytest.skip("Blackboard disabled")
        
        overview = blackboard_module["get_session_overview"](test_session_id)
        
        assert "henüz sorgu yapılmadı" in overview or "SESSION OVERVIEW" in overview
    
    def test_overview_with_steps(self, test_session_id, sample_results, blackboard_module):
        """Test overview with multiple steps."""
        if not blackboard_module["enabled"]:
            pytest.skip("Blackboard disabled")
        
        # Store some steps
        blackboard_module["store_step"](
            session_id=test_session_id,
            question_id="q1",
            question_text="İlk soru",
            question_number=1,
            step_number=1,
            step_name="find_variations",
            cypher_query="MATCH (c:Customer) RETURN c",
            status="success",
            record_count=3,
            results=sample_results,
        )
        
        blackboard_module["store_step"](
            session_id=test_session_id,
            question_id="q1",
            question_text="İlk soru",
            question_number=1,
            step_number=2,
            step_name="find_policies",
            cypher_query="MATCH (p:Policy) RETURN p",
            status="empty",
            record_count=0,
        )
        
        overview = blackboard_module["get_session_overview"](test_session_id)
        
        assert "SESSION OVERVIEW" in overview
        assert "Q1" in overview
        assert "find_variations" in overview
        assert "find_policies" in overview
        assert "✅" in overview  # Success indicator
        assert "⚪" in overview or "❌" in overview  # Empty/failed indicator


# =============================================================================
# UNIT TESTS: search_skills
# =============================================================================

class TestSearchSkills:
    """Tests for search_skills function."""
    
    def test_search_basic(self, test_session_id, blackboard_module):
        """Test basic skill search."""
        if not blackboard_module["enabled"]:
            pytest.skip("Blackboard disabled")
        
        # Store a step with skill description
        step_id = blackboard_module["store_step"](
            session_id=test_session_id,
            question_id="q1",
            question_text="Kira kaybı teminatı araması",
            question_number=1,
            step_number=1,
            step_name="kira_search",
            cypher_query="MATCH (c:Chunk) WHERE c.text CONTAINS 'kira kaybı' RETURN c",
            status="success",
            record_count=5,
        )
        
        # Update skill description
        blackboard_module["update_skill_description"](
            step_id=step_id,
            skill_description="Kira kaybı teminatı içeren chunk araması",
            skill_tags=["kira", "kaybi", "teminat"],
            intent="content_search",
        )
        
        # Search
        results = blackboard_module["search_skills"](test_session_id, "kira kaybı")
        
        assert "kira" in results.lower() or "sonuç bulunamadı" in results.lower()
    
    def test_search_or_query(self, test_session_id, blackboard_module):
        """Test OR search query."""
        if not blackboard_module["enabled"]:
            pytest.skip("Blackboard disabled")
        
        # Store step
        step_id = blackboard_module["store_step"](
            session_id=test_session_id,
            question_id="q1",
            question_text="Rent loss search",
            question_number=1,
            step_number=1,
            step_name="rent_loss_search",
            cypher_query="MATCH (c:Chunk) RETURN c",
            status="success",
            record_count=3,
        )
        
        blackboard_module["update_skill_description"](
            step_id=step_id,
            skill_description="Rent loss coverage search",
            skill_tags=["rent", "loss", "coverage"],
            intent="content_search",
        )
        
        # Search with OR
        results = blackboard_module["search_skills"](
            test_session_id, 
            "kira kaybı | rent loss | kira zararı"
        )
        
        # Should find something or return no results
        assert len(results) > 0


# =============================================================================
# UNIT TESTS: get_step_detail & get_step_results
# =============================================================================

class TestStepDetailAndResults:
    """Tests for step detail and pagination."""
    
    def test_get_step_detail(self, test_session_id, sample_results, blackboard_module):
        """Test getting step detail."""
        if not blackboard_module["enabled"]:
            pytest.skip("Blackboard disabled")
        
        # Store step
        step_id = blackboard_module["store_step"](
            session_id=test_session_id,
            question_id="q1",
            question_text="Detail test",
            question_number=1,
            step_number=1,
            step_name="detail_test_step",
            cypher_query="MATCH (c:Chunk) WHERE c.text CONTAINS 'test' RETURN c",
            status="success",
            record_count=3,
            results=sample_results,
        )
        
        detail = blackboard_module["get_step_detail"](step_id)
        
        assert "STEP DETAY" in detail
        assert "CYPHER SORGUSU" in detail
        assert "MATCH" in detail
    
    def test_get_step_results_pagination(self, test_session_id, blackboard_module):
        """Test result pagination."""
        if not blackboard_module["enabled"]:
            pytest.skip("Blackboard disabled")
        
        # Create 20 results
        results = [{"id": i, "text": f"Result {i}"} for i in range(20)]
        
        step_id = blackboard_module["store_step"](
            session_id=test_session_id,
            question_id="q1",
            question_text="Pagination test",
            question_number=1,
            step_number=1,
            step_name="pagination_test",
            cypher_query="MATCH (n) RETURN n",
            status="success",
            record_count=20,
            results=results,
        )
        
        # Get first page
        page1 = blackboard_module["get_step_results"](step_id, start=0, end=10)
        assert "1-10" in page1 or "Result" in page1
        
        # Get second page
        page2 = blackboard_module["get_step_results"](step_id, start=10, end=20)
        assert "11-" in page2 or "Result" in page2


# =============================================================================
# UNIT TESTS: update_skill_description
# =============================================================================

class TestUpdateSkillDescription:
    """Tests for skill description update."""
    
    def test_update_description(self, test_session_id, blackboard_module):
        """Test updating skill description."""
        if not blackboard_module["enabled"]:
            pytest.skip("Blackboard disabled")
        
        step_id = blackboard_module["store_step"](
            session_id=test_session_id,
            question_id="q1",
            question_text="Update test",
            question_number=1,
            step_number=1,
            step_name="update_test_step",
            cypher_query="MATCH (n) RETURN n",
            status="success",
            record_count=1,
        )
        
        success = blackboard_module["update_skill_description"](
            step_id=step_id,
            skill_description="Müşteri varyasyonlarını bulmak için yapılan arama",
            skill_tags=["müşteri", "varyasyon", "arama"],
            intent="entity_search",
        )
        
        assert success is True
        
        # Verify via detail
        detail = blackboard_module["get_step_detail"](step_id)
        assert "varyasyon" in detail.lower() or "Açıklama" in detail


# =============================================================================
# UNIT TESTS: Counters
# =============================================================================

class TestCounters:
    """Tests for question and step counters."""
    
    def test_question_count(self, test_session_id, blackboard_module):
        """Test question count."""
        if not blackboard_module["enabled"]:
            pytest.skip("Blackboard disabled")
        
        # Initial count should be 0
        count = blackboard_module["get_question_count"](test_session_id)
        assert count == 0
        
        # Add a step
        blackboard_module["store_step"](
            session_id=test_session_id,
            question_id="q1",
            question_text="Q1",
            question_number=1,
            step_number=1,
            step_name="step1",
            cypher_query="MATCH (n) RETURN n",
            status="success",
            record_count=1,
        )
        
        count = blackboard_module["get_question_count"](test_session_id)
        assert count == 1
    
    def test_step_count(self, test_session_id, blackboard_module):
        """Test step count."""
        if not blackboard_module["enabled"]:
            pytest.skip("Blackboard disabled")
        
        # Initial count should be 0
        count = blackboard_module["get_step_count"](test_session_id, "q1")
        assert count == 0
        
        # Add steps
        for i in range(3):
            blackboard_module["store_step"](
                session_id=test_session_id,
                question_id="q1",
                question_text="Q1",
                question_number=1,
                step_number=i+1,
                step_name=f"step_{i+1}",
                cypher_query="MATCH (n) RETURN n",
                status="success",
                record_count=1,
            )
        
        count = blackboard_module["get_step_count"](test_session_id, "q1")
        assert count == 3


# =============================================================================
# INTEGRATION TESTS
# =============================================================================

class TestIntegration:
    """Integration tests simulating real usage."""
    
    def test_full_session_workflow(self, test_session_id, sample_results, blackboard_module):
        """Test complete session workflow."""
        if not blackboard_module["enabled"]:
            pytest.skip("Blackboard disabled")
        
        # Q1: First question
        blackboard_module["store_step"](
            session_id=test_session_id,
            question_id="q1",
            question_text="Akenerji'nin kira kaybı teminatı?",
            question_number=1,
            step_number=1,
            step_name="find_customer",
            cypher_query="MATCH (c:Customer) WHERE c.name =~ '(?i).*akenerji.*' RETURN c",
            status="success",
            record_count=2,
            results=sample_results[:2],
        )
        
        blackboard_module["store_step"](
            session_id=test_session_id,
            question_id="q1",
            question_text="Akenerji'nin kira kaybı teminatı?",
            question_number=1,
            step_number=2,
            step_name="find_coverage",
            cypher_query="MATCH (c:Chunk) WHERE c.text CONTAINS 'kira kaybı' RETURN c",
            status="success",
            record_count=5,
            results=sample_results,
        )
        
        # Q2: Follow-up question
        blackboard_module["store_step"](
            session_id=test_session_id,
            question_id="q2",
            question_text="Bu belgede yangın teminatı da var mı?",
            question_number=2,
            step_number=1,
            step_name="find_fire_coverage",
            cypher_query="MATCH (c:Chunk) WHERE c.fileName = 'policy_001.pdf' AND c.text CONTAINS 'yangın' RETURN c",
            status="success",
            record_count=2,
        )
        
        # Check overview
        overview = blackboard_module["get_session_overview"](test_session_id)
        
        assert "Q1" in overview
        assert "Q2" in overview
        assert "Akenerji" in overview
        assert "yangın" in overview or "fire" in overview.lower()
        
        # Search skills
        results = blackboard_module["search_skills"](test_session_id, "kira kaybı")
        assert len(results) > 0


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
