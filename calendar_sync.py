#!/usr/bin/env python3
"""EKAP ihalelerini ortak Google Takvim'e yazar ve toplu .ics üretir.

Kimlik: GOOGLE_SERVICE_ACCOUNT_JSON (JSON metni, base64 veya dosya yolu)
Takvim: GOOGLE_CALENDAR_ID (paylaşılan takvimin ID'si)

Mükerrer kayıt: İKN'den türetilen sabit Event id (ve private extendedProperties.ekap_ikn).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Istanbul")
ICS_DOSYA_ADI = "gunluk_ekap_ihaleleri.ics"
SCOPES = ("https://www.googleapis.com/auth/calendar",)
_DT_RE = re.compile(
    r"(?P<d>\d{1,2})[./](?P<m>\d{1,2})[./](?P<y>\d{4})"
    r"(?:[ T](?P<H>\d{1,2})[:.](?P<M>\d{2}))?"
)


def _kayit_alani(kayit: Dict[str, str], *anahtarlar: str) -> str:
    for anahtar in anahtarlar:
        deger = (kayit.get(anahtar) or "").strip()
        if deger and deger != "-":
            return deger
    return ""


def ikn_al(kayit: Dict[str, str]) -> str:
    return _kayit_alani(kayit, "İKN", "IKN", "ikn")


def kurum_al(kayit: Dict[str, str]) -> str:
    return _kayit_alani(kayit, "Kurum", "İhaleyi Veren Kurum")


def isin_adi_al(kayit: Dict[str, str]) -> str:
    return _kayit_alani(kayit, "İşin Adı", "İhale Detayları", "İhale Detayı")


def link_al(kayit: Dict[str, str]) -> str:
    link = _kayit_alani(kayit, "Link")
    if link.startswith("http"):
        return link
    ikn = ikn_al(kayit)
    if ikn:
        return f"https://ekapv2.kik.gov.tr/ekap/search/{ikn.replace('/', '_')}"
    return ""


def tarih_metni_al(kayit: Dict[str, str]) -> str:
    return _kayit_alani(kayit, "İhale Tarihi", "İl / Saat", "İl")


def parse_ihale_datetime(metin: str) -> Tuple[Optional[datetime], bool]:
    """İhale tarih/saat metnini çözümler. Dönüş: (naive TR datetime, saati_var_mi)."""
    metin = (metin or "").strip()
    if not metin:
        return None, False

    iso = metin.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(TZ).replace(tzinfo=None)
        has_time = not (
            parsed.hour == 0
            and parsed.minute == 0
            and parsed.second == 0
            and "T" not in metin
            and " " not in metin
        )
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", metin):
            has_time = False
        return parsed, has_time
    except ValueError:
        pass

    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            parsed = datetime.strptime(metin, fmt)
            return parsed, "%H" in fmt
        except ValueError:
            continue

    m = _DT_RE.search(metin)
    if not m:
        return None, False
    parsed = datetime(int(m["y"]), int(m["m"]), int(m["d"]))
    if m["H"] is not None:
        parsed = parsed.replace(hour=int(m["H"]), minute=int(m["M"]))
        return parsed, True
    return parsed, False


def event_id_from_ikn(ikn: str) -> str:
    """Google Calendar event id: yalnızca a-v, 0-9, tire, alt çizgi."""
    digest = hashlib.md5(f"ekap-ikn:{ikn.strip()}".encode("utf-8")).hexdigest()
    return f"ekap{digest}"


def _kisalt(metin: str, limit: int) -> str:
    metin = (metin or "").strip()
    if len(metin) <= limit:
        return metin
    if limit <= 1:
        return metin[:limit]
    return metin[: limit - 1].rstrip() + "…"


def ozet_baslik(kayit: Dict[str, str]) -> str:
    ikn = ikn_al(kayit) or "-"
    ad = isin_adi_al(kayit) or "İhale"
    kurum = kurum_al(kayit)
    prefix = f"[{ikn}] "
    suffix = f" - {kurum}" if kurum else ""
    budget = 1024 - len(prefix) - len(suffix)
    if budget < 8:
        return _kisalt(f"{prefix}{ad}{suffix}", 1024)
    return f"{prefix}{_kisalt(ad, budget)}{suffix}"


def aciklama_metni(kayit: Dict[str, str]) -> str:
    ikn = ikn_al(kayit) or "-"
    ad = isin_adi_al(kayit) or "-"
    kurum = kurum_al(kayit) or "-"
    tarih = tarih_metni_al(kayit) or "-"
    il = _kayit_alani(kayit, "İl")
    link = link_al(kayit) or "-"
    satirlar = [
        f"İhale Detayı: {ad}",
        f"İKN: {ikn}",
        f"Kurum: {kurum}",
    ]
    if il:
        satirlar.append(f"İl: {il}")
    satirlar.append(f"Tarih / Saat: {tarih}")
    satirlar.append(f"EKAP: {link}")
    return _kisalt("\n".join(satirlar), 7800)


def _baslangic_bitis(
    kayit: Dict[str, str],
) -> Tuple[Optional[datetime], bool]:
    dt, has_time = parse_ihale_datetime(tarih_metni_al(kayit))
    return dt, has_time


def google_event_body(kayit: Dict[str, str]) -> Optional[Dict[str, Any]]:
    ikn = ikn_al(kayit)
    if not ikn:
        return None
    dt, has_time = _baslangic_bitis(kayit)
    if dt is None:
        return None

    start: Dict[str, str]
    end: Dict[str, str]
    if has_time:
        start_local = dt.replace(tzinfo=TZ)
        end_local = start_local + timedelta(hours=1)
        fmt = "%Y-%m-%dT%H:%M:%S"
        start = {"dateTime": start_local.strftime(fmt), "timeZone": "Europe/Istanbul"}
        end = {"dateTime": end_local.strftime(fmt), "timeZone": "Europe/Istanbul"}
    else:
        gun = dt.date()
        start = {"date": gun.isoformat()}
        end = {"date": (gun + timedelta(days=1)).isoformat()}

    link = link_al(kayit)
    body: Dict[str, Any] = {
        "id": event_id_from_ikn(ikn),
        "summary": ozet_baslik(kayit),
        "description": aciklama_metni(kayit),
        "start": start,
        "end": end,
        "status": "confirmed",
        "extendedProperties": {
            "private": {
                "ekap_ikn": ikn,
                "ekap_source": "ekap-bot",
            }
        },
    }
    il = _kayit_alani(kayit, "İl")
    if il:
        body["location"] = il
    if link:
        body["source"] = {"title": "EKAP", "url": link}
    return body


def _load_service_account_info() -> Optional[Dict[str, Any]]:
    raw = (os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip()
    if not raw:
        cred_path = (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
        if cred_path and Path(cred_path).is_file():
            return json.loads(Path(cred_path).read_text(encoding="utf-8"))
        return None

    if (raw.startswith("'") and raw.endswith("'")) or (
        raw.startswith('"') and raw.endswith('"')
    ):
        raw = raw[1:-1].strip()

    if raw.startswith("{") or raw.startswith("["):
        return json.loads(raw)

    path = Path(raw)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))

    try:
        decoded = base64.b64decode(raw, validate=True).decode("utf-8")
        if decoded.lstrip().startswith("{"):
            return json.loads(decoded)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        pass
    return None


def _calendar_id() -> str:
    return (os.environ.get("GOOGLE_CALENDAR_ID") or "").strip()


def _google_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    info = _load_service_account_info()
    if not info:
        return None
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _http_status(exc: BaseException) -> Optional[int]:
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None)
    if status is not None:
        return int(status)
    return None


def _execute_with_retry(request, retries: int = 4):
    last: Optional[BaseException] = None
    for i in range(retries):
        try:
            return request.execute()
        except Exception as e:
            last = e
            status = _http_status(e)
            if status in {403, 429} and i < retries - 1:
                time.sleep(2 ** (i + 1))
                continue
            raise
    if last:
        raise last
    raise RuntimeError("Google Calendar isteği başarısız.")


def google_takvime_yaz(veriler: Sequence[Dict[str, str]]) -> Dict[str, Any]:
    """Çekilen ihaleleri ortak takvime ekler/günceller. Kimlik yoksa atlar."""
    ozet = {
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": 0,
        "disabled": False,
    }
    cal_id = _calendar_id()
    has_json = bool((os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip())
    has_file = bool((os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip())
    if not cal_id or not (has_json or has_file):
        print(
            "ℹ️ Google Takvim atlandı "
            "(GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_CALENDAR_ID yok)."
        )
        ozet["disabled"] = True
        return ozet

    try:
        service = _google_service()
    except ImportError:
        print(
            "⚠️ google-api-python-client / google-auth yüklü değil; "
            "Takvim senkronu atlandı."
        )
        ozet["disabled"] = True
        return ozet
    except Exception as e:
        print(f"⚠️ Google Takvim kimliği okunamadı: {e}")
        ozet["errors"] += 1
        return ozet

    if service is None:
        print("ℹ️ Google Takvim atlandı (geçerli service account JSON yok).")
        ozet["disabled"] = True
        return ozet

    from googleapiclient.errors import HttpError

    print(f"📅 Google Takvim senkronu başlıyor ({len(veriler)} ihale) → {cal_id}")
    for i, kayit in enumerate(veriler, start=1):
        body = google_event_body(kayit)
        if body is None:
            ozet["skipped"] += 1
            ikn = ikn_al(kayit) or "?"
            print(f"   ↷ atlandı (İKN/tarih yok): {ikn}")
            continue
        event_id = body["id"]
        try:
            try:
                _execute_with_retry(
                    service.events().insert(
                        calendarId=cal_id,
                        body=body,
                        sendUpdates="none",
                    )
                )
                ozet["created"] += 1
            except HttpError as e:
                if _http_status(e) != 409:
                    raise
                update_body = {k: v for k, v in body.items() if k != "id"}
                _execute_with_retry(
                    service.events().update(
                        calendarId=cal_id,
                        eventId=event_id,
                        body=update_body,
                        sendUpdates="none",
                    )
                )
                ozet["updated"] += 1
        except Exception as e:
            ozet["errors"] += 1
            print(f"   ⚠️ {ikn_al(kayit) or event_id}: {e}")
        if i % 25 == 0:
            print(f"   … {i}/{len(veriler)}")

    print(
        "✅ Google Takvim: "
        f"yeni={ozet['created']} güncellenen={ozet['updated']} "
        f"atlanan={ozet['skipped']} hata={ozet['errors']}"
    )
    return ozet


def _ics_escape(metin: str) -> str:
    return (
        (metin or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _vevent_satirlari(kayit: Dict[str, str]) -> Optional[List[str]]:
    ikn = ikn_al(kayit)
    if not ikn:
        return None
    dt, has_time = _baslangic_bitis(kayit)
    if dt is None:
        return None

    uid = f"{event_id_from_ikn(ikn)}@ekap-bot.local"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    satirlar = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{stamp}",
        f"SUMMARY:{_ics_escape(ozet_baslik(kayit))}",
        f"DESCRIPTION:{_ics_escape(aciklama_metni(kayit))}",
    ]
    if has_time:
        start_utc = dt.replace(tzinfo=TZ).astimezone(timezone.utc)
        end_utc = start_utc + timedelta(hours=1)
        satirlar.append(f"DTSTART:{start_utc.strftime('%Y%m%dT%H%M%SZ')}")
        satirlar.append(f"DTEND:{end_utc.strftime('%Y%m%dT%H%M%SZ')}")
    else:
        gun = dt.date()
        satirlar.append(f"DTSTART;VALUE=DATE:{gun.strftime('%Y%m%d')}")
        satirlar.append(f"DTEND;VALUE=DATE:{(gun + timedelta(days=1)).strftime('%Y%m%d')}")
    link = link_al(kayit)
    if link:
        satirlar.append(f"URL:{_ics_escape(link)}")
    il = _kayit_alani(kayit, "İl")
    if il:
        satirlar.append(f"LOCATION:{_ics_escape(il)}")
    satirlar.append("END:VEVENT")
    return satirlar


def _ics_stdlib(veriler: Sequence[Dict[str, str]]) -> Tuple[bytes, int]:
    satirlar = [
        "BEGIN:VCALENDAR",
        "PRODID:-//EKAP Ihale Bulteni//TR",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:EKAP Günlük İhaleler",
        "X-WR-TIMEZONE:Europe/Istanbul",
    ]
    eklenen = 0
    for kayit in veriler:
        vevent = _vevent_satirlari(kayit)
        if vevent is None:
            continue
        satirlar.extend(vevent)
        eklenen += 1
    satirlar.append("END:VCALENDAR")
    payload = "\r\n".join(satirlar).encode("utf-8") + b"\r\n"
    return payload, eklenen


def _ics_icalendar(veriler: Sequence[Dict[str, str]]) -> Tuple[bytes, int]:
    from icalendar import Calendar, Event

    cal = Calendar()
    cal.add("prodid", "-//EKAP Ihale Bulteni//TR")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", "EKAP Günlük İhaleler")
    cal.add("x-wr-timezone", "Europe/Istanbul")

    eklenen = 0
    for kayit in veriler:
        ikn = ikn_al(kayit)
        dt, has_time = _baslangic_bitis(kayit)
        if not ikn or dt is None:
            continue
        event = Event()
        event.add("uid", f"{event_id_from_ikn(ikn)}@ekap-bot.local")
        event.add("dtstamp", datetime.now(timezone.utc))
        event.add("summary", ozet_baslik(kayit))
        event.add("description", aciklama_metni(kayit))
        if has_time:
            start_local = dt.replace(tzinfo=TZ)
            event.add("dtstart", start_local.astimezone(timezone.utc))
            event.add("dtend", (start_local + timedelta(hours=1)).astimezone(timezone.utc))
        else:
            gun: date = dt.date()
            event.add("dtstart", gun)
            event.add("dtend", gun + timedelta(days=1))
        link = link_al(kayit)
        if link:
            event.add("url", link)
        il = _kayit_alani(kayit, "İl")
        if il:
            event.add("location", il)
        cal.add_component(event)
        eklenen += 1
    return cal.to_ical(), eklenen


def ics_olustur(
    veriler: Sequence[Dict[str, str]],
    dosya: Optional[Path] = None,
) -> bytes:
    """Tüm ihaleleri tek .ics içinde VEVENT olarak birleştirir."""
    try:
        payload, eklenen = _ics_icalendar(veriler)
    except ImportError:
        payload, eklenen = _ics_stdlib(veriler)

    hedef = dosya or (Path(__file__).resolve().parent / ICS_DOSYA_ADI)
    hedef.write_bytes(payload)
    print(f"📎 ICS yazıldı: {hedef} ({eklenen} VEVENT, {len(payload)} bayt)")
    return payload
