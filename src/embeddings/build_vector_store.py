"""
Chunks every note in discharge_notes.jsonl and embeds them into a
persistent ChromaDB collection.

Uses ChromaDB's default embedding function (a small, local ONNX build of
all-MiniLM-L6-v2) — no API key needed, consistent with this project's
MVP decision to run the vector store locally rather than depend on an
external embedding API. The model (~80MB) downloads once on first run
and is cached; that download needs normal internet access.
"""

import json
import sys
import logging
import chromadb
import shutil
import os

sys.path.insert(0, ".")
from src.embeddings.chunking import chunk_note_record
from src.utils.config import load_config
from src.utils.logging_config import setup_logging

logger = logging.getLogger(__name__)

CHROMA_PATH = "data/processed/chroma_db"
COLLECTION_NAME = "discharge_notes"
BATCH_SIZE = 100  # ChromaDB embeds per batch — keeps memory/progress reasonable


def build_vector_store(notes_path: str, embedding_function=None) -> chromadb.Collection:
    """
    embedding_function=None uses ChromaDB's real default (production path).
    Tests pass a deterministic stand-in here instead, to validate storage/
    query/metadata behavior without needing network access for a model
    download.
    """
    if os.path.exists(CHROMA_PATH):
        shutil.rmtree(CHROMA_PATH)

    client = chromadb.PersistentClient(path=CHROMA_PATH)

    kwargs = {"name": COLLECTION_NAME}
    if embedding_function is not None:
        kwargs["embedding_function"] = embedding_function
    collection = client.create_collection(**kwargs)

    records = [json.loads(line) for line in open(notes_path)]
    logger.info("Chunking %d notes...", len(records))

    all_chunks = []
    for r in records:
        all_chunks.extend(chunk_note_record(r))
    logger.info("Produced %d chunks from %d notes", len(all_chunks), len(records))

    for i in range(0, len(all_chunks), BATCH_SIZE):
        batch = all_chunks[i:i + BATCH_SIZE]
        collection.add(
            ids=[c["chunk_id"] for c in batch],
            documents=[c["text"] for c in batch],
            metadatas=[
                {
                    "patient_id": c["patient_id"],
                    "encounter_id": c["encounter_id"],
                    "discharge_ts": c["discharge_ts"],
                    "section_name": c["section_name"],
                    "chunk_index": c["chunk_index"],
                    "total_chunks": c["total_chunks"],
                }
                for c in batch
            ],
        )
        logger.info("Embedded batch %d-%d / %d", i, min(i + BATCH_SIZE, len(all_chunks)), len(all_chunks))

    return collection


if __name__ == "__main__":
    setup_logging()
    cfg = load_config()
    collection = build_vector_store(cfg.output_notes)
    print(f"Vector store built at {CHROMA_PATH}")
    print(f"Collection '{COLLECTION_NAME}' contains {collection.count()} chunks")