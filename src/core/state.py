# src/core/state.py
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..db.db import get_conn

logger = logging.getLogger(__name__)


STATE_KEY_LAST_PUBDATE = "serie1_pdf_last_pubdate_utc"
STATE_KEY_CURSOR = "serie1_pdf_cursor_v2"


# -----------------------------
# State (checkpoint/cursor)
# -----------------------------
def get_state(key: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM collector_state WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_state(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO collector_state(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, value),
        )
        conn.commit()


def delete_state(key: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM collector_state WHERE key=?", (key,))
        conn.commit()


def reset_checkpoint() -> None:
    delete_state(STATE_KEY_LAST_PUBDATE)


def _parse_last_pub_dt() -> datetime | None:
    last_pub_str = get_state(STATE_KEY_LAST_PUBDATE)
    if not last_pub_str:
        return None
    try:
        return datetime.fromisoformat(last_pub_str.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception as e:
        logger.debug("⏱️ Checkpoint inválido '%s': %s", last_pub_str, e)
        return None


def _parse_cursor() -> tuple[datetime | None, set[str]]:
    """
    Cursor v2:
      {"last_dt":"2026-01-06T00:00:00Z","last_dt_links":[...]}
    """
    raw = get_state(STATE_KEY_CURSOR)
    if not raw:
        return None, set()

    try:
        obj = json.loads(raw)
        last_dt_s = (obj.get("last_dt") or "").strip()
        links = obj.get("last_dt_links") or []
        if not last_dt_s:
            return None, set()

        last_dt = datetime.fromisoformat(last_dt_s.replace("Z", "+00:00")).astimezone(timezone.utc)
        return last_dt, set(str(x).strip() for x in links if str(x).strip())
    except Exception as e:
        logger.debug("⏱️ Cursor inválido (%s): %s", raw[:120], e)
        return None, set()


def _save_cursor(last_dt: datetime, last_dt_links: set[str]) -> None:
    capped = list(sorted(last_dt_links))[:500]  # evita crescimento infinito
    obj = {
        "last_dt": last_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "last_dt_links": capped,
    }
    set_state(STATE_KEY_CURSOR, json.dumps(obj, ensure_ascii=False))


__all__ = [
    "STATE_KEY_LAST_PUBDATE",
    "STATE_KEY_CURSOR",
    "get_state",
    "set_state",
    "delete_state",
    "reset_checkpoint",
    "_parse_last_pub_dt",
    "_parse_cursor",
    "_save_cursor",
]
