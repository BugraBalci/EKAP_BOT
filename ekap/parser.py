"""JSON → Tender dönüşümü. Tarayıcıya bağlı değildir."""

from __future__ import annotations

import logging
from typing import Any, List, Tuple

from ekap.models import (
    Tender,
    extract_item_list,
    extract_total_count,
    tender_from_item,
)

log = logging.getLogger("ekap.parser")


def parse_search_json(payload: Any, *, source: str = "EKAP") -> Tuple[List[Tender], int]:
    """Arama JSON'unu Tender listesine çevirir. Biçim değişirse boş liste döner."""
    try:
        items = extract_item_list(payload)
        total = extract_total_count(payload, items)
    except Exception as exc:
        log.error("JSON arama gövdesi çözümlenemedi: %s", exc)
        return [], 0

    tenders: List[Tender] = []
    skipped = 0
    for item in items:
        try:
            tender = tender_from_item(item, source=source)
        except Exception as exc:
            skipped += 1
            log.debug("Kayıt eşlenemedi: %s", exc)
            continue
        if tender is None:
            skipped += 1
            continue
        tenders.append(tender)
    if skipped:
        log.warning("JSON içinden %s kayıt atlandı (alan/biçim uyumsuz)", skipped)
    return tenders, total
