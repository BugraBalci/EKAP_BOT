#!/usr/bin/env python3
"""EKAP ihaleleri için Google Takvim şablon linki (kullanıcı elle ekler).

Otomatik Google Calendar API yazımı kökten kapalıdır. Bot hiçbir Calendar ID'ye
etkinlik basmaz; takvim boş kalır. Kullanıcı yalnızca maildeki
📅 Takvime Ekle bağlantısıyla (calendar/render?action=TEMPLATE) kendi
takvimine, ihaleden 7 gün önce 1 saatlik hatırlatıcı ekler.

API satır şeması (ekap_api_search.tender_to_row):
  İKN, İşin Adı, Kurum, İhale Tarihi (ihaleTarihSaat), İl, Link
"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Istanbul")
ICS_DOSYA_ADI = "gunluk_ekap_ihaleleri.ics"
GOOGLE_CALENDAR_TEMPLATE = "https://calendar.google.com/calendar/render"
HATIRLATMA_GUN_ONCE = 7
HATIRLATICI_BASLIK_ONEK = "REMINDER-EKAP: 1 Hafta Sonra İhale Var - "

# "11.12.2026 11:00" veya "ANKARA, 11.12.2026 11:00" / "ANKARA / 11.12.2026"
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
    return _kayit_alani(kayit, "Kurum", "İhaleyi Veren Kurum", "idareAdi")


def isin_adi_al(kayit: Dict[str, str]) -> str:
    return _kayit_alani(kayit, "İşin Adı", "İhale Detayları", "ihaleAdi")


def il_al(kayit: Dict[str, str]) -> str:
    return _kayit_alani(kayit, "İl")


def konum_al(kayit: Dict[str, str]) -> str:
    """Takvim konumu: kurum varsa kurum, yoksa il."""
    return kurum_al(kayit) or il_al(kayit)


def link_al(kayit: Dict[str, str]) -> str:
    link = _kayit_alani(kayit, "Link")
    if link.startswith("http"):
        return link
    ikn = ikn_al(kayit)
    if ikn:
        return f"https://ekapv2.kik.gov.tr/ekap/search/{ikn.replace('/', '_')}"
    return ""


def tarih_metni_al(kayit: Dict[str, str]) -> str:
    """API `İhale Tarihi` (ihaleTarihSaat) öncelikli; yoksa birleşik İl/Saat metni."""
    return _kayit_alani(kayit, "İhale Tarihi", "ihaleTarihSaat", "İl / Saat")


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
    """ICS özeti: {İhale Kısa Adı} - {Kurum Adı}."""
    ad = isin_adi_al(kayit) or "İhale"
    kurum = kurum_al(kayit)
    suffix = f" - {kurum}" if kurum else ""
    budget = 1024 - len(suffix)
    if budget < 8:
        return _kisalt(f"{ad}{suffix}", 1024)
    return f"{_kisalt(ad, budget)}{suffix}"


def aciklama_metni(kayit: Dict[str, str]) -> str:
    ikn = ikn_al(kayit) or "-"
    ad = isin_adi_al(kayit) or "-"
    kurum = kurum_al(kayit) or "-"
    tarih = tarih_metni_al(kayit) or "-"
    il = il_al(kayit)
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


def hatirlatici_baslik(kayit: Dict[str, str]) -> str:
    """Takvime Ekle başlığı: REMINDER-EKAP: 1 Hafta Sonra İhale Var - {İşin Adı}."""
    ad = isin_adi_al(kayit) or "İhale"
    budget = 1024 - len(HATIRLATICI_BASLIK_ONEK)
    return f"{HATIRLATICI_BASLIK_ONEK}{_kisalt(ad, max(budget, 8))}"


def hatirlatici_aciklama(kayit: Dict[str, str]) -> str:
    """Takvime Ekle açıklaması — 7 gün öncesi hatırlatıcı metni."""
    tarih = tarih_metni_al(kayit) or "-"
    ikn = ikn_al(kayit) or "-"
    kurum = kurum_al(kayit) or "-"
    link = link_al(kayit) or "-"
    return (
        "⚠️ DİKKAT: Bu etkinlik 1 hafta önceden hatırlatıcıdır!\n"
        f"Gerçek İhale Tarihi ve Saati: {tarih}\n"
        f"İKN: {ikn}\n"
        f"Kurum: {kurum}\n"
        f"EKAP Bağlantısı: {link}"
    )


def _quote_tr(deger: str) -> str:
    """UTF-8 percent-encode; Türkçe karakterler bozulmaz, boşluk %20 olur."""
    return quote(deger or "", safe="", encoding="utf-8", errors="strict")


def _google_calendar_dates_param(kayit: Dict[str, str]) -> str:
    """TEMPLATE dates: ihale_tarihi - 7 gün, aynı saat, 1 saat süre.

    YYYYMMDDTHHMMSS/YYYYMMDDTHHMMSS (Europe/Istanbul, ctz ile).
    Saat yoksa 00:00'dan 1 saat. Tarih yoksa bugünün 1 saatlik aralığı.
    """
    dt, _has_time = parse_ihale_datetime(tarih_metni_al(kayit))
    if dt is None:
        start = datetime.now(TZ).replace(second=0, microsecond=0).replace(tzinfo=None)
    else:
        start = dt - timedelta(days=HATIRLATMA_GUN_ONCE)
    end = start + timedelta(hours=1)
    return f"{start.strftime('%Y%m%dT%H%M%S')}/{end.strftime('%Y%m%dT%H%M%S')}"


def google_calendar_template_url(kayit: Dict[str, str]) -> str:
    """Kullanıcının kendi takvimine eklemesi için Google Calendar şablon linki.

    Parametreler urllib.parse.quote ile UTF-8 encode edilir (quote_plus/+ kullanılmaz).
    dates ve ctz içindeki '/' kırılmasın diye encode edilmez.
    """
    text = _quote_tr(hatirlatici_baslik(kayit))
    details = _quote_tr(hatirlatici_aciklama(kayit))
    location = _quote_tr(konum_al(kayit))
    dates = _google_calendar_dates_param(kayit)
    return (
        f"{GOOGLE_CALENDAR_TEMPLATE}?action=TEMPLATE"
        f"&text={text}"
        f"&dates={dates}"
        f"&details={details}"
        f"&location={location}"
        f"&ctz=Europe/Istanbul"
    )


def google_calendar_button_html(kayit: Dict[str, str]) -> str:
    """E-posta tablosu için tıklanabilir Takvime Ekle düğmesi."""
    href = html.escape(google_calendar_template_url(kayit), quote=True)
    return (
        f'<a href="{href}" target="_blank" rel="noopener" '
        'style="display:inline-block;background:#0F766E;color:#ffffff;'
        "padding:6px 10px;border-radius:6px;text-decoration:none;"
        'font-size:12px;font-weight:bold;white-space:nowrap">'
        "📅 Takvime Ekle</a>"
    )


def google_takvime_yaz(veriler: Sequence[Dict[str, str]]) -> Dict[str, Any]:
    """Otomatik takvim yazımı kökten kapalıdır; Calendar API asla çağrılmaz."""
    print(
        "ℹ️ Otomatik Google Takvim yazımı kapalı; Calendar API çağrılmadı. "
        "Maildeki 📅 Takvime Ekle bağlantısını kullanın."
    )
    return {
        "created": 0,
        "updated": 0,
        "skipped": len(veriler),
        "errors": 0,
        "disabled": True,
    }


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
    dt, has_time = parse_ihale_datetime(tarih_metni_al(kayit))
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
    konum = konum_al(kayit)
    if konum:
        satirlar.append(f"LOCATION:{_ics_escape(konum)}")
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
        dt, has_time = parse_ihale_datetime(tarih_metni_al(kayit))
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
        konum = konum_al(kayit)
        if konum:
            event.add("location", konum)
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
