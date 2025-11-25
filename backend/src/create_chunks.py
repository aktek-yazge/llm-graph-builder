from langchain_text_splitters import TokenTextSplitter
from langchain.docstore.document import Document
from langchain_neo4j import Neo4jGraph
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain.text_splitter import MarkdownTextSplitter
import logging
# YouTube transcript functions moved to celery_worker
# Backend should not import youtube_transcript_api
try:
    from src.document_sources.youtube import get_chunks_with_timestamps, get_calculated_timestamps
except (ImportError, ModuleNotFoundError):
    # These functions are only available in celery_worker
    # Define stubs to avoid errors
    def get_chunks_with_timestamps(*args, **kwargs):
        raise NotImplementedError("YouTube transcript functions are only available in celery_worker")
    def get_calculated_timestamps(*args, **kwargs):
        raise NotImplementedError("YouTube transcript functions are only available in celery_worker")
from src.utils.log_helpers import log_chunking
import re
import os

logging.basicConfig(format="%(asctime)s - %(message)s", level="INFO")


class CreateChunksofDocument:
    def __init__(self, pages: list[Document], graph: Neo4jGraph):
        self.pages = pages
        self.graph = graph

    def split_file_into_chunks(self,token_chunk_size, chunk_overlap):
        """
        Split a list of documents(file pages) into chunks of fixed size.

        Args:
            pages: A list of pages to split. Each page is a list of text strings.

        Returns:
            A list of chunks each of which is a langchain Document.
        """
        log_chunking("Split file into smaller chunks")
        text_splitter = TokenTextSplitter(chunk_size=token_chunk_size, chunk_overlap=chunk_overlap)
        MAX_TOKEN_CHUNK_SIZE = int(os.getenv('MAX_TOKEN_CHUNK_SIZE', 10000))
        chunk_to_be_created = int(MAX_TOKEN_CHUNK_SIZE / token_chunk_size)
        
        if 'page' in self.pages[0].metadata:
            chunks = []
            for i, document in enumerate(self.pages):
                page_number = i + 1
                if len(chunks) >= chunk_to_be_created:
                    break
                else:
                    for chunk in text_splitter.split_documents([document]):
                        chunks.append(Document(page_content=chunk.page_content, metadata={'page_number':page_number}))    
        
        elif 'length' in self.pages[0].metadata:
            if len(self.pages) == 1  or (len(self.pages) > 1 and self.pages[1].page_content.strip() == ''): 
                match = re.search(r'(?:v=)([0-9A-Za-z_-]{11})\s*',self.pages[0].metadata['source'])
                youtube_id=match.group(1)   
                chunks_without_time_range = text_splitter.split_documents([self.pages[0]])
                chunks = get_calculated_timestamps(chunks_without_time_range[:chunk_to_be_created], youtube_id)
            else: 
                chunks_without_time_range = text_splitter.split_documents(self.pages)
                chunks = get_chunks_with_timestamps(chunks_without_time_range[:chunk_to_be_created])
        else:
            chunks = text_splitter.split_documents(self.pages)
            
        chunks = chunks[:chunk_to_be_created]
        return chunks

    def split_file_into_chunks_recursive(self, chunk_size: int, chunk_overlap: int):
        """
        Split the whole document (all pages) using MarkdownTextSplitter.

        This method concatenates all page texts into a single markdown text,
        then applies the MarkdownTextSplitter to produce chunks that respect
        markdown structure (headers, lists, code blocks, etc.). Each produced 
        chunk will have metadata with a best-effort page range when possible.

        Args:
            chunk_size: target chunk size in characters
            chunk_overlap: overlap size in characters

        Returns:
            List of langchain Document objects
        """
        log_chunking("Split whole document using MarkdownTextSplitter")

        # If pages look like a youtube transcript (time-based), keep existing behaviour
        if 'length' in self.pages[0].metadata:
            log_chunking("Detected time-based pages (youtube), falling back to token-based timestamp splitting")
            # reuse logic from split_file_into_chunks to get timestamped chunks
            token_chunk_size = max(1, int(chunk_size))
            MAX_TOKEN_CHUNK_SIZE = int(os.getenv('MAX_TOKEN_CHUNK_SIZE', 10000))
            chunk_to_be_created = int(MAX_TOKEN_CHUNK_SIZE / token_chunk_size)
            if len(self.pages) == 1 or (len(self.pages) > 1 and self.pages[1].page_content.strip() == ''):
                match = re.search(r'(?:v=)([0-9A-Za-z_-]{11})\s*', self.pages[0].metadata.get('source', ''))
                youtube_id = match.group(1) if match else None
                chunks_without_time_range = TokenTextSplitter(chunk_size=token_chunk_size, chunk_overlap=chunk_overlap).split_documents([self.pages[0]])
                chunks = get_calculated_timestamps(chunks_without_time_range[:chunk_to_be_created], youtube_id) if youtube_id else chunks_without_time_range[:chunk_to_be_created]
            else:
                chunks_without_time_range = TokenTextSplitter(chunk_size=token_chunk_size, chunk_overlap=chunk_overlap).split_documents(self.pages)
                chunks = get_chunks_with_timestamps(chunks_without_time_range[:chunk_to_be_created])

            # Ensure each chunk has page_number if available
            docs = []
            for c in chunks:
                meta = {}
                if 'start_timestamp' in c.metadata and 'end_timestamp' in c.metadata:
                    meta.update({k: c.metadata[k] for k in ('start_timestamp', 'end_timestamp') if k in c.metadata})
                if 'page_number' in c.metadata:
                    meta['page_number'] = c.metadata['page_number']
                docs.append(Document(page_content=c.page_content, metadata=meta))
            return docs

        # Debug: Log page information
        log_chunking(f"DEBUG: Total pages to process: {len(self.pages)}")
        
        for i, page in enumerate(self.pages):
            page_content = page.page_content
            log_chunking(f"DEBUG: Page {i+1} length: {len(page_content)} chars")
            if len(page_content) > 100:
                log_chunking(f"DEBUG: Page {i+1} preview: {page_content[:100]}...")

        # Check if we have single document with [PAGE BREAK] markers
        if (len(self.pages) == 1 and 
            hasattr(self.pages[0], 'page_content') and 
            "[PAGE BREAK]" in self.pages[0].page_content):
            
            log_chunking("🔍 Single document with [PAGE BREAK] markers detected - using direct approach")
            
            # Use original content directly without splitting/rejoining
            full_text = self.pages[0].page_content
            log_chunking(f"📄 Using original content with [PAGE BREAK] markers preserved")
            log_chunking(f"DEBUG: Original content length: {len(full_text)} chars")
            log_chunking(f"DEBUG: PAGE BREAK markers found: {full_text.count('[PAGE BREAK]')}")
            
        else:
            # Handle multiple pages case (fallback to existing logic)
            log_chunking("📄 Multiple pages detected - concatenating with separators")
            
            # Concatenate pages with page separators
            page_texts = []
            separator = "\n\n---\n\n"
            
            for i, p in enumerate(self.pages):
                page_text = p.page_content if hasattr(p, 'page_content') else str(p)
                page_text = str(page_text).strip()
                page_texts.append(page_text)
                
                log_chunking(f"DEBUG: Page {i+1} length: {len(page_text)} chars")
                if i < 3:  # Log first 3 pages content preview
                    log_chunking(f"DEBUG: Page {i+1} preview: {page_text[:100]}...")

            # Join pages with markdown page separators
            full_text = separator.join(page_texts)
            log_chunking(f"DEBUG: Full text length after concatenation: {len(full_text)} chars")

        log_chunking(f"DEBUG: Final full_text preview: {full_text[:200]}...")

        # No max chunks limit for markdown splitting - process entire document
        log_chunking(f"DEBUG: No chunk limit applied, chunk_size: {chunk_size}")
        log_chunking(f"DEBUG: No chunk limit applied, chunk_overlap: {chunk_overlap}")

        # Use RecursiveCharacterTextSplitter with custom separators including PAGE BREAK
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        
        # Custom separators including PAGE BREAK (highest priority)
        custom_separators = [
            "[PAGE BREAK]",      # Page breaks (highest priority)
            "\n#{1,6} ",         # Markdown headers (# ## ### etc.)
            "```\n",             # Code block ends
            "\n\\*\\*\\*+\n",     # Horizontal lines (***)
            "\n---+\n",          # Horizontal lines (---)
            "\n___+\n",          # Horizontal lines (___)
            "\n\n",              # Paragraph breaks
            "\n",                # Line breaks
            " ",                 # Word breaks
            "",                  # Character breaks (fallback)
        ]
        
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=custom_separators,
            is_separator_regex=True  # Enable regex for markdown patterns
        )
        
        log_chunking(f"DEBUG: RecursiveCharacterTextSplitter created with PAGE BREAK support")
        log_chunking(f"DEBUG: full_text type: {type(full_text)}, length: {len(full_text) if full_text else 'None'}")
        
        if not full_text or len(full_text.strip()) == 0:
            log_chunking("ERROR: full_text is empty or None!")
            return []

        # Create documents from the markdown text
        documents = splitter.create_documents([full_text])
        log_chunking(f"DEBUG: Markdown chunks created: {len(documents)}")

        # Add page metadata to each chunk using overlap-aware PAGE BREAK counting
        current_page = 1  # Start from page 1
        prev_chunk_end = ""  # Track end of previous chunk to detect overlap
        
        for ch_idx, doc in enumerate(documents):
            chunk_text = doc.page_content.strip()
            
            # Detect overlap with previous chunk
            overlap_length = 0
            if ch_idx > 0 and prev_chunk_end:
                # Find how much of current chunk overlaps with previous chunk end
                # Check different overlap lengths to find the longest match
                max_check_length = min(len(chunk_text), len(prev_chunk_end), 500)  # Limit check to reasonable size
                
                for test_length in range(max_check_length, 0, -1):
                    if chunk_text.startswith(prev_chunk_end[-test_length:]):
                        overlap_length = test_length
                        break
            
            # Count PAGE BREAK markers in this chunk, excluding overlap
            if overlap_length > 0:
                overlap_part = chunk_text[:overlap_length]
                non_overlap_part = chunk_text[overlap_length:]
                page_breaks_in_overlap = overlap_part.count("[PAGE BREAK]")
                page_breaks_in_new_content = non_overlap_part.count("[PAGE BREAK]")
                
                log_chunking(f"DEBUG: Chunk {ch_idx+1} - overlap detected: {overlap_length} chars, PAGE BREAKs in overlap: {page_breaks_in_overlap}, in new content: {page_breaks_in_new_content}")
                
                # Only count PAGE BREAK markers that are NOT in the overlap
                page_breaks_to_add = page_breaks_in_new_content
            else:
                page_breaks_to_add = chunk_text.count("[PAGE BREAK]")
                log_chunking(f"DEBUG: Chunk {ch_idx+1} - no overlap detected, PAGE BREAKs in chunk: {page_breaks_to_add}")
            
            # This chunk starts on current_page
            page_number = current_page
            
            # Update current_page for next chunk based on NEW PAGE BREAK markers found
            if page_breaks_to_add > 0:
                current_page += page_breaks_to_add
                log_chunking(f"DEBUG: Chunk {ch_idx+1} - found {page_breaks_to_add} new PAGE BREAK(s), next chunks will start from page {current_page}")
            
            # Store end of current chunk for next iteration's overlap detection
            prev_chunk_end = chunk_text[-200:] if len(chunk_text) > 200 else chunk_text  # Keep last 200 chars
            
            meta = {'page_number': page_number}
            doc.metadata.update(meta)
            log_chunking(f"DEBUG: Chunk {ch_idx+1} - assigned page {page_number}, new PAGE BREAKs: {page_breaks_to_add}, content length: {len(chunk_text)}")

        log_chunking(f"DEBUG: Total final documents created with metadata: {len(documents)}")
        return documents