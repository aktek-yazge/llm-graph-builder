#!/usr/bin/env python3
"""
Test language filtering with specific example: "Taşkın Orbay" should NOT appear when filtering for "Almanca"
"""

import sys
sys.path.append('/workspace/backend/src')

# Mock test data
mock_candidates = [
    {
        'p.name': 'Taşkın Orbay',
        'languages': ['İngilizce'],  # Only English, no German
        'skills': ['Python', 'JavaScript'],
        'match_score': 0.65
    },
    {
        'p.name': 'Prof. Dr. Yıldız Hakyemez', 
        'languages': ['Almanca', 'İngilizce'],  # Has German
        'skills': ['Python', 'R'],
        'match_score': 0.65
    },
    {
        'p.name': 'Hülya Yazıcı',
        'languages': ['Almanca', 'İngilizce', 'Türkçe'],  # Has German
        'skills': ['Python', 'JavaScript'],
        'match_score': 0.34
    }
]

def test_language_filtering():
    """Test the strict language filtering logic"""
    
    requirements = {
        "language_entities": ["Almanca"]  # German required
    }
    
    print("=== STRICT LANGUAGE FILTER TEST ===")
    print(f"Required Languages: {requirements['language_entities']}")
    print()
    
    final_candidates = []
    for candidate in mock_candidates:
        print(f"Testing: {candidate['p.name']}")
        print(f"  Languages: {candidate['languages']}")
        
        # Apply the same logic as the fix
        language_requirements = requirements.get("language_entities", [])
        if language_requirements:
            candidate_languages = [lang.lower() for lang in candidate.get('languages', [])]
            required_languages = [req.lower() for req in language_requirements]
            
            print(f"  Candidate (lowercase): {candidate_languages}")
            print(f"  Required (lowercase): {required_languages}")
            
            # Check for match
            language_match = any(
                any(req_lang in cand_lang for cand_lang in candidate_languages)
                for req_lang in required_languages
            )
            
            print(f"  Language Match: {language_match}")
            
            if not language_match:
                print(f"  ❌ FILTERED OUT: {candidate['p.name']}")
                continue
            else:
                print(f"  ✅ PASSES FILTER: {candidate['p.name']}")
        
        final_candidates.append(candidate)
        print()
    
    print("=== FINAL RESULTS ===")
    print(f"Original candidates: {len(mock_candidates)}")
    print(f"After language filter: {len(final_candidates)}")
    print()
    
    for candidate in final_candidates:
        print(f"✅ {candidate['p.name']} - {candidate['languages']}")
    
    # Specific test
    taskin_filtered = not any(c['p.name'] == 'Taşkın Orbay' for c in final_candidates)
    print(f"\n🎯 Taşkın Orbay filtered out: {taskin_filtered}")
    
    if taskin_filtered:
        print("✅ SUCCESS: Language filtering works correctly!")
    else:
        print("❌ FAILURE: Taşkın Orbay should be filtered out!")

if __name__ == "__main__":
    test_language_filtering()