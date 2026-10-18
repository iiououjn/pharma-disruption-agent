"""Typed change events between two snapshots.

Text fields are compared after normalisation (case / whitespace) so cosmetic edits do not fire events.
"""
from __future__ import annotations

from .db import ShortageRecord
from .normalize import norm_text

NEW_LISTING = "NEW_LISTING"
REMOVED = "REMOVED"
RESOLVED = "RESOLVED"
STATUS_CHANGE = "STATUS_CHANGE"
AVAILABILITY_TEXT_CHANGED = "AVAILABILITY_TEXT_CHANGED"
REASON_CHANGED = "REASON_CHANGED"


def diff_snapshots(old: list[ShortageRecord], new: list[ShortageRecord]) -> list[dict]:
    o = {r.record_key: r for r in old}
    n = {r.record_key: r for r in new}
    events: list[dict] = []

    def ev(kind, key, rec, **detail):
        events.append({"event": kind, "record_key": key, "generic_name": rec.generic_name,
                       "company_name": rec.company_name, **detail})

    for key in sorted(n.keys() - o.keys()):
        ev(NEW_LISTING, key, n[key], new_status=n[key].status)
    for key in sorted(o.keys() - n.keys()):
        ev(REMOVED, key, o[key], old_status=o[key].status)
    for key in sorted(n.keys() & o.keys()):
        a, b = o[key], n[key]
        if norm_text(a.status) != norm_text(b.status):
            kind = RESOLVED if norm_text(b.status) == "resolved" else STATUS_CHANGE
            ev(kind, key, b, old_status=a.status, new_status=b.status)
        if norm_text(a.availability) != norm_text(b.availability):
            ev(AVAILABILITY_TEXT_CHANGED, key, b, old=a.availability, new=b.availability)
        if norm_text(a.shortage_reason) != norm_text(b.shortage_reason):
            ev(REASON_CHANGED, key, b, old=a.shortage_reason, new=b.shortage_reason)
    return events
