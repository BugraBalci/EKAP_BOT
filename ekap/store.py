"""SQLite first-seen takibi. EKAP resmi ilan tarihi tutulmaz."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ekap.models import Tender
from ekap.report import (
    DetectionBuckets,
    empty_meta,
    istanbul_now,
    parse_seen_at,
    this_week_window,
    to_iso,
    today_window,
    yesterday_window,
)

log = logging.getLogger("ekap.store")

BASELINE_KEY = "baseline_completed_at"


@dataclass
class ScanPersistResult:
    baseline_just_completed: bool
    inserted: List[Tender] = field(default_factory=list)
    updated: List[Tender] = field(default_factory=list)

    @property
    def inserted_keys(self) -> List[str]:
        return [t.unique_key() for t in self.inserted]


class TenderStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure(self) -> None:
        with self._connect() as conn:
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scan_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='seen_tenders'"
        ).fetchone()
        if not exists:
            self._create_seen_tenders(conn)
            return

        cols = {row[1] for row in conn.execute("PRAGMA table_info(seen_tenders)")}
        if "tender_key" in cols and "first_seen_at" in cols:
            self._ensure_extra_columns(conn, cols)
            return

        conn.execute("ALTER TABLE seen_tenders RENAME TO seen_tenders_legacy")
        self._create_seen_tenders(conn)
        legacy_cols = {row[1] for row in conn.execute("PRAGMA table_info(seen_tenders_legacy)")}
        first_col = "first_seen_at" if "first_seen_at" in legacy_cols else "first_seen"
        last_col = "last_seen_at" if "last_seen_at" in legacy_cols else "last_seen"
        conn.execute(
            f"""
            INSERT OR IGNORE INTO seen_tenders (
                tender_key, ikn, ekap_id, title, institution,
                tender_datetime, province, tender_type, url,
                first_seen_at, last_seen_at, is_baseline
            )
            SELECT
                ekap_id,
                CASE
                    WHEN fingerprint LIKE 'ikn:%' THEN substr(fingerprint, 5)
                    ELSE ''
                END,
                ekap_id,
                COALESCE(title, ''),
                '',
                '',
                '',
                '',
                '',
                {first_col},
                {last_col},
                1
            FROM seen_tenders_legacy
            """
        )
        conn.execute("DROP TABLE seen_tenders_legacy")
        count = conn.execute("SELECT COUNT(*) FROM seen_tenders").fetchone()[0]
        if count:
            self._set_state(conn, BASELINE_KEY, to_iso(istanbul_now()))
            log.info("Eski seen_tenders şeması taşındı; %s kayıt baseline işaretlendi", count)

    def _create_seen_tenders(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_tenders (
                tender_key TEXT PRIMARY KEY,
                ikn TEXT,
                ekap_id TEXT,
                title TEXT,
                institution TEXT,
                tender_datetime TEXT,
                province TEXT,
                tender_type TEXT,
                url TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                is_baseline INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_seen_first_seen_at ON seen_tenders(first_seen_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_seen_ekap_id ON seen_tenders(ekap_id)"
        )

    def _ensure_extra_columns(self, conn: sqlite3.Connection, cols: set[str]) -> None:
        wanted = {
            "ikn": "TEXT",
            "ekap_id": "TEXT",
            "title": "TEXT",
            "institution": "TEXT",
            "tender_datetime": "TEXT",
            "province": "TEXT",
            "tender_type": "TEXT",
            "url": "TEXT",
            "is_baseline": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, decl in wanted.items():
            if name not in cols:
                conn.execute(f"ALTER TABLE seen_tenders ADD COLUMN {name} {decl}")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_seen_first_seen_at ON seen_tenders(first_seen_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_seen_ekap_id ON seen_tenders(ekap_id)"
        )

    def _get_state(self, conn: sqlite3.Connection, key: str) -> Optional[str]:
        row = conn.execute(
            "SELECT value FROM scan_state WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def _set_state(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            "INSERT INTO scan_state(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def is_baseline_complete(self) -> bool:
        with self._connect() as conn:
            return bool(self._get_state(conn, BASELINE_KEY))

    def _find_existing(
        self, conn: sqlite3.Connection, tender: Tender
    ) -> Optional[sqlite3.Row]:
        key = tender.unique_key()
        row = conn.execute(
            "SELECT * FROM seen_tenders WHERE tender_key = ?", (key,)
        ).fetchone()
        if row:
            return row
        ekap_id = (tender.ekap_id or "").strip()
        if ekap_id and ekap_id != key:
            return conn.execute(
                "SELECT * FROM seen_tenders WHERE ekap_id = ? OR tender_key = ?",
                (ekap_id, ekap_id),
            ).fetchone()
        return None

    def split_new(self, tenders: Sequence[Tender]) -> Tuple[List[Tender], List[Tender]]:
        new_rows: List[Tender] = []
        dupes: List[Tender] = []
        with self._connect() as conn:
            for tender in tenders:
                if self._find_existing(conn, tender):
                    dupes.append(tender)
                else:
                    new_rows.append(tender)
        return new_rows, dupes

    def record_scan(
        self,
        tenders: Sequence[Tender],
        *,
        now: Optional[datetime] = None,
    ) -> ScanPersistResult:
        """Başarılı taramadaki ihaleleri işler.

        Yeni kayıt: insert + first_seen_at = now.
        Var olan: first_seen_at değişmez, last_seen_at = now.
        İlk başarılı tarama baseline/seed olur; o kayıtlar rapora girmez.
        """
        stamp = to_iso(istanbul_now(now))
        was_complete = self.is_baseline_complete()
        seed_run = not was_complete
        inserted: List[Tender] = []
        updated: List[Tender] = []
        with self._connect() as conn:
            for tender in tenders:
                existing = self._find_existing(conn, tender)
                if existing:
                    conn.execute(
                        "UPDATE seen_tenders SET last_seen_at = ? WHERE tender_key = ?",
                        (stamp, existing["tender_key"]),
                    )
                    updated.append(tender)
                    continue
                conn.execute(
                    """
                    INSERT INTO seen_tenders (
                        tender_key, ikn, ekap_id, title, institution,
                        tender_datetime, province, tender_type, url,
                        first_seen_at, last_seen_at, is_baseline
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tender.unique_key(),
                        (tender.ikn or "").strip(),
                        (tender.ekap_id or "").strip(),
                        tender.title,
                        tender.institution,
                        tender.tender_datetime,
                        tender.province,
                        tender.tender_type,
                        tender.url,
                        stamp,
                        stamp,
                        1 if seed_run else 0,
                    ),
                )
                inserted.append(tender)
            if seed_run:
                self._set_state(conn, BASELINE_KEY, stamp)
        log.info(
            "seen_tenders: insert=%s update=%s baseline=%s (%s)",
            len(inserted),
            len(updated),
            seed_run,
            self.path,
        )
        return ScanPersistResult(
            baseline_just_completed=seed_run,
            inserted=inserted,
            updated=updated,
        )

    def mark_seen(
        self,
        tenders: Iterable[Tender],
        *,
        now: Optional[datetime] = None,
    ) -> ScanPersistResult:
        return self.record_scan(list(tenders), now=now)

    def first_seen_at(self, tender: Tender) -> Optional[str]:
        row = self.get(tender)
        return row["first_seen_at"] if row else None

    def get(self, tender: Tender) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = self._find_existing(conn, tender)
            return dict(row) if row else None

    def _row_to_mail(self, row: sqlite3.Row) -> Dict[str, str]:
        ikn = (row["ikn"] or "").strip() or "-"
        title = (row["title"] or "").strip() or "-"
        institution = (row["institution"] or "").strip() or "-"
        url = (row["url"] or "").strip()
        return {
            "İKN": ikn,
            "İşin Adı": title,
            "İhale Adı": title,
            "Kurum": institution,
            "İdare": institution,
            "İl": (row["province"] or "").strip() or "-",
            "İhale Tarihi": (row["tender_datetime"] or "").strip() or "-",
            "Tür": (row["tender_type"] or "").strip() or "-",
            "Link": url,
            "EKAP Linki": url,
            "Kaynak": "EKAP",
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
        }

    def query_first_seen(
        self,
        start: datetime,
        end: datetime,
        *,
        end_inclusive: bool = True,
        exclude_baseline: bool = True,
    ) -> List[Dict[str, str]]:
        sql = "SELECT * FROM seen_tenders"
        params: List[Any] = []
        if exclude_baseline:
            sql += " WHERE is_baseline = 0"
        sql += " ORDER BY first_seen_at ASC, tender_key ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        start_local = istanbul_now(start)
        end_local = istanbul_now(end)
        out: List[Dict[str, str]] = []
        for row in rows:
            try:
                ts = parse_seen_at(row["first_seen_at"])
            except ValueError:
                continue
            if end_inclusive:
                keep = start_local <= ts <= end_local
            else:
                keep = start_local <= ts < end_local
            if keep:
                out.append(self._row_to_mail(row))
        return out

    def report_windows(
        self,
        *,
        now: Optional[datetime] = None,
        source: str = "EKAP",
        baseline_just_completed: bool = False,
    ) -> Dict[str, Any]:
        local_now = istanbul_now(now)
        today_start, today_end = today_window(local_now)
        yesterday_start, yesterday_end = yesterday_window(local_now)
        week_start, week_end = this_week_window(local_now)
        buckets = DetectionBuckets(
            today=self.query_first_seen(today_start, today_end, end_inclusive=True),
            yesterday=self.query_first_seen(
                yesterday_start, yesterday_end, end_inclusive=False
            ),
            this_week=self.query_first_seen(week_start, week_end, end_inclusive=True),
        )
        meta = buckets.as_meta(source=source)
        meta["baseline"] = baseline_just_completed
        meta["send_mail"] = not baseline_just_completed
        meta["now"] = to_iso(local_now)
        meta["week_start"] = to_iso(week_start)
        return meta
