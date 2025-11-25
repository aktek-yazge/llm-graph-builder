import logging
from pathlib import Path
from langchain_community.document_loaders import PyMuPDFLoader
# Docling is only needed for document processing, which is done in celery_worker
try:
    from langchain_docling import DoclingLoader
    from langchain_docling.loader import ExportType
    from docling_core.types.doc import ImageRefMode, PictureItem, TableItem
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
except (ImportError, ModuleNotFoundError):
    # Document processing is in celery_worker
    DoclingLoader = None
    ExportType = None
    ImageRefMode = None
    PictureItem = None
    TableItem = None
    InputFormat = None
    PdfPipelineOptions = None
    DocumentConverter = None
    PdfFormatOption = None
from langchain_community.document_loaders import UnstructuredFileLoader
from langchain_core.documents import Document
# chardet is only needed for encoding detection, which is done in celery_worker
try:
    import chardet
except (ImportError, ModuleNotFoundError):
    chardet = None  # Encoding detection is in celery_worker
from langchain_core.document_loaders import BaseLoader
# docling_core is only needed for document processing, which is done in celery_worker
try:
    from docling_core.types.doc import DocItemLabel
    from docling_core.types.doc.document import DEFAULT_EXPORT_LABELS
except (ImportError, ModuleNotFoundError):
    DocItemLabel = None
    DEFAULT_EXPORT_LABELS = None
from src.utf8_utils import normalize_unicode_text, normalize_file_name
import csv
import io
import os
import time
from pathlib import Path
# BeautifulSoup and markdown are only needed for document processing, which is done in celery_worker
try:
    from bs4 import BeautifulSoup
    from bs4.element import NavigableString, Tag
    from markdown import markdown as md_to_html
except (ImportError, ModuleNotFoundError):
    BeautifulSoup = None
    NavigableString = None
    Tag = None
    md_to_html = None
try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

# Image resolution scale constant
IMAGE_RESOLUTION_SCALE = 2.0  # 2x scale for better quality


def demo_page_image_generation():
    """
    Example function showing how to use the page image generation feature.
    """
    logging.basicConfig(level=logging.INFO)
    
    # Example usage
    file_path = "path/to/your/document.pdf"
    output_dir = "output"
    
    try:
        file_name, pages, file_extension, generated_images = get_documents_from_file_by_path(
            file_path=file_path, 
            file_name="document.pdf", 
            generate_images=True, 
            output_dir=output_dir
        )
        
        print(f"Processed {len(pages)} pages")
        print(f"Generated {len(generated_images)} page images:")
        for img_path in generated_images:
            print(f"  - {img_path}")
            
    except Exception as e:
        print(f"Error: {e}")


class ListLoader(BaseLoader):
    """Basit bir document listesi loader'ı"""
    def __init__(self, docs):
        self.docs = docs
    
    def load(self):
        return self.docs


def _norm(s: str) -> str:
    # Hücre içi whitespace'leri tek boşluğa indir (satır sonlarını da düzler)
    return " ".join((s or "").split())


def _table_to_csv_text(
    table_tag: Tag, delimiter=";", fill_down_cols=(), drop_fully_empty_rows=True
) -> str:
    """HTML <table> -> CSV metni"""
    # Başlıklar
    headers = []
    thead = table_tag.find("thead")
    if thead:
        tr = thead.find("tr")
        if tr:
            headers = [
                _norm(th.get_text(" ", strip=True)) for th in tr.find_all(["th", "td"])
            ]

    # Satırlar
    rows = []
    tbody = table_tag.find("tbody") or table_tag
    for tr in tbody.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        row = [_norm(td.get_text(" ", strip=True)) for td in cells]
        # thead yoksa ve ilk satır başlığa eşitse yinelenmesin
        if headers and row == headers:
            continue
        rows.append(row)

    if not headers and rows:
        headers = rows.pop(0)

    # Tamamen boş satırları at
    if drop_fully_empty_rows:
        rows = [r for r in rows if any(cell for cell in r)]

    max_cols = max([len(headers)] + [len(r) for r in rows]) if (headers or rows) else 0

    def pad(r):
        return r + [""] * (max_cols - len(r))

    # Fill-down (istenirse, ör. fill_down_cols=(0,) ile ilk sütunu yukarıdan doldur)
    if fill_down_cols:
        last_vals = [""] * max_cols
        for i, r in enumerate(rows):
            r = pad(r)
            for c in fill_down_cols:
                if c < max_cols and r[c] == "" and last_vals[c]:
                    r[c] = last_vals[c]
            for c in range(max_cols):
                if r[c] != "":
                    last_vals[c] = r[c]
            rows[i] = r
    else:
        rows = [pad(r) for r in rows]

    # CSV yaz
    buf = io.StringIO()
    writer = csv.writer(
        buf,
        delimiter=delimiter,
        quotechar='"',
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\n",
        escapechar="\\",
    )
    if headers:
        writer.writerow(pad(headers))
    for r in rows:
        writer.writerow(r)
    return buf.getvalue().rstrip("\n")


def _render_children(tag: Tag, lines: list[str], indent=0, **opts):
    for child in tag.children:
        _render_node(child, lines, indent=indent, **opts)


def _render_node(
    node,
    lines: list[str],
    indent=0,
    delimiter=";",
    fill_down_cols=(),
    drop_fully_empty_rows=True,
):
    if isinstance(node, NavigableString):
        text = str(node)
        if text.strip():
            lines.append((" " * indent) + _norm(text))
        return

    if not isinstance(node, Tag):
        return

    name = node.name

    if name in ["h1", "h2", "h3", "h4", "h5", "h6"]:
        txt = node.get_text(" ", strip=True)
        if txt:
            lines.append(txt)
            lines.append("")  # boş satır
        return

    if name == "p":
        txt = node.get_text(" ", strip=True)
        if txt:
            lines.append(txt)  # <--- boş satır eklemiyoruz
        return

    if name in ["ul", "ol"]:
        i = 1
        for li in node.find_all("li", recursive=False):
            sub_lines = []
            _render_children(
                li,
                sub_lines,
                indent=0,
                delimiter=delimiter,
                fill_down_cols=fill_down_cols,
                drop_fully_empty_rows=drop_fully_empty_rows,
            )
            marker = f"{i}. " if name == "ol" else "- "
            if sub_lines:
                lines.append((" " * indent) + marker + sub_lines[0])
                for sl in sub_lines[1:]:
                    lines.append((" " * (indent + len(marker))) + sl)
            else:
                lines.append((" " * indent) + marker)
            lines.append("")
            if name == "ol":
                i += 1
        return

    if name == "pre":
        code = node.get_text("", strip=False)
        if code:
            lines.append(code.rstrip("\n"))
            lines.append("")
        return

    if name == "table":
        csv_text = _table_to_csv_text(
            node,
            delimiter=delimiter,
            fill_down_cols=fill_down_cols,
            drop_fully_empty_rows=drop_fully_empty_rows,
        )
        if csv_text:
            lines.append(csv_text)
            lines.append("")
        return

    # Varsayılan: alt düğümleri işle
    _render_children(
        node,
        lines,
        indent=indent,
        delimiter=delimiter,
        fill_down_cols=fill_down_cols,
        drop_fully_empty_rows=drop_fully_empty_rows,
    )


def markdown_to_text_with_csv_tables(
    md_text: str,
    delimiter: str = ";",
    fill_down_cols=(),  # <-- varsayılanı açtık
    drop_fully_empty_rows: bool = True,
) -> str:
    """
    Markdown içeriğini düz metne çevirir:
      - Tablaları CSV bloklarına dönüştürür (aynı akış içinde, sırayı korur)
      - Diğer tüm içeriği (başlık, paragraf, liste, kod) metin olarak korur
    return: Tek bir string (JSON değil)
    """
    html = md_to_html(md_text, extensions=["tables", "fenced_code"])
    soup = BeautifulSoup(html, "html.parser")

    lines: list[str] = []
    _render_children(
        soup,
        lines,
        indent=0,
        delimiter=delimiter,
        fill_down_cols=fill_down_cols,
        drop_fully_empty_rows=drop_fully_empty_rows,
    )

    # Ardışık birden fazla boş satırı teke indir
    compact = []
    prev_blank = False
    for ln in (ln.rstrip() for ln in lines):
        blank = ln.strip() == ""
        if blank and prev_blank:
            continue
        compact.append(ln)
        prev_blank = blank

    return ("\n".join(compact)).strip() + "\n"


class ListLoader(BaseLoader):
    """A wrapper to make a list of Documents compatible with BaseLoader."""

    def __init__(self, documents):
        self.documents = documents

    def load(self):
        return self.documents


def detect_encoding(file_path):
    """Detects the file encoding to avoid UnicodeDecodeError."""
    with open(file_path, "rb") as f:
        raw_data = f.read(4096)
        if chardet is None:
            return "utf-8"  # Default encoding if chardet not available
        result = chardet.detect(raw_data)
        return result["encoding"] or "utf-8"


def generate_page_images_with_pymupdf(file_path, output_dir="output"):
    """
    PyMuPDF (fitz) kullanarak PDF dosyasından page image'larını generate eder.
    
    Args:
        file_path: PDF dosyasının yolu
        output_dir: Çıktı klasörü (varsayılan: "output")
        
    Returns:
        List[str]: Kaydedilen image dosyalarının yolları
    """
    try:
        if fitz is None:
            logging.warning("PyMuPDF (fitz) is not available for image generation")
            return []
            
        # Output directory'yi oluştur
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        start_time = time.time()
        logging.info(f"🖼️ [PyMuPDF] Starting page image generation for {file_path}")
        
        # PDF'i aç
        doc = fitz.open(str(file_path))
        doc_filename = Path(file_path).stem
        saved_images = []
        
        # Her sayfayı image olarak kaydet
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            
            # Page'i image'a çevir (2x scale için matrix kullan)
            mat = fitz.Matrix(IMAGE_RESOLUTION_SCALE, IMAGE_RESOLUTION_SCALE)
            pix = page.get_pixmap(matrix=mat)
            
            # PNG olarak kaydet
            page_image_filename = output_path / f"{doc_filename}_page_{page_num + 1:03d}.png"
            pix.save(str(page_image_filename))
            pix = None  # Memory cleanup
            
            saved_images.append(str(page_image_filename))
            logging.info(f"Saved PyMuPDF page image: {page_image_filename}")
        
        doc.close()
        
        elapsed_time = time.time() - start_time
        logging.info(f"PyMuPDF page image generation completed in {elapsed_time:.2f} seconds. Generated {len(saved_images)} images.")
        
        return saved_images
        
    except Exception as e:
        logging.error(f"Error generating page images with PyMuPDF for {file_path}: {e}")
        return []


def generate_page_images_from_converter(converter, file_path, output_dir="output"):
    """
    Mevcut DocumentConverter kullanarak page image'larını generate eder.
    
    Args:
        converter: DocumentConverter instance
        file_path: PDF dosyasının yolu
        output_dir: Çıktı klasörü (varsayılan: "output")
        
    Returns:
        List[str]: Kaydedilen image dosyalarının yolları
    """
    try:
        # Output directory'yi oluştur
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        start_time = time.time()
        logging.info(f"🖼️ [Docling] Starting page image generation for {file_path}")
        
        # Document'i convert et
        conv_res = converter.convert(file_path)
        
        doc_filename = Path(file_path).stem
        saved_images = []
        
        # Page image'larını kaydet
        for page_no, page in conv_res.document.pages.items():
            if hasattr(page, 'image') and page.image and hasattr(page.image, 'pil_image'):
                page_image_filename = output_path / f"{doc_filename}_page_{page_no:03d}.png"
                with page_image_filename.open("wb") as fp:
                    page.image.pil_image.save(fp, format="PNG")
                saved_images.append(str(page_image_filename))
                logging.info(f"Saved page image: {page_image_filename}")
        
        elapsed_time = time.time() - start_time
        logging.info(f"Page image generation completed in {elapsed_time:.2f} seconds. Generated {len(saved_images)} images.")
        
        return saved_images
        
    except Exception as e:
        logging.error(f"Error generating page images for {file_path}: {e}")
        return []


def generate_page_images(file_path, output_dir="output"):
    """
    PDF dosyasından page image'larını generate eder ve belirtilen klasöre kaydeder.
    
    Args:
        file_path: PDF dosyasının yolu
        output_dir: Çıktı klasörü (varsayılan: "output")
        
    Returns:
        List[str]: Kaydedilen image dosyalarının yolları
    """
    try:
        # Output directory'yi oluştur
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        # Pipeline options ile PDF format option oluştur
        pipeline_options = PdfPipelineOptions(
            images_scale=IMAGE_RESOLUTION_SCALE,
            generate_page_images=True,
            generate_picture_images=True
        )
        
        # Document converter'ı oluştur
        doc_converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )
        
        start_time = time.time()
        logging.info(f"Starting page image generation for {file_path}")
        
        # Document'i convert et
        conv_res = doc_converter.convert(file_path)
        
        doc_filename = Path(file_path).stem
        saved_images = []
        
        # Page image'larını kaydet
        for page_no, page in conv_res.document.pages.items():
            if hasattr(page, 'image') and page.image and hasattr(page.image, 'pil_image'):
                page_image_filename = output_path / f"{doc_filename}_page_{page_no:03d}.png"
                with page_image_filename.open("wb") as fp:
                    page.image.pil_image.save(fp, format="PNG")
                saved_images.append(str(page_image_filename))
                logging.info(f"Saved page image: {page_image_filename}")
        
        elapsed_time = time.time() - start_time
        logging.info(f"Page image generation completed in {elapsed_time:.2f} seconds. Generated {len(saved_images)} images.")
        
        return saved_images
        
    except Exception as e:
        logging.error(f"Error generating page images for {file_path}: {e}")
        return []


def load_document_content(file_path, generate_images=False, output_dir="output"):
    file_extension = Path(file_path).suffix.lower()
    encoding_flag = False
    generated_images = []
    
    # Docling desteklenen formatlar: PDF, DOCX, PPTX, HTML, CSV, Markdown
    docling_supported_formats = [".pdf", ".docx", ".pptx", ".html", ".csv", ".md"]
    
    if file_extension in docling_supported_formats:
        logging.info(f"Using Docling for {file_extension} file processing (generate_images={generate_images})")
        
        # Common labels for all document types
        labels = [
            label
            for label in DEFAULT_EXPORT_LABELS
            if label not in (DocItemLabel.PICTURE, DocItemLabel.PAGE_FOOTER)
        ]
        
        # Image generation için custom converter oluştur
        if generate_images:
            # PDF için özel yaklaşım: sadece PyMuPDF kullan
            if file_extension == ".pdf":
                # PDF için standart DoclingLoader (image generation olmadan)
                loader = DoclingLoader(
                    file_path=file_path,
                    export_type=ExportType.MARKDOWN,
                    md_export_kwargs={
                        "page_break_placeholder": "[PAGE BREAK]",
                        "labels": labels,
                    },
                )
                
                # PDF için sadece PyMuPDF ile image generation yap
                generated_images = generate_page_images_with_pymupdf(file_path, output_dir)
            else:
                # Diğer formatlar için Docling converter ile image generation
                custom_converter = DocumentConverter()
                
                # DoclingLoader'a custom converter'ı geç
                loader = DoclingLoader(
                    file_path=file_path,
                    converter=custom_converter,
                    export_type=ExportType.MARKDOWN,
                    md_export_kwargs={
                        "page_break_placeholder": "[PAGE BREAK]",
                        "labels": labels,
                    },
                )
                
                # Diğer formatlar için Docling converter'dan image'ları extract et
                generated_images = generate_page_images_from_converter(
                    custom_converter, file_path, output_dir
                )
        else:
            # Standart DoclingLoader kullan (image generation olmadan)
            loader = DoclingLoader(
                file_path=file_path,
                export_type=ExportType.MARKDOWN,
                md_export_kwargs={
                    "page_break_placeholder": "[PAGE BREAK]",
                    "labels": labels,
                },
            )
        
        return loader, encoding_flag, generated_images
    elif file_extension == ".txt":
        encoding = detect_encoding(file_path)
        logging.info(f"Detected encoding for {file_path}: {encoding}")
        if encoding.lower() == "utf-8":
            loader = UnstructuredFileLoader(
                file_path, mode="elements", autodetect_encoding=True
            )
            return loader, encoding_flag, generated_images
        else:
            with open(file_path, encoding=encoding, errors="replace") as f:
                content = f.read()
            loader = ListLoader(
                [Document(page_content=content, metadata={"source": file_path})]
            )
            encoding_flag = True
            return loader, encoding_flag, generated_images
    else:
        loader = UnstructuredFileLoader(
            file_path, mode="elements", autodetect_encoding=True
        )
        return loader, encoding_flag, generated_images


def get_documents_from_file_by_path(file_path, file_name, generate_images=False, output_dir="output"):
    file_path = Path(file_path)
    if not file_path.exists():
        logging.warning(f"File {file_name} does not exist at path: {file_path}")
        raise FileNotFoundError(f"File {file_name} does not exist")
    
    # File name'i normalize et
    file_name = normalize_file_name(file_name)
    logging.info(f"file {file_name} processing")
    
    generated_images = []
    
    try:
        loader, encoding_flag, generated_images = load_document_content(file_path, generate_images, output_dir)
        file_extension = file_path.suffix.lower()
        if file_extension == ".pdf" or (file_extension == ".txt" and encoding_flag):
            try:
                loaded_docs = loader.load()
                content = loaded_docs[0].page_content
                
                # UTF-8 ve Unicode normalization
                content = normalize_unicode_text(content)
                
                txt = markdown_to_text_with_csv_tables(content, delimiter=",")
            except Exception as pdf_error:
                if file_extension == ".pdf":
                    logging.warning(f"Docling PDF parsing failed for {file_name}: {pdf_error}")
                    logging.info(f"Trying fallback PDF loader (PyMuPDF) for {file_name}")
                    
                    # Fallback: PyMuPDF kullan
                    try:
                        from langchain_community.document_loaders import PyMuPDFLoader
                        fallback_loader = PyMuPDFLoader(str(file_path))
                        loaded_docs = fallback_loader.load()
                        
                        if loaded_docs and len(loaded_docs) > 0:
                            content = loaded_docs[0].page_content
                            content = normalize_unicode_text(content)
                            txt = markdown_to_text_with_csv_tables(content, delimiter=",")
                            logging.info(f"✅ Fallback PDF parsing successful for {file_name}")
                            
                            # PyMuPDF fallback'inde de image generation yap
                            if generate_images:
                                generated_images = generate_page_images_with_pymupdf(file_path, output_dir)
                        else:
                            raise Exception(f"Fallback PDF loader returned no content for {file_name}")
                            
                    except Exception as fallback_error:
                        logging.error(f"❌ PyMuPDF fallback also failed for {file_name}: {fallback_error}")
                        
                        # İkinci fallback: UnstructuredPDFLoader dene
                        try:
                            from langchain_community.document_loaders import UnstructuredPDFLoader
                            unstructured_loader = UnstructuredPDFLoader(str(file_path))
                            loaded_docs = unstructured_loader.load()
                            
                            if loaded_docs and len(loaded_docs) > 0:
                                content = " ".join([doc.page_content for doc in loaded_docs])
                                content = normalize_unicode_text(content)
                                txt = markdown_to_text_with_csv_tables(content, delimiter=",")
                                logging.info(f"✅ Unstructured PDF parsing successful for {file_name}")
                            else:
                                raise Exception(f"Unstructured PDF loader returned no content for {file_name}")
                                
                        except Exception as unstructured_error:
                            logging.error(f"❌ Unstructured PDF parsing also failed for {file_name}: {unstructured_error}")
                            
                            # Son çare: Basit text extraction dene
                            try:
                                if fitz is None:
                                    raise Exception("PyMuPDF (fitz) is not available")
                                    
                                doc = fitz.open(str(file_path))
                                content = ""
                                for page_num in range(len(doc)):
                                    page = doc.load_page(page_num)
                                    content += page.get_text() + "\n[PAGE BREAK]\n"
                                doc.close()
                                
                                if content.strip():
                                    content = normalize_unicode_text(content)
                                    txt = markdown_to_text_with_csv_tables(content, delimiter=",")
                                    loaded_docs = [Document(page_content=content, metadata={"source": str(file_path)})]
                                    logging.info(f"✅ Basic text extraction successful for {file_name}")
                                    
                                    # Basic text extraction'da da image generation yap
                                    if generate_images:
                                        generated_images = generate_page_images_with_pymupdf(file_path, output_dir)
                                else:
                                    raise Exception(f"No extractable text found in {file_name}")
                                    
                            except Exception as basic_error:
                                logging.error(f"❌ All PDF parsing methods failed for {file_name}: {basic_error}")
                                raise Exception(f"Unable to parse PDF file {file_name}. The file may be corrupted, encrypted, or have unsupported formatting.")
                else:
                    raise pdf_error
            
            # Eğer sadece bir Document ve içinde [PAGE BREAK] varsa split et
            if (
                file_extension == ".pdf"
                and len(loaded_docs) == 1
                and "[PAGE BREAK]" in txt
            ):
                page_texts = txt.split("[PAGE BREAK]")
                # Metadata'yı koru
                metadata = loaded_docs[0].metadata

                pages = []
                for idx, txt_part in enumerate(page_texts, start=1):
                    if txt_part.strip():
                        page_metadata = dict(metadata) if metadata else {}
                        page_metadata["page_number"] = idx
                        page_metadata["page"] = idx
                        pages.append(
                            Document(
                                page_content=txt_part.strip(), metadata=page_metadata
                            )
                        )
            else:
                pages = loaded_docs
        else:
            unstructured_pages = loader.load()
            pages = get_pages_with_page_numbers(unstructured_pages)
    except Exception as e:
        logging.error(f"❌ Complete document processing failed for {file_name}: {e}")
        raise Exception(f"Error while reading the file content or metadata, {e}")
    
    # Generated images'ı metadata'ya ekle
    if generated_images:
        for page in pages:
            if hasattr(page, 'metadata') and page.metadata:
                page.metadata['generated_images'] = generated_images
    
    return file_name, pages, file_extension, generated_images


def get_pages_with_page_numbers(unstructured_pages):
    pages = []
    page_number = 1
    page_content = ""
    metadata = {}
    for page in unstructured_pages:
        if "page_number" in page.metadata:
            if page.metadata["page_number"] == page_number:
                page_content += page.page_content
                metadata = {
                    "source": page.metadata["source"],
                    "page_number": page_number,
                    "filename": page.metadata["filename"],
                    "filetype": page.metadata["filetype"],
                }

            if page.metadata["page_number"] > page_number:
                # Mevcut sayfa içeriğini kaydet (önceki sayfa için)
                if page_content.strip():
                    pages.append(Document(page_content=page_content, metadata=metadata))
                
                # Yeni sayfaya geç
                page_number = page.metadata["page_number"]
                page_content = page.page_content
                metadata = {
                    "source": page.metadata["source"],
                    "page_number": page_number,
                    "filename": page.metadata["filename"],
                    "filetype": page.metadata["filetype"],
                }

            if page == unstructured_pages[-1]:
                # Son sayfa için metadata ile birlikte kaydet
                if page_content.strip():
                    pages.append(Document(page_content=page_content, metadata=metadata))

        elif page.metadata.get("category") == "PageBreak" and page != unstructured_pages[0]:
            # PageBreak ile sayfa geçişi
            if page_content.strip():
                pages.append(Document(page_content=page_content, metadata=metadata))
            page_number += 1
            page_content = ""
            metadata = {
                "source": page.metadata.get("source", ""),
                "page_number": page_number,
                "filename": page.metadata.get("filename", ""),
                "filetype": page.metadata.get("filetype", ""),
            }

        else:
            # Normal sayfa içeriği
            page_content += page.page_content
            # Metadata yoksa mevcut page_number kullan
            if not metadata:
                metadata = {
                    "source": page.metadata.get("source", ""),
                    "page_number": page_number,
                    "filename": page.metadata.get("filename", ""),
                    "filetype": page.metadata.get("filetype", ""),
                }
            
            if page == unstructured_pages[-1]:
                # Son sayfa için metadata ile birlikte kaydet
                if page_content.strip():
                    pages.append(Document(page_content=page_content, metadata=metadata))
    
    return pages
