-- PostgreSQL + pgvector Schema for Text-Book-RAG

CREATE EXTENSION IF NOT EXISTS vector;

-- 1. Books Table
CREATE TABLE IF NOT EXISTS books (
    id SERIAL PRIMARY KEY,
    slug VARCHAR(255) UNIQUE NOT NULL,
    title VARCHAR(500) NOT NULL,
    total_pages INT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 2. Sections Table
CREATE TABLE IF NOT EXISTS sections (
    id INT NOT NULL,
    book_slug VARCHAR(255) NOT NULL REFERENCES books(slug) ON DELETE CASCADE,
    parent_id INT,
    title TEXT NOT NULL,
    level INT NOT NULL,
    kind VARCHAR(100),
    use VARCHAR(50) DEFAULT 'teaching',
    pdf_start INT NOT NULL,
    pdf_end INT NOT NULL,
    heading_path TEXT NOT NULL,
    is_leaf BOOLEAN DEFAULT TRUE,
    summary TEXT,
    keywords TEXT[],
    tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(heading_path, '') || ' ' || coalesce(title, '') || ' ' || coalesce(summary, ''))
    ) STORED,
    PRIMARY KEY (book_slug, id)
);

CREATE INDEX IF NOT EXISTS idx_sections_tsv ON sections USING GIN(tsv);
CREATE INDEX IF NOT EXISTS idx_sections_book ON sections(book_slug);
CREATE INDEX IF NOT EXISTS idx_sections_parent ON sections(book_slug, parent_id);

-- 3. Chunks Table
CREATE TABLE IF NOT EXISTS chunks (
    id SERIAL PRIMARY KEY,
    chunk_id VARCHAR(255) UNIQUE NOT NULL,
    book_slug VARCHAR(255) NOT NULL REFERENCES books(slug) ON DELETE CASCADE,
    section_id INT NOT NULL,
    content_type VARCHAR(50) NOT NULL,
    sub_type VARCHAR(100),
    page_start INT NOT NULL,
    page_end INT NOT NULL,
    token_count INT NOT NULL,
    text TEXT NOT NULL,
    embed_text TEXT NOT NULL,
    content_hash VARCHAR(64) UNIQUE NOT NULL,
    embedding vector(1536),
    tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(embed_text, '') || ' ' || coalesce(text, ''))
    ) STORED,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (book_slug, section_id) REFERENCES sections(book_slug, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_chunks_tsv ON chunks USING GIN(tsv);
CREATE INDEX IF NOT EXISTS idx_chunks_book_section ON chunks(book_slug, section_id);
CREATE INDEX IF NOT EXISTS idx_chunks_content_type ON chunks(content_type);
