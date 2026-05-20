"""Atomic write-once upsert for API-fetched data.

Immutable fields are set only on first write (if currently null/missing).
Mutable fields are always overwritten. Implemented as a Painless update_with_upsert
so concurrent writers can't race.

Used by enrichers (NVD, KEV) so API-paid data is never silently overwritten.
"""
from typing import Optional


_SCRIPT_SOURCE = """
    for (entry in params.immutable.entrySet()) {
        if (!ctx._source.containsKey(entry.getKey()) || ctx._source[entry.getKey()] == null) {
            ctx._source[entry.getKey()] = entry.getValue();
        }
    }
    for (entry in params.mutable.entrySet()) {
        ctx._source[entry.getKey()] = entry.getValue();
    }
"""


async def upsert_immutable(
    *,
    client,
    index: str,
    doc_id: str,
    immutable_fields: dict,
    mutable_fields: Optional[dict] = None,
) -> None:
    """Upsert a doc: write immutable fields only if currently null.

    Args:
      client: AsyncOpenSearch instance
      index: target index name
      doc_id: document id
      immutable_fields: fields that are written once and never updated
      mutable_fields: fields that are always written (e.g. timestamps)
    """
    mutable_fields = mutable_fields or {}
    if not immutable_fields and not mutable_fields:
        return

    await client.update(
        index=index,
        id=doc_id,
        body={
            "script": {
                "source": _SCRIPT_SOURCE,
                "lang": "painless",
                "params": {
                    "immutable": immutable_fields,
                    "mutable": mutable_fields,
                },
            },
            "upsert": {**immutable_fields, **mutable_fields},
        },
        retry_on_conflict=3,
    )
