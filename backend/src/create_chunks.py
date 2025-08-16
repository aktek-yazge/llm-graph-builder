from langchain_text_splitters import TokenTextSplitter
from langchain.docstore.document import Document
from langchain_neo4j import Neo4jGraph
from langchain_text_splitters import RecursiveCharacterTextSplitter
import logging
from src.document_sources.youtube import get_chunks_with_timestamps, get_calculated_timestamps
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
        logging.info("Split file into smaller chunks")
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
        Split the whole document (all pages) using RecursiveCharacterTextSplitter.

        This method concatenates all page texts into a single text with page
        separators, then applies the RecursiveCharacterTextSplitter to produce
        character-based chunks. Each produced chunk will have metadata with a
        best-effort page range (start_page, end_page) when possible.

        Args:
            chunk_size: target chunk size in characters
            chunk_overlap: overlap size in characters

        Returns:
            List of langchain Document objects
        """
        logging.info("Split whole document using RecursiveCharacterTextSplitter")

        # If pages look like a youtube transcript (time-based), keep existing behaviour
        if 'length' in self.pages[0].metadata:
            logging.info("Detected time-based pages (youtube), falling back to token-based timestamp splitting")
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
        logging.info(f"DEBUG: Total pages to process: {len(self.pages)}")
        for i, p in enumerate(self.pages):
            page_content = p.page_content if hasattr(p, 'page_content') else str(p)
            logging.info(f"DEBUG: Page {i+1} length: {len(page_content)} chars")
            if i < 3:  # Log first 3 pages content preview
                logging.info(f"DEBUG: Page {i+1} preview: {page_content[:100]}...")

        # Concatenate pages with page separators and record start/end offsets for each page
        page_texts = []
        page_starts = []
        page_ends = []
        current_offset = 0
        
        for i, p in enumerate(self.pages):
            page_text = p.page_content if hasattr(p, 'page_content') else str(p)
            # Clean and normalize text
            page_text = str(page_text).strip()
            
            page_texts.append(page_text)
            page_starts.append(current_offset)
            current_offset += len(page_text)
            page_ends.append(current_offset)
            
            logging.info(f"DEBUG: Page {i+1} - start: {page_starts[i]}, end: {page_ends[i]}, length: {len(page_text)}")
            if i < 3:  # Log first 3 pages content preview
                logging.info(f"DEBUG: Page {i+1} preview: {page_text[:100]}...")

        full_text = "".join(page_texts)
        logging.info(f"DEBUG: Full text length after concatenation: {len(full_text)} chars")
        logging.info(f"DEBUG: Full text preview: {full_text[:200]}...")

        # No max chunks limit for recursive splitting - process entire document
        logging.info(f"DEBUG: No chunk limit applied, chunk_size: {chunk_size}")

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            separators=["\n\n", "\n", ". ", "! ", "? ", " ", ""]
        )

        raw_chunks = splitter.split_text(full_text)
        logging.info(f"DEBUG: Raw chunks created: {len(raw_chunks)}")
        for i, chunk in enumerate(raw_chunks[:5]):  # Log first 5 chunks
            logging.info(f"DEBUG: Raw chunk {i+1} length: {len(chunk)}, preview: {chunk[:100]}...")

        documents = []
        for ch_idx, ch in enumerate(raw_chunks):  # Process all chunks, no limit
            meta = {}
            
            # Clean chunk content for searching
            clean_ch = ch.strip()
            if not clean_ch:
                # Empty chunk, assign first page as fallback
                meta['page_number'] = 1
                documents.append(Document(page_content=ch, metadata=meta))
                logging.info(f"DEBUG: Empty chunk {ch_idx+1} assigned to page 1")
                continue
            
            # Try multiple search strategies to find chunk position
            idx = -1
            search_attempts = [
                ch,  # exact match
                clean_ch,  # stripped
                ch[:min(100, len(ch))],  # first 100 chars
                clean_ch[:min(50, len(clean_ch))] if len(clean_ch) >= 50 else clean_ch  # first 50 chars
            ]
            
            for attempt in search_attempts:
                if not attempt:
                    continue
                try:
                    idx = full_text.index(attempt)
                    break
                except ValueError:
                    continue
            
            if idx >= 0:
                start_idx = idx
                end_idx = idx + len(ch) - 1

                # Find start_page and end_page with improved logic
                start_page = None
                end_page = None
                
                # Find which page contains the start of the chunk
                for pi, (s, e) in enumerate(zip(page_starts, page_ends)):
                    if start_idx >= s and start_idx < e:
                        start_page = pi + 1
                        break
                
                # Find which page contains the end of the chunk
                for pi, (s, e) in enumerate(zip(page_starts, page_ends)):
                    if end_idx >= s and end_idx < e:
                        end_page = pi + 1
                        break
                
                # Assign metadata based on what we found
                if start_page and end_page:
                    if start_page == end_page:
                        meta['page_number'] = start_page
                    else:
                        meta['page_number'] = start_page  # Use start page as primary
                        meta['end_page'] = end_page
                elif start_page:
                    meta['page_number'] = start_page
                elif end_page:
                    meta['page_number'] = end_page
                else:
                    # Fallback: assign to middle page based on chunk index
                    estimated_page = min(len(self.pages), max(1, (ch_idx * len(self.pages) // len(raw_chunks)) + 1))
                    meta['page_number'] = estimated_page
                    logging.warning(f"Could not determine exact page for chunk {ch_idx+1}, assigned estimated page {estimated_page}")
            else:
                # Could not find chunk in full text, use estimation
                estimated_page = min(len(self.pages), max(1, (ch_idx * len(self.pages) // len(raw_chunks)) + 1))
                meta['page_number'] = estimated_page
                logging.warning(f"Could not locate chunk {ch_idx+1} in full text, assigned estimated page {estimated_page}")

            documents.append(Document(page_content=ch, metadata=meta))
            logging.info(f"DEBUG: Final chunk {ch_idx+1} - metadata: {meta}, content length: {len(ch)}")

        logging.info(f"DEBUG: Total final documents created: {len(documents)}")
        return documents