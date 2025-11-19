
def test_chunk_logic(raw_chunks):
    merged_chunks = []
    current_chunk_text = ""
    
    print(f"Testing with raw chunks: {[len(c) for c in raw_chunks]}")
    
    for chunk_text in raw_chunks:
        chunk_text = chunk_text.strip()
        if not chunk_text:
            continue
            
        if not current_chunk_text:
            current_chunk_text = chunk_text
        else:
            # Check if adding this chunk keeps it under limit or if current is too small
            if len(current_chunk_text) < 150:
                # Current is small.
                # Check if the INCOMING chunk is big (>150) AND we have a previous chunk
                if len(chunk_text) > 150 and merged_chunks:
                    # User Rule: Current is small, Next is Big -> Merge Current to Previous
                    print(f"  -> Merging backward: '{current_chunk_text[:20]}...' to previous")
                    merged_chunks[-1] += "\n" + current_chunk_text
                    # Set incoming (big) as new current
                    current_chunk_text = chunk_text
                else:
                    # Standard: Merge incoming into current
                    print(f"  -> Merging forward: '{chunk_text[:20]}...' into current")
                    current_chunk_text += "\n" + chunk_text
            else:
                # Current chunk is big enough, save it and start new
                print(f"  -> Committing current: '{current_chunk_text[:20]}...'")
                merged_chunks.append(current_chunk_text)
                current_chunk_text = chunk_text
    
    # Add the last chunk
    if current_chunk_text:
        merged_chunks.append(current_chunk_text)
        
    print(f"Result chunks lengths: {[len(c) for c in merged_chunks]}")
    return merged_chunks

# Test Data
# A: 200 chars
# B: 50 chars
# C: 200 chars
# D: 50 chars
# E: 50 chars

A = "A" * 200
B = "B" * 50
C = "C" * 200
D = "D" * 50
E = "E" * 50

print("\n--- Test Case 1: [Big, Small, Big] ---")
# Expected: [A+B, C] -> [251, 200]
test_chunk_logic([A, B, C])

print("\n--- Test Case 2: [Small, Big] (No previous) ---")
# Expected: [B+C] -> [251] (Merge forward fallback)
test_chunk_logic([B, C])

print("\n--- Test Case 3: [Big, Small, Small, Big] ---")
# Expected: [A+B+D, C] -> Wait.
# Trace: A(200). Commit A. Current=B(50).
# Next=D(50). Small. Merge forward -> Current=B+D(101).
# Next=C(200). Big. Current(101) < 150. Merge backward -> A+B+D. Current=C.
# Result: [A+B+D, C] -> [302, 200]
test_chunk_logic([A, B, D, C])

print("\n--- Test Case 4: [Big, Small, Small] ---")
# Expected: [A, B+D] -> [200, 101]
test_chunk_logic([A, B, D])
