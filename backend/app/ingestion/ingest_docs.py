"""
One-shot script: chunk + embed the provided documents into `doc_chunks`.
Run manually (`python -m app.ingestion.ingest_docs`), not on every backend
boot.

Chunking: one chunk per section, split on bold heading lines in the
extracted text (headings are plain bold text, not a PDF outline — this
covers both the numbered headings ("1. Return Window") and product_faq.pdf's
unnumbered ones ("Shipping and Delivery")). The first bold line of each file
is the document title with no body of its own and is dropped. NOT fixed
token windows — every section is ~40-80 words, and citing a whole section is
more useful than citing a token fragment.

Embeds with the same model tools.py uses at query time (see
config.EMBEDDING_MODEL_NAME) — model MUST match between ingest and query.

Expect ~21 rows (4+4+5+5+4 sections across the five files).

Source files in backend/data/ are fixed ground truth — never edited before
ingesting (see docs/ARCHITECTURE.md).
"""

import fitz
from sentence_transformers import SentenceTransformer

from app.config import DATA_DIR, EMBEDDING_MODEL_NAME
from app.db import get_conn, init_db

PDF_FILES = [
    "hr_leave_policy.pdf",
    "pricing_discounts_policy.pdf",
    "product_faq.pdf",
    "returns_policy.pdf",
    "warranty_policy.pdf",
]

BOLD_FLAG = 16  # fitz span flag bit for bold


def extract_sections(pdf_path) -> list[tuple[str, str]]:
    """[(section_title, section_text)] — bold line starts a section; the
    title-only first bold line (no body before the next heading) is dropped."""
    doc = fitz.open(pdf_path)
    sections: list[tuple[str, list[str]]] = []
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                spans = line["spans"]
                text = "".join(s["text"] for s in spans).strip()
                if not text:
                    continue
                if all(s["flags"] & BOLD_FLAG for s in spans):
                    sections.append((text, []))
                elif sections:
                    sections[-1][1].append(text)
    return [
        (title, f"{title}\n{' '.join(body)}") for title, body in sections if body
    ]


def main() -> None:
    init_db()
    model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    dim = model.get_embedding_dimension()

    with get_conn() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS doc_chunks (
                id            serial PRIMARY KEY,
                source_file   text NOT NULL,
                section_title text NOT NULL,
                chunk_text    text NOT NULL,
                chunk_index   int NOT NULL,
                embedding     vector({dim}) NOT NULL
            )
            """
        )
        conn.execute("TRUNCATE doc_chunks")  # idempotent re-runs

        total = 0
        for filename in PDF_FILES:
            sections = extract_sections(DATA_DIR / filename)
            embeddings = model.encode([text for _, text in sections])
            for idx, ((title, text), emb) in enumerate(zip(sections, embeddings)):
                conn.execute(
                    """
                    INSERT INTO doc_chunks
                        (source_file, section_title, chunk_text, chunk_index, embedding)
                    VALUES (%s, %s, %s, %s, %s::vector)
                    """,
                    (filename, title, text, idx, str(emb.tolist())),
                )
            print(f"{filename}: {len(sections)} sections")
            total += len(sections)

    print(f"Inserted {total} chunks into doc_chunks (expected ~21).")


if __name__ == "__main__":
    main()
