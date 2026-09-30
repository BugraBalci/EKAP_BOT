"""OKAS ve tarih süzgeçleri. Tarayıcıya bağlı değildir."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Iterable, List, Optional, Sequence, Tuple

from ekap.models import Tender

log = logging.getLogger("ekap.filters")


def normalize_okas(code: str) -> str:
    return "".join(ch for ch in (code or "") if ch.isdigit())


def okas_matches(code: str, targets: Sequence[str]) -> bool:
    """Kod hedef listede veya bir hedefin alt/üst öneki ise True.

    72200000 hedefi 72212300 alt kodunu tutar; 72000000 kök araması 72200000
    hedefine denk gelir.
    """
    needle = normalize_okas(code)
    if not needle:
        return False
    for raw in targets:
        target = normalize_okas(raw)
        if not target:
            continue
        if needle == target:
            return True
        # alt kod: hedef önek
        if needle.startswith(target.rstrip("0") or target):
            return True
        # üst kod geldiyse hedef onun altında mı
        prefix = needle.rstrip("0") or needle
        if target.startswith(prefix):
            return True
    return False


def any_okas_matches(codes: Iterable[str], targets: Sequence[str]) -> bool:
    return any(okas_matches(code, targets) for code in codes)


def filter_by_okas(
    tenders: Sequence[Tender], targets: Sequence[str]
) -> Tuple[List[Tender], int]:
    """OKAS alanı olan kayıtları hedeflere göre süz. Alan yoksa bırakır."""
    if not targets:
        return list(tenders), 0
    kept: List[Tender] = []
    dropped = 0
    missing = 0
    for tender in tenders:
        if not tender.okas_codes:
            kept.append(tender)
            missing += 1
            continue
        if any_okas_matches(tender.okas_codes, targets):
            kept.append(tender)
        else:
            dropped += 1
    if missing:
        log.info(
            "OKAS alanı olmayan %s kayıt bırakıldı (özet yanıtta OKAS yok; istek okasBransKodList'e güvenilir)",
            missing,
        )
    return kept, dropped


def _relevant_date(tender: Tender) -> Optional[date]:
    """Yayım tarihi tercih edilir; yoksa ihale tarihi kullanılmaz (gelecek ihale)."""
    if tender.publish_date:
        return tender.publish_date
    return None


def filter_by_lookback(
    tenders: Sequence[Tender],
    lookback_days: int,
    *,
    today: Optional[date] = None,
) -> Tuple[List[Tender], int]:
    """Yayım tarihi lookback dışındaysa ezer.

    Yayım tarihi yoksa kayıt tutulur — ihale tarihi ileride olabilir.
    lookback_days <= 0 ise süzgeç uygulanmaz.
    """
    if lookback_days <= 0:
        return list(tenders), 0
    ref = today or date.today()
    start = ref - timedelta(days=lookback_days)
    kept: List[Tender] = []
    dropped = 0
    for tender in tenders:
        published = _relevant_date(tender)
        if published is None:
            kept.append(tender)
            continue
        if start <= published <= ref:
            kept.append(tender)
        else:
            dropped += 1
    return kept, dropped


def excluded_by_keywords(tender: Tender, words: Sequence[str]) -> bool:
    if not words:
        return False
    blob = f"{tender.title} {tender.institution} {tender.description}".lower()
    return any(word in blob for word in words)


def parse_exclude_words(raw: str) -> List[str]:
    out = []
    for part in (raw or "").split(","):
        word = part.strip().lower()
        if word:
            out.append(word)
    return out
