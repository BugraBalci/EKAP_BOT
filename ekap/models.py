"""Normalize edilmiş ihale modeli.

GetListByParameters özet yanıtı doğrulanmış sözleşmeye göre okunur:
top-level `list` + `totalCount`. Özet nesnede olmayan alan uydurulmaz.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def parse_date_value(value: Any) -> Optional[date]:
    """ISO, YYYY-MM-DD veya DD.MM.YYYY (opsiyonel saat) okur."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    metin = _as_text(value)
    if not metin:
        return None
    iso = metin.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso).date()
    except ValueError:
        pass
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(metin, fmt).date()
        except ValueError:
            continue
    if len(metin) >= 10:
        for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(metin[:10], fmt).date()
            except ValueError:
                continue
    return None


def _collect_okas(item: Dict[str, Any]) -> List[str]:
    """Özet yanıtta OKAS kodu gözlemlenmedi. Alan yoksa boş liste.

    `okasBransAdiList` ile eşleme yapılmaz. Başka anahtar adı uydurulmaz.
    """
    if "okasBransKodList" not in item:
        return []
    raw = item.get("okasBransKodList")
    if not isinstance(raw, (list, tuple)):
        return []
    codes: List[str] = []
    seen = set()
    for piece in raw:
        if isinstance(piece, dict):
            continue
        text = _as_text(piece)
        digits = "".join(ch for ch in text if ch.isdigit())
        if not digits or digits in seen:
            continue
        seen.add(digits)
        codes.append(digits)
    return codes


@dataclass
class Tender:
    ekap_id: str
    title: str
    institution: str
    okas_codes: List[str] = field(default_factory=list)
    publish_date: Optional[date] = None
    tender_date: Optional[date] = None
    tender_datetime: str = ""
    url: str = ""
    description: str = ""
    ikn: str = ""
    province: str = ""
    status: str = ""
    status_code: str = ""
    tender_type: str = ""
    tender_type_code: str = ""
    procedure: str = ""
    has_announcement: Optional[bool] = None
    source: str = "EKAP"
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def okas_code(self) -> str:
        return ", ".join(self.okas_codes)

    def fingerprint(self) -> str:
        if self.ikn and self.ikn != "-":
            return f"ikn:{self.ikn}"
        if self.ekap_id:
            return f"id:{self.ekap_id}"
        return f"kurum:{self.institution}|ad:{self.title}"

    def unique_key(self) -> str:
        """Kalıcı kimlik: İKN varsa o, yoksa stabil ekap_id."""
        ikn = (self.ikn or "").strip()
        if ikn and ikn != "-":
            return ikn
        ekap_id = (self.ekap_id or "").strip()
        if ekap_id:
            return ekap_id
        return self.fingerprint()

    def to_mail_row(self) -> Dict[str, str]:
        yayim = self.publish_date.strftime("%d.%m.%Y") if self.publish_date else ""
        ihale = self.tender_datetime or (
            self.tender_date.strftime("%d.%m.%Y") if self.tender_date else ""
        )
        il_saat = " / ".join(p for p in (self.province, ihale) if p)
        return {
            "Kurum": self.institution or "-",
            "İdare": self.institution or "-",
            "İşin Adı": self.title or "-",
            "İhale Adı": self.title or "-",
            "İKN": self.ikn or "-",
            "İhale Tarihi": ihale,
            "Yayım Tarihi": yayim,
            "Tür": self.tender_type or "-",
            "İl": self.province or "-",
            "İl / Saat": il_saat or "-",
            "Durum": self.status or "Teklif Vermeye Açık",
            "Link": self.url,
            "EKAP Linki": self.url,
            "OKAS": self.okas_code,
            "Kaynak": self.source,
            "İhaleyi Veren Kurum": self.institution or "-",
            "İhale Detayları": self.description or self.title or "-",
        }


def ihale_url(ikn: str) -> str:
    ikn = (ikn or "").strip()
    if not ikn or ikn == "-":
        return ""
    return f"https://ekapv2.kik.gov.tr/ekap/search/{ikn.replace('/', '_')}"


def tender_from_item(item: Dict[str, Any], *, source: str = "EKAP") -> Optional[Tender]:
    """Doğrulanmış özet alanlarını Tender'a çevirir. Tanınmayan şekil None döner.

    `ihaleTarihSaat` ihale tarihidir; ilan/yayın tarihi bu yanıtta yoktur.
    `publish_date` bu özetten doldurulmaz.
    """
    if not isinstance(item, dict):
        return None

    ikn = _as_text(item.get("ikn"))
    ekap_id = _as_text(item.get("id"))
    title = _as_text(item.get("ihaleAdi"))
    institution = _as_text(item.get("idareAdi"))
    if not title and not ikn and not ekap_id:
        return None

    tender_dt = _as_text(item.get("ihaleTarihSaat"))
    if not ekap_id:
        ekap_id = ikn or f"{institution}|{title}|{tender_dt}"

    ilan_var = item.get("ilanVarMi")
    has_announcement = ilan_var if isinstance(ilan_var, bool) else None

    return Tender(
        ekap_id=ekap_id,
        title=title or "-",
        institution=institution or "-",
        okas_codes=_collect_okas(item),
        publish_date=None,
        tender_date=parse_date_value(tender_dt),
        tender_datetime=tender_dt,
        url=ihale_url(ikn),
        description=title,
        ikn=ikn or "-",
        province=_as_text(item.get("ihaleIlAdi")),
        status=_as_text(item.get("ihaleDurumAciklama")),
        status_code=_as_text(item.get("ihaleDurum")),
        tender_type=_as_text(item.get("ihaleTipAciklama")),
        tender_type_code=_as_text(item.get("ihaleTip")),
        procedure=_as_text(item.get("ihaleUsulAciklama")),
        has_announcement=has_announcement,
        source=source,
        raw=item,
    )


def extract_item_list(payload: Any) -> List[Dict[str, Any]]:
    """Arama JSON'undan `list` dizisini okur. Başka sarmalayıcı adı denenmez."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    value = payload.get("list")
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    return []


def extract_total_count(payload: Any, items: Iterable[Any]) -> int:
    if isinstance(payload, dict) and "totalCount" in payload:
        value = payload.get("totalCount")
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return len(list(items))


def looks_like_search_payload(payload: Any) -> bool:
    """JSON'un GetListByParameters özet yanıtı olup olmadığına yapıdan bakar."""
    if not isinstance(payload, dict) or "list" not in payload or "totalCount" not in payload:
        return False
    items = extract_item_list(payload)
    if not items:
        return True
    sample = items[0]
    return any(
        key in sample
        for key in ("ikn", "ihaleAdi", "idareAdi", "ihaleTarihSaat", "id")
    )
