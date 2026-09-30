"""first_seen_at tabanlı ihale raporu (EKAP resmi ilan tarihi değildir)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

ISTANBUL = ZoneInfo("Europe/Istanbul")

MAIL_COLUMNS = (
    "İKN",
    "İhale Adı",
    "İdare",
    "İl",
    "İhale Tarihi",
    "Tür",
    "EKAP Linki",
)

FIRST_SEEN_DISCLAIMER = (
    "Not: 'İlk tespit tarihi' EKAP'ın resmi ilan/yayın tarihi değildir. "
    "Botun hedef OKAS sorgusunda ihaleyi SQLite'a ilk kaydettiği andır."
)

THIS_WEEK_DISCLAIMER = (
    "'Bu hafta' bölümü Pazartesi 00:00 (Europe/Istanbul) ile şu an arasındaki "
    "ilk tespitlerin haftalık özetidir; bugün ve dün tespit edilen ihaleler "
    "de bu özete dahildir."
)


def istanbul_now(now: Optional[datetime] = None) -> datetime:
    if now is None:
        return datetime.now(ISTANBUL)
    if now.tzinfo is None:
        return now.replace(tzinfo=ISTANBUL)
    return now.astimezone(ISTANBUL)


def to_iso(now: datetime) -> str:
    return istanbul_now(now).isoformat(timespec="seconds")


def parse_seen_at(value: str) -> datetime:
    raw = (value or "").strip()
    if not raw:
        raise ValueError("boş first_seen_at")
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return istanbul_now(dt)


def day_start(value: datetime) -> datetime:
    local = istanbul_now(value)
    return datetime(local.year, local.month, local.day, tzinfo=ISTANBUL)


def monday_start(now: Optional[datetime] = None) -> datetime:
    """İçinde bulunulan haftanın Pazartesi 00:00'ı (Europe/Istanbul)."""
    local = istanbul_now(now)
    monday = local.date() - timedelta(days=local.weekday())
    return datetime(monday.year, monday.month, monday.day, tzinfo=ISTANBUL)


def today_window(now: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    local = istanbul_now(now)
    return day_start(local), local


def yesterday_window(now: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    local = istanbul_now(now)
    start_today = day_start(local)
    return start_today - timedelta(days=1), start_today


def this_week_window(now: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    local = istanbul_now(now)
    return monday_start(local), local


def in_closed_window(value: datetime, start: datetime, end: datetime) -> bool:
    ts = istanbul_now(value)
    return start <= ts <= end


def in_half_open_window(value: datetime, start: datetime, end: datetime) -> bool:
    ts = istanbul_now(value)
    return start <= ts < end


def classify_first_seen(
    first_seen_at: datetime,
    now: Optional[datetime] = None,
) -> Dict[str, bool]:
    local_now = istanbul_now(now)
    today_start, today_end = today_window(local_now)
    yesterday_start, yesterday_end = yesterday_window(local_now)
    week_start, week_end = this_week_window(local_now)
    ts = istanbul_now(first_seen_at)
    return {
        "today": in_closed_window(ts, today_start, today_end),
        "yesterday": in_half_open_window(ts, yesterday_start, yesterday_end),
        "this_week": in_closed_window(ts, week_start, week_end),
    }


def should_send_mail(
    meta: Optional[Dict[str, Any]] = None,
    *,
    baseline_just_completed: Optional[bool] = None,
    mail_enabled: bool = True,
) -> bool:
    if not mail_enabled:
        return False
    if baseline_just_completed is True:
        return False
    if isinstance(meta, dict):
        if meta.get("baseline") is True:
            return False
        if meta.get("send_mail") is False:
            return False
    return True


def empty_meta(*, source: str = "EKAP", **extra: Any) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "yeni_bugun": [],
        "yeni_dun": [],
        "yeni_bu_hafta": [],
        "kaynak": source,
        "baseline": False,
        "send_mail": True,
        "tracking": "first_seen",
    }
    meta.update(extra)
    return meta


def mail_cell(row: Dict[str, str], key: str) -> str:
    if key == "İhale Adı":
        return (row.get("İhale Adı") or row.get("İşin Adı") or "-").strip() or "-"
    if key == "İdare":
        return (
            row.get("İdare")
            or row.get("Kurum")
            or row.get("İhaleyi Veren Kurum")
            or "-"
        ).strip() or "-"
    if key == "EKAP Linki":
        return (row.get("EKAP Linki") or row.get("Link") or "").strip()
    value = (row.get(key) or "").strip()
    return value or "-"


def format_mail_line(row: Dict[str, str]) -> str:
    return " | ".join(mail_cell(row, key) for key in MAIL_COLUMNS)


def build_subject(
    yeni_bugun: Sequence[Dict[str, str]],
    yeni_dun: Sequence[Dict[str, str]],
) -> str:
    return (
        f"Bugün {len(yeni_bugun)} ihale, dün {len(yeni_dun)} ihale "
        "ilk kez tespit edildi"
    )


@dataclass
class DetectionBuckets:
    today: List[Dict[str, str]] = field(default_factory=list)
    yesterday: List[Dict[str, str]] = field(default_factory=list)
    this_week: List[Dict[str, str]] = field(default_factory=list)

    def as_meta(self, *, source: str = "EKAP", **extra: Any) -> Dict[str, Any]:
        meta = empty_meta(source=source, **extra)
        meta["yeni_bugun"] = list(self.today)
        meta["yeni_dun"] = list(self.yesterday)
        meta["yeni_bu_hafta"] = list(self.this_week)
        return meta


def bucket_rows(
    rows: Iterable[Dict[str, Any]],
    now: Optional[datetime] = None,
) -> DetectionBuckets:
    """first_seen_at taşıyan satırları bugün/dün/hafta kovalarına ayırır."""
    local_now = istanbul_now(now)
    buckets = DetectionBuckets()
    for row in rows:
        raw = row.get("first_seen_at") or row.get("İlk Tespit ISO") or ""
        try:
            seen = parse_seen_at(str(raw))
        except ValueError:
            continue
        flags = classify_first_seen(seen, local_now)
        mail_row = dict(row)
        if flags["today"]:
            buckets.today.append(mail_row)
        if flags["yesterday"]:
            buckets.yesterday.append(mail_row)
        if flags["this_week"]:
            buckets.this_week.append(mail_row)
    return buckets
