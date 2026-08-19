"""CSV export that streams, so "everything stored" is a safe thing to ask for.

The exports used to read a fixed 5,000 rows into memory, build the whole file
as one string, and send it. That is fine for a page of results and wrong for a
month of them: the cap silently truncated the answer, and lifting the cap
without changing the shape would just move the problem to memory.

So rows are read a page at a time and written out as they go. The response
starts before the last row has been read, nothing larger than one page is ever
held, and the caller gets the whole retention window rather than the first
five thousand rows of it.
"""

from __future__ import annotations

import csv
import io
from typing import AsyncIterator, Callable, Iterable

from fastapi.responses import StreamingResponse

# Rows per database round trip, and per flush to the client.
PAGE = 1000

# A ceiling that exists to stop a runaway, not to trim an answer. Retention is
# what bounds these collections; a file this long means something else is
# wrong, and the export should stop rather than run the box out of memory.
HARD_CAP = 500_000


async def paged(collection, flt: dict, sort_key: str = "ts",
                direction: int = -1, cap: int = HARD_CAP) -> AsyncIterator[dict]:
    """Walk a collection newest-first, a page at a time.

    skip/limit rather than an async cursor because both database backends here
    support it — the in-memory one used by the tests has no async iteration.
    """
    seen = 0
    while seen < cap:
        page = await (collection.find(flt)
                      .sort(sort_key, direction)
                      .skip(seen)
                      .limit(PAGE)
                      .to_list(PAGE))
        if not page:
            return
        for doc in page:
            yield doc
            seen += 1
            if seen >= cap:
                return
        if len(page) < PAGE:
            return


def csv_response(rows: AsyncIterator[dict], columns: list[str],
                 filename: str, row: Callable[[dict], dict] | None = None,
                 ) -> StreamingResponse:
    """Stream `rows` as a CSV download.

    Excel decides a file's encoding from a BOM, and these carry token names
    with non-ASCII characters, so the header is written as utf-8-sig.
    """
    async def body() -> AsyncIterator[bytes]:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        yield buf.getvalue().encode("utf-8-sig")
        buf.seek(0)
        buf.truncate(0)

        n = 0
        async for doc in rows:
            writer.writerow(row(doc) if row else doc)
            n += 1
            if n % PAGE == 0:
                yield buf.getvalue().encode("utf-8")
                buf.seek(0)
                buf.truncate(0)
        rest = buf.getvalue()
        if rest:
            yield rest.encode("utf-8")

    return StreamingResponse(
        body(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def from_list(items: Iterable[dict]) -> AsyncIterator[dict]:
    """Adapt an already-materialised list to the same interface.

    For the one export that cannot be a single query — the detections history
    lives in a day-per-document archive as well as the live collection — where
    the merge has to happen in memory anyway.
    """
    for it in items:
        yield it
