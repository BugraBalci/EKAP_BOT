"""EKAP listesini güncel ihale-mcp sunucusundan çeker.

EKAP v2, AES imza başlıklarını (X-Ekap-Sec / X-Custom-Request-*) bıraktı.
Arama artık `ekap.human-verification` çerezi istiyor; bu çerezi Turnstile
ile üreten taraf ihale-mcp sunucusudur. Bot yalnızca MCP `search_tenders`
aracını çağırır.

Varsayılan adres README'deki güncel uzak sunucudur:
https://ihale.surucu.dev/mcp
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from ekap.config import EkapConfig
from ekap.models import Tender
from ekap.parser import parse_search_json
from ekap.retry import with_retry

log = logging.getLogger("ekap.mcp_source")

_NON_RETRY_MARKERS = (
    "401",
    "403",
    "406",
    "428",
    "turnstile",
    "human_verification",
    "not acceptable",
)


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


def error_is_retryable(error: str, message: str = "") -> bool:
    blob = f"{error} {message}".lower()
    return not any(marker in blob for marker in _NON_RETRY_MARKERS)


def tenders_from_mcp_result(result: Dict[str, Any]) -> Tuple[List[Tender], int]:
    if not isinstance(result, dict):
        raise EkapMcpError(f"MCP yanıtı sözlük değil: {type(result).__name__}")
    if result.get("error"):
        message = str(result.get("message") or result.get("error"))
        status = str(result.get("error"))
        raise EkapMcpError(message, retryable=error_is_retryable(status, message))
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


def parse_mcp_message(raw: str) -> Dict[str, Any]:
    """JSON-RPC gövdesi ya da SSE (`event: message` / ping yorumları) okur."""
    text = (raw or "").strip()
    if not text:
        raise EkapMcpError("MCP yanıtı boş", retryable=True)
    if text.startswith("{"):
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise EkapMcpError("MCP yanıtı sözlük değil", retryable=False)
        return parsed

    messages: List[Dict[str, Any]] = []
    for block in text.split("\n\n"):
        data_lines = []
        for line in block.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if not data_lines:
            continue
        parsed = json.loads("\n".join(data_lines))
        if isinstance(parsed, dict):
            messages.append(parsed)
    if not messages:
        raise EkapMcpError("MCP SSE yanıtında veri yok", retryable=True)
    return messages[-1]


def tool_result_payload(rpc: Dict[str, Any]) -> Dict[str, Any]:
    """tools/call JSON-RPC sonucundan ihale sözlüğünü çıkarır."""
    if rpc.get("error"):
        err = rpc["error"]
        message = err.get("message") if isinstance(err, dict) else str(err)
        code = str(err.get("code") if isinstance(err, dict) else err)
        raise EkapMcpError(str(message), retryable=error_is_retryable(code, str(message)))
    result = rpc.get("result")
    if not isinstance(result, dict):
        raise EkapMcpError("MCP araç sonucu yok", retryable=True)
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    for item in result.get("content") or []:
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = item.get("text") or ""
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise EkapMcpError("MCP araç yanıtı okunamadı", retryable=False)


class RemoteMcpClient:
    """Streamable HTTP MCP istemcisi. Oturum başlığı varsa sonraki çağrıda taşınır."""

    def __init__(self, url: str, *, timeout: float = 180.0) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout
        self._http = None
        self._session_id: Optional[str] = None
        self._seq = 0
        self._ready = False

    async def __aenter__(self) -> "RemoteMcpClient":
        import httpx

        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=30.0),
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *_exc) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _post(self, payload: Dict[str, Any], *, notify: bool = False) -> Dict[str, Any]:
        if self._http is None:
            raise EkapMcpError("MCP istemcisi açık değil", retryable=False)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        response = await self._http.post(self.url, json=payload, headers=headers)
        session = response.headers.get("mcp-session-id")
        if session:
            self._session_id = session
        if response.status_code >= 400:
            snippet = (response.text or "")[:300]
            raise EkapMcpError(
                f"MCP HTTP {response.status_code}: {snippet}",
                retryable=response.status_code >= 500 or response.status_code == 429,
            )
        if notify or response.status_code == 202 or not (response.content or b"").strip():
            return {}
        return parse_mcp_message(response.text)

    async def ensure_session(self) -> None:
        if self._ready:
            return
        self._seq += 1
        await self._post(
            {
                "jsonrpc": "2.0",
                "id": self._seq,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "ekap-bot", "version": "1.0"},
                },
            }
        )
        await self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, notify=True)
        self._ready = True

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        await self.ensure_session()
        self._seq += 1
        rpc = await self._post(
            {
                "jsonrpc": "2.0",
                "id": self._seq,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        return tool_result_payload(rpc)


async def collect_tenders(config: EkapConfig) -> Tuple[List[Tender], Dict[str, Any]]:
    codes = list(config.search_okas_roots or config.target_okas)
    start, end = announcement_window(config.lookback_days)
    take = max(1, min(int(config.page_size), 100))
    log.info(
        "MCP arama: url=%s okas=%s yayım=%s..%s sayfa=%s",
        config.mcp_url,
        len(codes),
        start or "-",
        end or "-",
        take,
    )
    if config.network_debug:
        log.info("MCP OKAS kodları: %s", ",".join(codes))

    async with RemoteMcpClient(config.mcp_url, timeout=config.mcp_timeout) as client:

        async def fetch(skip: int, limit: int) -> Dict[str, Any]:
            arguments: Dict[str, Any] = {
                "order_by": "ihaleTarihi",
                "sort_order": "desc",
                "skip": skip,
                "limit": limit,
            }
            if codes:
                arguments["okas_codes"] = codes
            if start:
                arguments["announcement_date_start"] = start
            if end:
                arguments["announcement_date_end"] = end

            async def once():
                return await client.call_tool("search_tenders", arguments)

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
            "url": config.mcp_url,
            "total_count": total,
            "pages": skips,
            "announcement_start": start,
            "announcement_end": end,
            "okas_count": len(codes),
        }
    }
    return collected, extra
