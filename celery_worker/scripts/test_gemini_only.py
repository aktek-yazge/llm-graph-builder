#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
GeminiOCRAgent Test Script
"""
import asyncio
import os
import sys

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

# Paths
IMAGES_DIR = '/workspace/celery_worker/output_celery/Aksa-09.03.2016-9028-GENEL KURUL TOPLANTIYA ÇAĞIRI/images'
OUTPUT_DIR = '/workspace/celery_worker/output_celery/gemini_test'


async def main():
    from src.agents.gemini_ocr_agent import GeminiOCRAgent
    
    # Get images
    images = sorted([
        os.path.join(IMAGES_DIR, f) 
        for f in os.listdir(IMAGES_DIR) 
        if f.endswith('.png') and 'page_' in f
    ])

    print(f'Found {len(images)} images')
    for img in images:
        print(f'  - {os.path.basename(img)}')
    
    agent = GeminiOCRAgent()
    await agent.initialize()
    
    result = await agent.process(
        image_list=images,
        output_dir=OUTPUT_DIR,
    )
    
    print(f'\n{"="*60}')
    print(f'RESULT')
    print(f'{"="*60}')
    print(f'Pages: {result["page_count"]}')
    print(f'Total chars: {result["total_chars"]}')
    print(f'Duration: {result["duration_ms"]} ms')
    print(f'Errors: {result["errors"]}')
    print(f'\n--- TOKEN USAGE ---')
    print(f'Input tokens:  {result["token_usage"]["input_tokens"]}')
    print(f'Output tokens: {result["token_usage"]["output_tokens"]}')
    print(f'Total tokens:  {result["token_usage"]["total_tokens"]}')
    print(f'Cost USD:      ${result["token_usage"]["cost_usd"]:.6f}')
    
    print(f'\n--- OCR FILES ---')
    for f in result["ocr_files"]:
        print(f'  - {os.path.basename(f)}')
    
    print(f'\n--- MERGED TEXT (first 1000 chars) ---')
    print(result['merged_text'][:1000])
    
    print(f'\n--- OCR TEXTS (per page) ---')
    for i, txt in enumerate(result['ocr_texts'], 1):
        preview = txt[:200].replace('\n', ' ')
        print(f'Page {i} ({len(txt)} chars): {preview}...')
    
    await agent.close()
    print('\n✓ Test completed!')

if __name__ == '__main__':
    asyncio.run(main())
