#!/usr/bin/env python3
"""EKAP ihaleleri için Google Takvim şablon linki ve .ics eki.

Otomatik Google Calendar API yazımı kökten kapalıdır. Bot hiçbir Calendar ID'ye
etkinlik basmaz; takvim boş kalır. Kullanıcı maildeki 📅 bağlantılarıyla
(calendar/render?action=TEMPLATE) kendi takvimine iki etkinlik ekler:

  1. İhaleden 7 gün önce, aynı saatte 1 saatlik hazırlık hatırlatıcısı
  2. Gerçek ihale günü / saati etkinliği (ICS'te ihale saatinden 1 saat
     önce VALARM; Google şablon URL'si alarm kuramaz, başlangıç saatini taşır)

Saat metinden okunamazsa Türkiye mesai başlangıcı (09:00) varsayılır.
Geçmişe düşecek 7 günlük veya 1 saatlik hatırlatıcı üretilmez; Google
hazırlık etkinliği 7 günden az kala en erken "şimdi"ye çekilir.

Selenium kart satır şeması:
  İKN, İşin Adı, Kurum, İhale Tarihi / İl / Saat, İl, Link
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Istanbul")
ICS_DOSYA_ADI = "gunluk_ekap_ihaleleri.ics"
GOOGLE_CALENDAR_TEMPLATE = "https://calendar.google.com/calendar/render"
HATIRLATMA_GUN_ONCE = 7
HATIRLATMA_SAAT_ONCE = 1
VARSAYILAN_MESAI_SAAT = 9
VARSAYILAN_MESAI_DAKIKA = 0
HATIRLATICI_BASLIK_ONEK = "REMINDER-EKAP: 1 Hafta Sonra İhale Var - "
IHAL_GUNU_BASLIK_ONEK = "EKAP İhale: "

# "11.12.2026 11:00" veya "ANKARA, 11.12.2026 11:00" / "ANKARA / 11.12.2026"
_DT_RE = re.compile(
    r"(?P<d>\d{1,2})[./](?P<m>\d{1,2})[./](?P<y>\d{4})"
    r"(?:[ T](?P<H>\d{1,2})[:.](?P<M>\d{2}))?"
)


@dataclass
class TakvimEtkinligi:
    """ICS'e yazılacak tek VEVENT (hazırlık veya ihale günü)."""

    uid: str
    summary: str
    description: str
    start: datetime  # naive Europe/Istanbul
    alarm_offsets: List[Tuple[timedelta, str]] = field(default_factory=list)


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


def _simdi_tr() -> datetime:
    return datetime.now(TZ).replace(second=0, microsecond=0, tzinfo=None)


def ihale_baslangic_dt(kayit: Dict[str, str]) -> Tuple[Optional[datetime], bool]:
    """Takvim başlangıcı: metindeki saat, yoksa mesai başlangıcı 09:00.

    Dönüş: (naive TR datetime, saat_metinden_okundu).
    """
    dt, has_time = parse_ihale_datetime(tarih_metni_al(kayit))
    if dt is None:
        return None, False
    if not has_time:
        dt = dt.replace(
            hour=VARSAYILAN_MESAI_SAAT,
            minute=VARSAYILAN_MESAI_DAKIKA,
            second=0,
            microsecond=0,
        )
    return dt, has_time


def _saat_ifadesi(dt: Optional[datetime], has_time: bool) -> str:
    if dt is None:
        return "-"
    metin = dt.strftime("%d.%m.%Y %H:%M")
    if not has_time:
        metin += " (saat yok; mesai başlangıcı 09:00 varsayıldı)"
    return metin


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
    dt, has_time = ihale_baslangic_dt(kayit)
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
    satirlar.append(f"Takvim başlangıcı: {_saat_ifadesi(dt, has_time)}")
    satirlar.append("Hatırlatıcı: ihale saatinden 1 saat önce.")
    satirlar.append(f"EKAP: {link}")
    return _kisalt("\n".join(satirlar), 7800)


def hatirlatici_baslik(kayit: Dict[str, str]) -> str:
    """Takvime Ekle başlığı: REMINDER-EKAP: 1 Hafta Sonra İhale Var - {İşin Adı}."""
    ad = isin_adi_al(kayit) or "İhale"
    budget = 1024 - len(HATIRLATICI_BASLIK_ONEK)
    return f"{HATIRLATICI_BASLIK_ONEK}{_kisalt(ad, max(budget, 8))}"


def ihale_gunu_baslik(kayit: Dict[str, str]) -> str:
    """İhale günü etkinlik başlığı: EKAP İhale: {İşin Adı}."""
    ad = isin_adi_al(kayit) or "İhale"
    budget = 1024 - len(IHAL_GUNU_BASLIK_ONEK)
    return f"{IHAL_GUNU_BASLIK_ONEK}{_kisalt(ad, max(budget, 8))}"


def hatirlatici_aciklama(kayit: Dict[str, str]) -> str:
    """Takvime Ekle açıklaması — 7 gün öncesi hazırlık hatırlatıcısı."""
    dt, has_time = ihale_baslangic_dt(kayit)
    tarih = _saat_ifadesi(dt, has_time)
    if tarih == "-":
        tarih = tarih_metni_al(kayit) or "-"
    ikn = ikn_al(kayit) or "-"
    kurum = kurum_al(kayit) or "-"
    link = link_al(kayit) or "-"
    return (
        "⚠️ DİKKAT: Bu etkinlik 1 hafta önceden hazırlık hatırlatıcısıdır!\n"
        f"Gerçek İhale Tarihi ve Saati: {tarih}\n"
        "İhale günü etkinliği ve 1 saat önce bildirim için ikinci bağlantıyı "
        "veya e-posta ekindeki .ics dosyasını kullanın.\n"
        f"İKN: {ikn}\n"
        f"Kurum: {kurum}\n"
        f"EKAP Bağlantısı: {link}"
    )


def ihale_gunu_aciklama(kayit: Dict[str, str]) -> str:
    """İhale günü Google şablonu / ICS açıklaması."""
    dt, has_time = ihale_baslangic_dt(kayit)
    ikn = ikn_al(kayit) or "-"
    ad = isin_adi_al(kayit) or "-"
    kurum = kurum_al(kayit) or "-"
    link = link_al(kayit) or "-"
    satirlar = [
        f"İhale Detayı: {ad}",
        f"İKN: {ikn}",
        f"Kurum: {kurum}",
        f"İhale başlangıcı: {_saat_ifadesi(dt, has_time)}",
        "Hatırlatıcı: ihale saatinden 1 saat önce "
        "(Google şablon URL'si alarm kuramaz; .ics ekindeki VALARM kullanılır).",
        f"EKAP Bağlantısı: {link}",
    ]
    return _kisalt("\n".join(satirlar), 7800)


def _quote_tr(deger: str) -> str:
    """UTF-8 percent-encode; Türkçe karakterler bozulmaz, boşluk %20 olur."""
    return quote(deger or "", safe="", encoding="utf-8", errors="strict")


def _dates_aralik(start: datetime) -> str:
    end = start + timedelta(hours=1)
    return f"{start.strftime('%Y%m%dT%H%M%S')}/{end.strftime('%Y%m%dT%H%M%S')}"


def _google_calendar_dates_param(
    kayit: Dict[str, str],
    *,
    kind: str = "hazirlik",
) -> str:
    """TEMPLATE dates: YYYYMMDDTHHMMSS/YYYYMMDDTHHMMSS (Europe/Istanbul, ctz ile).

    hazirlik: ihale_tarihi - 7 gün, aynı saat, 1 saat süre. Geçmişse şimdiye çekilir.
    ihale: gerçek (veya varsayılan 09:00) başlangıç, 1 saat süre. Geçmişe çekilmez.
    """
    simdi = _simdi_tr()
    dt, _has_time = ihale_baslangic_dt(kayit)
    if kind == "ihale":
        start = dt if dt is not None else simdi
        return _dates_aralik(start)

    if dt is None:
        start = simdi
    else:
        start = dt - timedelta(days=HATIRLATMA_GUN_ONCE)
        if start < simdi:
            # İhaleye 7 günden az kaldı. Geçmişe kurulan etkinlik hiç bildirim
            # üretmez; hazırlık hatırlatıcısı en erken şimdiye çekilir.
            start = simdi
    return _dates_aralik(start)


def google_calendar_template_url(kayit: Dict[str, str]) -> str:
    """7 gün öncesi hazırlık hatırlatıcısı için Google Calendar şablon linki.

    Parametreler urllib.parse.quote ile UTF-8 encode edilir (quote_plus/+ kullanılmaz).
    dates ve ctz içindeki '/' kırılmasın diye encode edilmez.
    """
    text = _quote_tr(hatirlatici_baslik(kayit))
    details = _quote_tr(hatirlatici_aciklama(kayit))
    location = _quote_tr(konum_al(kayit))
    dates = _google_calendar_dates_param(kayit, kind="hazirlik")
    return (
        f"{GOOGLE_CALENDAR_TEMPLATE}?action=TEMPLATE"
        f"&text={text}"
        f"&dates={dates}"
        f"&details={details}"
        f"&location={location}"
        f"&ctz=Europe/Istanbul"
    )


def google_calendar_ihale_gunu_url(kayit: Dict[str, str]) -> str:
    """Gerçek ihale günü / saati için Google Calendar şablon linki."""
    text = _quote_tr(ihale_gunu_baslik(kayit))
    details = _quote_tr(ihale_gunu_aciklama(kayit))
    location = _quote_tr(konum_al(kayit))
    dates = _google_calendar_dates_param(kayit, kind="ihale")
    return (
        f"{GOOGLE_CALENDAR_TEMPLATE}?action=TEMPLATE"
        f"&text={text}"
        f"&dates={dates}"
        f"&details={details}"
        f"&location={location}"
        f"&ctz=Europe/Istanbul"
    )


def google_calendar_linkleri_metin(kayit: Dict[str, str]) -> str:
    """Düz metin bülten satırı: her iki Google Takvim şablonu."""
    return (
        f"takvim-7gun: {google_calendar_template_url(kayit)}"
        f" | takvim-ihale: {google_calendar_ihale_gunu_url(kayit)}"
    )


def _takvim_dugmesi(href: str, etiket: str, renk: str) -> str:
    href = html.escape(href, quote=True)
    etiket = html.escape(etiket, quote=True)
    return (
        f'<a href="{href}" target="_blank" rel="noopener" '
        f'style="display:inline-block;background:{renk};color:#ffffff;'
        "padding:6px 10px;border-radius:6px;text-decoration:none;"
        'font-size:12px;font-weight:bold;white-space:nowrap">'
        f"{etiket}</a>"
    )


def google_calendar_button_html(kayit: Dict[str, str]) -> str:
    """E-posta tablosu: 7 gün önce + ihale günü düğmeleri."""
    hazirlik = _takvim_dugmesi(
        google_calendar_template_url(kayit),
        "📅 7 gün önce",
        "#0F766E",
    )
    ihale = _takvim_dugmesi(
        google_calendar_ihale_gunu_url(kayit),
        "📅 İhale günü",
        "#1E3A5F",
    )
    return (
        '<div style="display:flex;flex-direction:column;gap:6px;'
        'align-items:flex-start">'
        f"{hazirlik}{ihale}</div>"
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


def _trigger_ics(offset: timedelta) -> str:
    """RFC 5545 TRIGGER; icalendar ile aynı biçim (P0D / -PT1H / -P7D)."""
    toplam = int(offset.total_seconds())
    if toplam == 0:
        return "P0D"
    isaret = "-" if toplam < 0 else ""
    kalan = abs(toplam)
    gun = kalan // 86400
    kalan %= 86400
    saat = kalan // 3600
    kalan %= 3600
    dakika = kalan // 60
    saniye = kalan % 60
    if gun and not (saat or dakika or saniye):
        return f"{isaret}P{gun}D"
    if not gun:
        if dakika == 0 and saniye == 0:
            return f"{isaret}PT{saat}H"
        if saniye == 0:
            return f"{isaret}PT{saat}H{dakika}M" if saat else f"{isaret}PT{dakika}M"
        return f"{isaret}PT{saat}H{dakika}M{saniye}S"
    return f"{isaret}P{gun}DT{saat}H{dakika}M{saniye}S"


def _valarm_satirlari(offset: timedelta, aciklama: str) -> List[str]:
    return [
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        f"DESCRIPTION:{_ics_escape(aciklama)}",
        f"TRIGGER:{_trigger_ics(offset)}",
        "END:VALARM",
    ]


def _kayit_etkinlikleri(kayit: Dict[str, str]) -> List[TakvimEtkinligi]:
    """Kayıt başına 7 gün önce hazırlık VEVENT + ihale günü VEVENT (+ VALARM)."""
    ikn = ikn_al(kayit)
    if not ikn:
        return []
    dt, _has_time = ihale_baslangic_dt(kayit)
    if dt is None:
        return []

    simdi = _simdi_tr()
    taban = event_id_from_ikn(ikn)
    out: List[TakvimEtkinligi] = []

    hazirlik = dt - timedelta(days=HATIRLATMA_GUN_ONCE)
    if hazirlik >= simdi:
        out.append(
            TakvimEtkinligi(
                uid=f"{taban}-h7d@ekap-bot.local",
                summary=hatirlatici_baslik(kayit),
                description=hatirlatici_aciklama(kayit),
                start=hazirlik,
                alarm_offsets=[
                    (timedelta(0), "İhale hazırlık hatırlatıcısı — 1 hafta sonra ihale var")
                ],
            )
        )

    alarmlar: List[Tuple[timedelta, str]] = []
    yedi_gun = dt - timedelta(days=HATIRLATMA_GUN_ONCE)
    if yedi_gun >= simdi:
        alarmlar.append(
            (
                timedelta(days=-HATIRLATMA_GUN_ONCE),
                "İhale 7 gün sonra — hazırlık hatırlatıcısı",
            )
        )
    bir_saat = dt - timedelta(hours=HATIRLATMA_SAAT_ONCE)
    if bir_saat >= simdi:
        alarmlar.append(
            (
                timedelta(hours=-HATIRLATMA_SAAT_ONCE),
                "İhale 1 saat sonra başlıyor",
            )
        )

    out.append(
        TakvimEtkinligi(
            uid=f"{taban}@ekap-bot.local",
            summary=ozet_baslik(kayit),
            description=aciklama_metni(kayit),
            start=dt,
            alarm_offsets=alarmlar,
        )
    )
    return out


def _dt_utc(dt_naive: datetime) -> datetime:
    return dt_naive.replace(tzinfo=TZ).astimezone(timezone.utc)


def _vevent_satirlari(etkinlik: TakvimEtkinligi, kayit: Dict[str, str]) -> List[str]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    start_utc = _dt_utc(etkinlik.start)
    end_utc = start_utc + timedelta(hours=1)
    satirlar = [
        "BEGIN:VEVENT",
        f"UID:{etkinlik.uid}",
        f"DTSTAMP:{stamp}",
        f"SUMMARY:{_ics_escape(etkinlik.summary)}",
        f"DESCRIPTION:{_ics_escape(etkinlik.description)}",
        f"DTSTART:{start_utc.strftime('%Y%m%dT%H%M%SZ')}",
        f"DTEND:{end_utc.strftime('%Y%m%dT%H%M%SZ')}",
    ]
    link = link_al(kayit)
    if link:
        satirlar.append(f"URL:{_ics_escape(link)}")
    konum = konum_al(kayit)
    if konum:
        satirlar.append(f"LOCATION:{_ics_escape(konum)}")
    for offset, aciklama in etkinlik.alarm_offsets:
        satirlar.extend(_valarm_satirlari(offset, aciklama))
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
        for etkinlik in _kayit_etkinlikleri(kayit):
            satirlar.extend(_vevent_satirlari(etkinlik, kayit))
            eklenen += 1
    satirlar.append("END:VCALENDAR")
    payload = "\r\n".join(satirlar).encode("utf-8") + b"\r\n"
    return payload, eklenen


def _ics_icalendar(veriler: Sequence[Dict[str, str]]) -> Tuple[bytes, int]:
    from icalendar import Alarm, Calendar, Event

    cal = Calendar()
    cal.add("prodid", "-//EKAP Ihale Bulteni//TR")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", "EKAP Günlük İhaleler")
    cal.add("x-wr-timezone", "Europe/Istanbul")

    eklenen = 0
    for kayit in veriler:
        for etkinlik in _kayit_etkinlikleri(kayit):
            event = Event()
            event.add("uid", etkinlik.uid)
            event.add("dtstamp", datetime.now(timezone.utc))
            event.add("summary", etkinlik.summary)
            event.add("description", etkinlik.description)
            start_local = etkinlik.start.replace(tzinfo=TZ)
            event.add("dtstart", start_local.astimezone(timezone.utc))
            event.add("dtend", (start_local + timedelta(hours=1)).astimezone(timezone.utc))
            link = link_al(kayit)
            if link:
                event.add("url", link)
            konum = konum_al(kayit)
            if konum:
                event.add("location", konum)
            for offset, aciklama in etkinlik.alarm_offsets:
                alarm = Alarm()
                alarm.add("action", "DISPLAY")
                alarm.add("description", aciklama)
                alarm.add("trigger", offset)
                event.add_component(alarm)
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
