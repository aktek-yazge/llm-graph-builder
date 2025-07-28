import logging
from pathlib import Path
from langchain_community.document_loaders import PyMuPDFLoader
from langchain_docling import DoclingLoader
from langchain_docling.loader import ExportType
from docling.document_converter import DocumentConverter
from langchain_community.document_loaders import UnstructuredFileLoader
from langchain_core.documents import Document
import chardet
from langchain_core.document_loaders import BaseLoader
from docling_core.types.doc import DocItemLabel
from docling_core.types.doc.document import DEFAULT_EXPORT_LABELS
import csv
import io
from pathlib import Path
from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag
from markdown import markdown as md_to_html


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
        result = chardet.detect(raw_data)
        return result["encoding"] or "utf-8"


def load_document_content(file_path):
    file_extension = Path(file_path).suffix.lower()
    encoding_flag = False
    if file_extension == ".pdf":
        # converter = DocumentConverter()
        # loader = PyMuPDFLoader(file_path)
        labels = [
            label
            for label in DEFAULT_EXPORT_LABELS
            if label not in (DocItemLabel.PICTURE, DocItemLabel.PAGE_FOOTER)
        ]
        loader = DoclingLoader(
            file_path=file_path,
            export_type=ExportType.MARKDOWN,
            md_export_kwargs={
                "page_break_placeholder": "[PAGE BREAK]",
                "labels": labels,
            },
        )
        # loader = DoclingLoader(file_path, export_type=ExportType.MARKDOWN)
        return loader, encoding_flag
    elif file_extension == ".txt":
        encoding = detect_encoding(file_path)
        logging.info(f"Detected encoding for {file_path}: {encoding}")
        if encoding.lower() == "utf-8":
            loader = UnstructuredFileLoader(
                file_path, mode="elements", autodetect_encoding=True
            )
            return loader, encoding_flag
        else:
            with open(file_path, encoding=encoding, errors="replace") as f:
                content = f.read()
            loader = ListLoader(
                [Document(page_content=content, metadata={"source": file_path})]
            )
            encoding_flag = True
            return loader, encoding_flag
    else:
        loader = UnstructuredFileLoader(
            file_path, mode="elements", autodetect_encoding=True
        )
        return loader, encoding_flag


def get_documents_from_file_by_path(file_path, file_name):
    file_path = Path(file_path)
    if not file_path.exists():
        logging.info(f"File {file_name} does not exist")
        raise Exception(f"File {file_name} does not exist")
    logging.info(f"file {file_name} processing")
    try:
        loader, encoding_flag = load_document_content(file_path)
        file_extension = file_path.suffix.lower()
        if file_extension == ".pdf" or (file_extension == ".txt" and encoding_flag):
            loaded_docs = loader.load()
            content = loaded_docs[0].page_content
            txt = markdown_to_text_with_csv_tables(content, delimiter=",")
            # Eğer sadece bir Document ve içinde [PAGE BREAK] varsa split et
            if (
                file_extension == ".pdf"
                and len(loaded_docs) == 1
                and "[PAGE BREAK]" in txt
            ):
                page_texts = loaded_docs[0].page_content.split("[PAGE BREAK]")
                # Metadata'yı koru
                metadata = loaded_docs[0].metadata

                pages = []
                for idx, txt_part in enumerate(page_texts, start=1):
                    if txt_part.strip():
                        page_metadata = dict(metadata) if metadata else {}
                        page_metadata["page_number"] = idx
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
        raise Exception(f"Error while reading the file content or metadata, {e}")
    return file_name, pages, file_extension


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
                page_number += 1
                pages.append(Document(page_content=page_content))
                page_content = ""

            if page == unstructured_pages[-1]:
                pages.append(Document(page_content=page_content))

        elif page.metadata["category"] == "PageBreak" and page != unstructured_pages[0]:
            page_number += 1
            pages.append(Document(page_content=page_content, metadata=metadata))
            page_content = ""
            metadata = {}

        else:
            page_content += page.page_content
            metadata_with_custom_page_number = {
                "source": page.metadata["source"],
                "page_number": 1,
                "filename": page.metadata["filename"],
                "filetype": page.metadata["filetype"],
            }
            if page == unstructured_pages[-1]:
                pages.append(
                    Document(
                        page_content=page_content,
                        metadata=metadata_with_custom_page_number,
                    )
                )
    return pages
