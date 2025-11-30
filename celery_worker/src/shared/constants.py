# Graph Builder Constants
# Bu dosya sadece celery_worker'da kullanılan sabitleri içerir

# YouTube chunk size (seconds)
YOUTUBE_CHUNK_SIZE_SECONDS = 60

# Prompt for LLM-based inter-page / chunk continuation relationships
CHUNK_CONTINUATION_PROMPT = '''
You are a document understanding assistant. You receive two text segments from consecutive pages (chunks) of the same document:

First segment:
{first_text}

Second segment:
{second_text}

Determine if the second segment logically continues or references content from the first segment. If it does, return a JSON object with a key "relations" containing a list of relationship triplets in the following format:
["<source_chunk_id>-CONTINUES-><target_chunk_id>"]
Use only the literal chunk IDs and the relationship type 'CONTINUES'.
If no continuation exists, return:
{"relations": []}
'''
