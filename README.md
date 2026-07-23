# ai-librarian
# AI Vault (ai-librarian)

A self-hosted, local-first AI knowledge platform. Turns a folder of personal
documents into a searchable, conversational knowledge base — fully offline,
no cloud APIs, no vendor lock-in.

> Own your data. Own your AI. Own your knowledge.

## Architecture
    /ai-vault (filesystem, source of truth)
        |
        +-- inbox/          <- drop files here, auto-sorted
        +-- books/
        +-- papers/
        +-- notes/
        +-- conversations/
        +-- generated/
             |
        Ingestion Service (Python, containerized)
        - classifies + sorts inbox files via Ollama
        - extracts text, chunks, embeds
             |
        Ollama (GPU-accelerated)
        - llama3.2:3b        -> chat + classification
        - nomic-embed-text    -> embeddings
             |
        Qdrant
        - vector storage, semantic search
             |
        Open WebUI
        - chat interface, RAG-connected to Qdrant "vault" collection
