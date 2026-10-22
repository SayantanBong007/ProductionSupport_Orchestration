"""
Incident memory — ChromaDB semantic index + JSON audit archive.

  store_incident(incident_id, record)   embed the diagnosis, archive the full record
                                        (alert, evidence ledger, agent trace, diagnosis)
  search_similar(text, k)               semantic search over past diagnoses, joined
                                        with how their remediation actually turned out
  update_remediation(incident_id, ...)  append the remediation outcome to the archive

ChromaDB uses its built-in ONNX MiniLM-L6-v2 embedding (no API, no torch). All
ChromaDB I/O runs in a worker thread so it never blocks the event loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("rag_store")

REPORTS_DIR = Path(os.getenv("REPORTS_DIR", "/app/reports"))
CHROMA_DIR  = Path(os.getenv("CHROMA_DIR",  "/app/chroma"))
COLLECTION  = "incident_memory"

_collection = None   # lazy singleton


def _get_collection():
    global _collection
    if _collection is None:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        import chromadb
        client = chromadb.PersistentClient(path=str(CHROMA_DIR),
                                           settings=chromadb.Settings(anonymized_telemetry=False))
        _collection = client.get_or_create_collection(name=COLLECTION, metadata={"hnsw:space": "cosine"})
        logger.info(f"ChromaDB collection '{COLLECTION}' ready — {_collection.count()} incidents")
    return _collection


def _report_path(incident_id: str) -> Path:
    return REPORTS_DIR / f"{incident_id}.json"


def _read_record(incident_id: str) -> dict | None:
    path = _report_path(incident_id)
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, ValueError):
        return None


async def store_incident(incident_id: str, record: dict) -> None:
    diagnosis = record.get("diagnosis") or {}

    def _sync():
        col = _get_collection()
        doc = (f"Alert: {'; '.join(record.get('alert', {}).get('signals', []))}. "
               f"Category: {diagnosis.get('failure_category', 'unknown')}. "
               f"Root cause: {diagnosis.get('root_cause', '')}. "
               f"Summary: {diagnosis.get('summary', '')[:600]}")
        col.upsert(ids=[incident_id], documents=[doc], metadatas=[{
            "incident_id":      incident_id,
            "timestamp":        record.get("created_at", ""),
            "failure_category": diagnosis.get("failure_category", "unknown"),
            "root_cause":       diagnosis.get("root_cause", "")[:300],
            "severity":         diagnosis.get("severity", ""),
            "runbook":          (record.get("remediation_proposal") or {}).get("runbook_id", "none"),
        }])
        _report_path(incident_id).write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")

    await asyncio.to_thread(_sync)
    logger.info(f"Incident stored: {incident_id}")


async def search_similar(text: str, k: int = 3) -> list[dict]:
    def _sync():
        col = _get_collection()
        count = col.count()
        if count == 0:
            return []
        res = col.query(query_texts=[text], n_results=min(k, count), include=["metadatas", "distances"])
        out = []
        for meta, dist in zip(res["metadatas"][0], res["distances"][0]):
            rec = _read_record(meta.get("incident_id", "")) or {}
            outcome = rec.get("remediation") or {}
            out.append({**meta, "similarity": round(1.0 - float(dist), 3),
                        "remediation_success": outcome.get("success"),
                        "remediation_summary": (outcome.get("closure") or "")[:300]})
        return out

    return await asyncio.to_thread(_sync)


async def update_remediation(incident_id: str, outcome: dict) -> None:
    def _sync():
        rec = _read_record(incident_id)
        if rec is None:
            logger.warning(f"update_remediation: {incident_id}.json not found — skipping")
            return
        rec["remediation"] = {**outcome,
                              "resolved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        _report_path(incident_id).write_text(json.dumps(rec, indent=2, default=str), encoding="utf-8")

    await asyncio.to_thread(_sync)
    logger.info(f"Remediation outcome written: {incident_id} success={outcome.get('success')}")
