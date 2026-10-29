"""Tool definitions and dispatcher for LLM function calling."""
from __future__ import annotations

from typing import Any

from retrieval.tools import (
    get_chunk,
    get_toc,
    read_section,
    search_chunks,
    search_figures,
    search_sections,
)

# Open-AI compatible tool schemas
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_chunks",
            "description": "Search granular textbook passages, tables, and callouts by keyword or concept.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Specific medical term, symptom, anatomical landmark, or dosage."
                    },
                    "book_slug": {
                        "type": "string",
                        "description": "Optional book identifier to restrict search to a single volume."
                    },
                    "allowed_books": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of book identifiers to restrict search scope."
                    },
                    "section_id": {
                        "type": "integer",
                        "description": "Optional section ID to restrict search within that section."
                    },
                    "content_type": {
                        "type": "string",
                        "enum": ["prose", "table", "box", "figure_caption"],
                        "description": "Filter by block type."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of chunks to return (default: 6, up to 8)."
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_sections",
            "description": "Search section and chapter headings by concept or condition to locate relevant clinical context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Condition, disease, organ system, or topic heading."
                    },
                    "book_slug": {
                        "type": "string",
                        "description": "Optional book identifier."
                    },
                    "allowed_books": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of book identifiers to restrict search scope."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of sections to return (default: 5)."
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_section",
            "description": "Read an entire textbook subsection in reading order with its prose and tables.",
            "parameters": {
                "type": "object",
                "properties": {
                    "book_slug": {
                        "type": "string",
                        "description": "Book slug identifier."
                    },
                    "section_id": {
                        "type": "integer",
                        "description": "The section ID to read."
                    },
                    "max_tokens": {
                        "type": "integer",
                        "description": "Token cap to avoid context overflow (default: 2500)."
                    }
                },
                "required": ["book_slug", "section_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_toc",
            "description": "Browse table of contents or child subsections under a chapter.",
            "parameters": {
                "type": "object",
                "properties": {
                    "book_slug": {
                        "type": "string",
                        "description": "Book slug identifier."
                    },
                    "parent_id": {
                        "type": "integer",
                        "description": "Parent section ID, or omit for root chapters."
                    }
                },
                "required": ["book_slug"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_chunk",
            "description": "Inspect a single chunk by its exact ID to retrieve its full text and citation metadata.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chunk_id": {
                        "type": "string",
                        "description": "Exact chunk ID."
                    }
                },
                "required": ["chunk_id"]
            }
        }
    }
]


def execute_tool(name: str, arguments: dict[str, Any]) -> Any:
    """Executes a retrieval tool by name with arguments."""
    if name == "search_chunks":
        return search_chunks(**arguments)
    elif name == "search_sections":
        return search_sections(**arguments)
    elif name == "read_section":
        return read_section(**arguments)
    elif name == "get_toc":
        return get_toc(**arguments)
    elif name == "get_chunk":
        return get_chunk(**arguments)
    elif name == "search_figures":
        return search_figures(**arguments)
    else:
        raise ValueError(f"Unknown tool: {name}")

