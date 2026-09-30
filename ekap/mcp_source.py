"""EKAP listesini vendor/ihale-mcp istemcisi üzerinden çeker.

Tarayıcı yok. Aynı istemci Cursor'daki ihale-mcp `search_tenders` aracının
kullandığı `EKAPClient.search_tenders` çağrısıdır.
"""

from __future__ import annotations

import logging
import sys
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from ekap.config import ROOT, EkapConfig
from ekap.models import Tender
from ekap.parser import parse_search_json
from ekap.retry import with_retry

log = logging.getLogger("ekap.mcp_source")

VENDOR_DIR = ROOT / "vendor" / "ihale-mcp"


class EkapMcpError(RuntimeError):
    """ihale-mcp istemcisi arama döndüremedi."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


def announcement_window(
    lookback_days: int, *, today: Optional[date] = None
) -> Tuple[Optional[str], Optional[str]]:
    """Yayım tarihi penceresi, MCP'nin beklediği YYYY-MM-DD."""
    if lookback_days <= 0:
        return None, None
    ref = today or date.today()
    start = ref - timedelta(days=lookback_days)
    return start.isoformat(), ref.isoformat()


def formatted_to_item(tender: Dict[str, Any]) -> Dict[str, Any]:
    """MCP'nin okunaklı ihale sözlüğünü EKAP özet alan adlarına çevirir."""
    type_info = tender.get("type") if isinstance(tender.get("type"), dict) else {}
    status_info = tender.get("status") if isinstance(tender.get("status"), dict) else {}
    return {
        "id": tender.get("id"),
        "ihaleAdi": tender.get("name"),
        "ikn": tender.get("ikn"),
        "ihaleTip": type_info.get("code"),
        "ihaleTipAciklama": type_info.get("description"),
        "ihaleUsulAciklama": tender.get("method"),
        "ihaleDurum": status_info.get("code"),
        "ihaleDurumAciklama": status_info.get("description"),
        "idareAdi": tender.get("authority"),
        "ihaleIlAdi": tender.get("province"),
        "ihaleTarihSaat": tender.get("tender_datetime"),
        "ilanVarMi": tender.get("has_announcement"),
        "dokumanSayisi": tender.get("document_count") or 0,
    }


def tenders_from_mcp_result(result: Dict[str, Any]) -> Tuple[List[Tender], int]:
    if not isinstance(result, dict):
        raise EkapMcpError(f"MCP yanıtı sözlük değil: {type(result).__name__}")
    if result.get("error"):
        message = str(result.get("message") or result.get("error"))
        status = str(result.get("error"))
        retryable = not any(code in status for code in ("401", "403", "428"))
        raise EkapMcpError(message, retryable=retryable)
    raw_items = []
    for piece in result.get("tenders") or []:
        if isinstance(piece, dict):
            raw_items.append(formatted_to_item(piece))
    payload = {"list": raw_items, "totalCount": result.get("total_count") or 0}
    return parse_search_json(payload)


def paginate_mcp(
    fetch_page: Callable[[int, int], Dict[str, Any]],
    *,
    take: int,
    max_pages: int,
) -> Tuple[List[Tender], int, List[int]]:
    """skip += take. fetch_page(skip, take) MCP arama sözlüğü döner."""
    skip = 0
    page_no = 0
    collected: List[Tender] = []
    total = 0
    skips: List[int] = []
    while page_no < max_pages:
        skips.append(skip)
        page_items, total = tenders_from_mcp_result(fetch_page(skip, take))
        collected.extend(page_items)
        page_no += 1
        skip += take
        if skip >= total or not page_items:
            break
    return collected, total, skips


def _ensure_vendor_path() -> None:
    if not (VENDOR_DIR / "ihale_client.py").is_file():
        raise EkapMcpError(
            f"ihale-mcp bulunamadı ({VENDOR_DIR}). "
            "vendor/ihale-mcp klonu yerinde olmalı.",
            retryable=False,
        )
    vendor = str(VENDOR_DIR)
    if vendor not in sys.path:
        sys.path.insert(0, vendor)


def _load_client():
    _ensure_vendor_path()
    try:
        import markitdown  # noqa: F401
    except ImportError:
        import types

        stub = types.ModuleType("markitdown")

        class _MarkItDown:
            def convert(self, *_args, **_kwargs):
                raise EkapMcpError("markitdown kurulu değil", retryable=False)

        stub.MarkItDown = _MarkItDown
        sys.modules["markitdown"] = stub
    from ihale_client import EKAPClient

    client = EKAPClient()

    async def _skip_document(_tender_id):
        return {"success": False}

    # Özet mail İKN linkini kullanır; her kayıt için belge URL'si istenmez.
    client.get_tender_document_url = _skip_document
    return client


async def collect_tenders(config: EkapConfig) -> Tuple[List[Tender], Dict[str, Any]]:
    client = _load_client()
    codes = list(config.search_okas_roots or config.target_okas)
    names: List[str] = []
    if codes:
        try:
            names = await client.resolve_okas_names(codes)
        except Exception as exc:
            log.warning("OKAS adları çözülemedi, kodlarla devam: %s", exc)
            names = []
    if names and not any(names):
        names = []

    start, end = announcement_window(config.lookback_days)
    take = max(1, min(int(config.page_size), 100))
    log.info(
        "MCP arama: okas=%s yayım=%s..%s sayfa=%s",
        len(codes),
        start or "-",
        end or "-",
        take,
    )
    if config.network_debug:
        log.info("MCP OKAS kodları: %s", ",".join(codes))

    async def fetch(skip: int, limit: int) -> Dict[str, Any]:
        async def once():
            return await client.search_tenders(
                okas_codes=codes or None,
                okas_names=names or None,
                announcement_date_start=start,
                announcement_date_end=end,
                order_by="ihaleTarihi",
                sort_order="desc",
                skip=skip,
                limit=limit,
            )

        return await with_retry(
            once,
            attempts=config.retry_attempts,
            label=f"MCP arama skip={skip}",
        )

    collected: List[Tender] = []
    total = 0
    skips: List[int] = []
    skip = 0
    for _page in range(max(1, config.max_pages)):
        skips.append(skip)
        page_items, total = tenders_from_mcp_result(await fetch(skip, take))
        collected.extend(page_items)
        log.info("MCP sayfa skip=%s kayıt=%s toplam=%s", skip, len(page_items), total)
        skip += take
        if skip >= total or not page_items:
            break

    extra = {
        "mcp": {
            "total_count": total,
            "pages": skips,
            "announcement_start": start,
            "announcement_end": end,
            "okas_count": len(codes),
        }
    }
    return collected, extra
