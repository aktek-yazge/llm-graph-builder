from langchain_text_splitters import TokenTextSplitter
from langchain.docstore.document import Document
from langchain_neo4j import Neo4jGraph
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain.text_splitter import MarkdownTextSplitter
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
        logging.info("Split whole document using MarkdownTextSplitter")

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

        # Check if we have single document with [PAGE BREAK] markers
        if (len(self.pages) == 1 and 
            hasattr(self.pages[0], 'page_content') and 
            "[PAGE BREAK]" in self.pages[0].page_content):
            
            logging.info("🔍 Single document with [PAGE BREAK] markers detected - splitting pages manually")
            
            # Split by PAGE BREAK markers first
            full_content = self.pages[0].page_content
            page_parts = full_content.split("[PAGE BREAK]")
            
            # Create proper page documents with page numbers
            page_documents = []
            for idx, page_content in enumerate(page_parts, start=1):
                if page_content.strip():  # Only non-empty pages
                    page_metadata = dict(self.pages[0].metadata) if self.pages[0].metadata else {}
                    page_metadata['page_number'] = idx
                    page_documents.append(Document(
                        page_content=page_content.strip(), 
                        metadata=page_metadata
                    ))
            
            logging.info(f"📄 Created {len(page_documents)} pages from PAGE BREAK markers")
            
            # Now update self.pages for processing
            self.pages = page_documents

        # Concatenate pages with page separators and record start/end offsets for each page
        page_texts = []
        page_starts = []
        page_ends = []
        current_offset = 0
        separator = "\n\n---\n\n"
        
        for i, p in enumerate(self.pages):
            page_text = p.page_content if hasattr(p, 'page_content') else str(p)
            # Clean and normalize text
            page_text = str(page_text).strip()
            
            page_texts.append(page_text)
            page_starts.append(current_offset)
            current_offset += len(page_text)
            page_ends.append(current_offset)
            
            # Add separator length for next page (except for the last page)
            if i < len(self.pages) - 1:
                current_offset += len(separator)
            
            logging.info(f"DEBUG: Page {i+1} - start: {page_starts[i]}, end: {page_ends[i]}, length: {len(page_text)}")
            if i < 3:  # Log first 3 pages content preview
                logging.info(f"DEBUG: Page {i+1} preview: {page_text[:100]}...")

        # Join pages with markdown page separators (preserving markdown structure)
        full_text = separator.join(page_texts)
        logging.info(f"DEBUG: Full markdown text length after concatenation: {len(full_text)} chars")
        logging.info(f"DEBUG: Full markdown text preview: {full_text[:200]}...")

        # No max chunks limit for markdown splitting - process entire document
        logging.info(f"DEBUG: No chunk limit applied, chunk_size: {chunk_size}")
        logging.info(f"DEBUG: No chunk limit applied, chunk_overlap: {chunk_overlap}")

        # Use MarkdownTextSplitter to respect markdown structure
        splitter = MarkdownTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap
        )

        # Create documents from the markdown text
        documents = splitter.create_documents([full_text])
        logging.info(f"DEBUG: Markdown chunks created: {len(documents)}")
        
        # Add page metadata to each chunk
        for ch_idx, doc in enumerate(documents):
            # Try to find which page(s) this chunk belongs to
            chunk_text = doc.page_content
            
            # Try multiple search strategies to find chunk position
            idx = -1
            search_attempts = [
                chunk_text,  # exact match
                chunk_text.strip(),  # stripped
                chunk_text[:min(100, len(chunk_text))],  # first 100 chars
            ]
            
            for attempt in search_attempts:
                if not attempt:
                    continue
                try:
                    idx = full_text.index(attempt)
                    break
                except ValueError:
                    continue
            
            meta = {}
            if idx >= 0:
                start_idx = idx
                end_idx = idx + len(chunk_text) - 1

                # Find start_page and end_page
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
                    estimated_page = min(len(self.pages), max(1, (ch_idx * len(self.pages) // len(documents)) + 1))
                    meta['page_number'] = estimated_page
                    logging.warning(f"Could not determine exact page for chunk {ch_idx+1}, assigned estimated page {estimated_page}")
            else:
                # Could not find chunk in full text, use estimation
                estimated_page = min(len(self.pages), max(1, (ch_idx * len(self.pages) // len(documents)) + 1))
                meta['page_number'] = estimated_page
                logging.warning(f"Could not locate chunk {ch_idx+1} in full text, assigned estimated page {estimated_page}")

            # Update document metadata
            doc.metadata.update(meta)
            logging.info(f"DEBUG: Final chunk {ch_idx+1} - metadata: {meta}, content length: {len(chunk_text)}")

        logging.info(f"DEBUG: Total final documents created with metadata: {len(documents)}")
        return documents