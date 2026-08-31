#!/usr/bin/env python3
"""EKAP ihalelerini Google Calendar API ile ortak takvime yazar.

Kimlik: GitHub Secrets / env → GOOGLE_SERVICE_ACCOUNT_JSON (JSON, base64 veya dosya yolu)
Takvim: GOOGLE_CALENDAR_ID

API satır şeması (ekap_api_search.tender_to_row):
  İKN, İşin Adı, Kurum, İhale Tarihi (ihaleTarihSaat), İl, Link

Mükerrer kayıt: İKN'den türetilen sabit Event id + private extendedProperties.ekap_ikn.
Tarih+saat varsa süreli etkinlik; yalnızca tarih varsa tüm gün (all-day).
Mevcut etkinlikte [İKN] önekli veya eski özet varsa events.patch ile başlık yenilenir.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Istanbul")
ICS_DOSYA_ADI = "gunluk_ekap_ihaleleri.ics"
CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]
GOOGLE_CALENDAR_TEMPLATE = "https://calendar.google.com/calendar/render"

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


# Eski özet: "[2026/1381058] İhale adı - Kurum"
_ESKI_IKN_BASLIK_RE = re.compile(r"^\[\s*\d{4}\s*/\s*\d+\s*\]\s+")


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
    """{İhale Kısa Adı} - {Kurum Adı} — İKN başlığa yazılmaz."""
    ad = isin_adi_al(kayit) or "İhale"
    kurum = kurum_al(kayit)
    suffix = f" - {kurum}" if kurum else ""
    budget = 1024 - len(suffix)
    if budget < 8:
        return _kisalt(f"{ad}{suffix}", 1024)
    return f"{_kisalt(ad, budget)}{suffix}"


def _baslik_eski_ikn_iceriyor(ozet: str) -> bool:
    return bool(_ESKI_IKN_BASLIK_RE.match((ozet or "").strip()))


def _baslik_guncellenmeli(mevcut_ozet: str, yeni_ozet: str) -> bool:
    """Yeni formattan farklıysa veya eski [İKN] önekini taşıyorsa True."""
    mevcut = (mevcut_ozet or "").strip()
    yeni = (yeni_ozet or "").strip()
    if not yeni:
        return False
    return mevcut != yeni or _baslik_eski_ikn_iceriyor(mevcut)


def _eski_basliktan_yeni(ozet: str) -> Optional[str]:
    metin = (ozet or "").strip()
    yeni = _ESKI_IKN_BASLIK_RE.sub("", metin, count=1).strip()
    if yeni and yeni != metin:
        return _kisalt(yeni, 1024)
    return None


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


def auto_sync_calendar_enabled() -> bool:
    """AUTO_SYNC_CALENDAR=true olmadıkça ortak takvime toplu yazılmaz."""
    raw = (os.environ.get("AUTO_SYNC_CALENDAR") or "false").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _google_calendar_dates_param(kayit: Dict[str, str]) -> str:
    """Google Calendar TEMPLATE dates: timed UTC veya tüm gün (bitiş hariç)."""
    dt, has_time = parse_ihale_datetime(tarih_metni_al(kayit))
    if dt is None:
        gun = datetime.now(TZ).date()
        return f"{gun.strftime('%Y%m%d')}/{(gun + timedelta(days=1)).strftime('%Y%m%d')}"
    if has_time:
        start_utc = dt.replace(tzinfo=TZ).astimezone(timezone.utc)
        end_utc = start_utc + timedelta(hours=1)
        return (
            f"{start_utc.strftime('%Y%m%dT%H%M%SZ')}/"
            f"{end_utc.strftime('%Y%m%dT%H%M%SZ')}"
        )
    gun = dt.date()
    return f"{gun.strftime('%Y%m%d')}/{(gun + timedelta(days=1)).strftime('%Y%m%d')}"


def google_calendar_template_url(kayit: Dict[str, str]) -> str:
    """Kullanıcının kendi takvimine eklemesi için Google Calendar şablon linki."""
    il = _kayit_alani(kayit, "İl")
    kurum = kurum_al(kayit)
    location = " — ".join(p for p in (il, kurum) if p)
    params = [
        ("action", "TEMPLATE"),
        ("text", ozet_baslik(kayit) or "EKAP İhale"),
        ("dates", _google_calendar_dates_param(kayit)),
        ("details", _kisalt(aciklama_metni(kayit), 1500)),
        ("location", location),
        ("ctz", "Europe/Istanbul"),
    ]
    return f"{GOOGLE_CALENDAR_TEMPLATE}?{urlencode(params, quote_via=quote)}"


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


def google_event_body(kayit: Dict[str, str]) -> Optional[Dict[str, Any]]:
    ikn = ikn_al(kayit)
    if not ikn:
        return None
    dt, has_time = parse_ihale_datetime(tarih_metni_al(kayit))
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


def _unwrap_secret(raw: str) -> str:
    raw = (raw or "").strip().lstrip("\ufeff")
    if (raw.startswith("'") and raw.endswith("'")) or (
        raw.startswith('"') and raw.endswith('"')
    ):
        raw = raw[1:-1].strip()
    return raw


def _json_loads_sa(raw: str) -> Optional[Dict[str, Any]]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"⚠️ GOOGLE_SERVICE_ACCOUNT_JSON json.loads başarısız: {e}")
        return None
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError as e:
            print(f"⚠️ GOOGLE_SERVICE_ACCOUNT_JSON iç içe JSON çözülemedi: {e}")
            return None
    if not isinstance(parsed, dict) or not parsed:
        print("⚠️ GOOGLE_SERVICE_ACCOUNT_JSON boş veya nesne değil; anonim bağlantı yok.")
        return None
    return parsed


def _normalize_sa_info(info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    pk = info.get("private_key")
    if isinstance(pk, str) and "\\n" in pk and "\n" not in pk:
        info = dict(info)
        info["private_key"] = pk.replace("\\n", "\n")
    if (
        info.get("type") != "service_account"
        or not (info.get("client_email") or "").strip()
        or not (info.get("private_key") or "").strip()
    ):
        print(
            "⚠️ GOOGLE_SERVICE_ACCOUNT_JSON service account değil "
            "(type/client_email/private_key eksik); anonim bağlantı yok."
        )
        return None
    return info


def _load_service_account_info() -> Optional[Dict[str, Any]]:
    """Secret'ı json.loads ile oku. Başarısızsa None; asla boş kimlikle devam etme."""
    raw = _unwrap_secret(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or "")
    if not raw:
        cred_path = (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
        if cred_path and Path(cred_path).is_file():
            try:
                info = json.loads(Path(cred_path).read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                print(f"⚠️ GOOGLE_APPLICATION_CREDENTIALS json.loads başarısız: {e}")
                return None
            if not isinstance(info, dict) or not info:
                print("⚠️ GOOGLE_APPLICATION_CREDENTIALS boş; anonim bağlantı yok.")
                return None
            return _normalize_sa_info(info)
        print("⚠️ GOOGLE_SERVICE_ACCOUNT_JSON boş; Google Takvim atlandı.")
        return None

    info: Optional[Dict[str, Any]] = None
    if raw.startswith("{") or raw.startswith("["):
        info = _json_loads_sa(raw)
    else:
        path = Path(raw)
        if path.is_file():
            try:
                info = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                print(f"⚠️ Service account dosyası json.loads başarısız: {e}")
                return None
            if not isinstance(info, dict) or not info:
                print("⚠️ Service account dosyası boş; anonim bağlantı yok.")
                return None
        else:
            try:
                decoded = base64.b64decode(raw, validate=True).decode("utf-8")
            except (ValueError, UnicodeDecodeError) as e:
                print(f"⚠️ GOOGLE_SERVICE_ACCOUNT_JSON JSON/base64 değil: {e}")
                return None
            info = _json_loads_sa(decoded)

    if not info:
        return None
    return _normalize_sa_info(info)


def _calendar_id() -> str:
    return (os.environ.get("GOOGLE_CALENDAR_ID") or "").strip()


def _google_service():
    """Kimlik yoksa None döner; build() asla credentials olmadan çağrılmaz."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    info = _load_service_account_info()
    if not info:
        return None

    credentials = service_account.Credentials.from_service_account_info(
        info,
        scopes=CALENDAR_SCOPES,
    )
    if credentials is None:
        print("⚠️ Service account kimliği oluşturulamadı; anonim bağlantı yok.")
        return None

    email = (info.get("client_email") or "").strip()
    print(f"🔐 Google service account yüklendi: {email}")
    return build("calendar", "v3", credentials=credentials, static_discovery=True)


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
            if status == 429 and i < retries - 1:
                time.sleep(2 ** (i + 1))
                continue
            raise
    if last:
        raise last
    raise RuntimeError("Google Calendar isteği başarısız.")


def _bos_ozet(*, disabled: bool = False, errors: int = 0) -> Dict[str, Any]:
    return {
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": errors,
        "disabled": disabled,
    }


def _etkinlik_yama(service, cal_id: str, event_id: str, yama: Dict[str, Any]) -> Any:
    return _execute_with_retry(
        service.events().patch(
            calendarId=cal_id,
            eventId=event_id,
            body=yama,
            sendUpdates="none",
        )
    )


def _etkinlik_getir(service, cal_id: str, event_id: str) -> Optional[Dict[str, Any]]:
    from googleapiclient.errors import HttpError

    try:
        return _execute_with_retry(
            service.events().get(calendarId=cal_id, eventId=event_id)
        )
    except HttpError as e:
        if _http_status(e) == 404:
            return None
        raise


def _etkinlikleri_listele(
    service,
    cal_id: str,
    *,
    private_prop: Optional[str] = None,
    time_min: Optional[str] = None,
    max_sonuc: int = 250,
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    page_token: Optional[str] = None
    while True:
        kwargs: Dict[str, Any] = {
            "calendarId": cal_id,
            "singleEvents": True,
            "showDeleted": False,
            "maxResults": max_sonuc,
        }
        if private_prop:
            kwargs["privateExtendedProperty"] = private_prop
        if time_min:
            kwargs["timeMin"] = time_min
        if page_token:
            kwargs["pageToken"] = page_token
        resp = _execute_with_retry(service.events().list(**kwargs))
        items.extend(resp.get("items") or [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return items


def _mevcut_etkinlikleri_bul(
    service,
    cal_id: str,
    event_id: str,
    ikn: str,
) -> List[Dict[str, Any]]:
    """Aynı ihaleyi Event id veya private extendedProperties.ekap_ikn ile bulur."""
    mevcut = _etkinlik_getir(service, cal_id, event_id)
    if mevcut:
        return [mevcut]

    if not ikn:
        return []
    return _etkinlikleri_listele(
        service,
        cal_id,
        private_prop=f"ekap_ikn={ikn}",
        max_sonuc=10,
    )


def _eski_ikn_basliklarini_temizle(service, cal_id: str, ozet: Dict[str, Any]) -> None:
    """Takvimde kalan [İKN] önekli başlıkları patch ile yeni formata çevirir."""
    time_min = (datetime.now(TZ) - timedelta(days=30)).isoformat()
    try:
        etkinlikler = _etkinlikleri_listele(service, cal_id, time_min=time_min)
    except Exception as e:
        print(f"⚠️ Eski başlık taraması atlandı: {e}")
        ozet["errors"] += 1
        return

    temizlenen = 0
    for ev in etkinlikler:
        eid = ev.get("id")
        if not eid:
            continue
        yeni = _eski_basliktan_yeni(ev.get("summary") or "")
        if not yeni:
            continue
        try:
            _etkinlik_yama(service, cal_id, eid, {"summary": yeni})
            temizlenen += 1
            ozet["updated"] += 1
        except Exception as e:
            ozet["errors"] += 1
            print(f"   ⚠️ başlık yaması ({eid}): {e}")
    if temizlenen:
        print(f"🧹 Eski [İKN] başlığı temizlendi: {temizlenen} etkinlik")


def google_takvime_yaz(veriler: Sequence[Dict[str, str]]) -> Dict[str, Any]:
    """Çekilen ihaleleri ortak takvime ekler/günceller. Hata olursa yükseltmez."""
    if not auto_sync_calendar_enabled():
        print(
            "ℹ️ AUTO_SYNC_CALENDAR kapalı; ortak takvime yazılmadı. "
            "Maildeki 📅 Takvime Ekle bağlantısını kullanın."
        )
        return _bos_ozet(disabled=True)
    ozet = _bos_ozet()
    try:
        return _google_takvime_yaz(veriler, ozet)
    except Exception as e:
        print(f"⚠️ Google Takvim senkronu başarısız: {e}")
        ozet["errors"] = max(ozet["errors"], 1)
        return ozet


def _google_takvime_yaz(
    veriler: Sequence[Dict[str, str]],
    ozet: Dict[str, Any],
) -> Dict[str, Any]:
    cal_id = _calendar_id()
    if not cal_id:
        print("⚠️ GOOGLE_CALENDAR_ID yok; Google Takvim atlandı.")
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
        print("⚠️ Google Takvim atlandı (geçerli kimlik yok; anonim istek atılmadı).")
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
        yeni_ozet = body["summary"]
        yama = {k: v for k, v in body.items() if k != "id"}
        ikn = ikn_al(kayit) or ""
        try:
            mevcutlar = _mevcut_etkinlikleri_bul(service, cal_id, event_id, ikn)
            if mevcutlar:
                for mevcut in mevcutlar:
                    hedef_id = mevcut.get("id") or event_id
                    if _baslik_guncellenmeli(mevcut.get("summary") or "", yeni_ozet):
                        print(
                            f"   ✏ başlık güncelleniyor: {ikn} | "
                            f"{(mevcut.get('summary') or '')[:60]} → {yeni_ozet[:60]}"
                        )
                        _etkinlik_yama(
                            service, cal_id, hedef_id, {"summary": yeni_ozet}
                        )
                    _etkinlik_yama(service, cal_id, hedef_id, yama)
                ozet["updated"] += 1
            else:
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
                    _etkinlik_yama(service, cal_id, event_id, yama)
                    ozet["updated"] += 1
        except Exception as e:
            ozet["errors"] += 1
            print(f"   ⚠️ {ikn or event_id}: {e}")
        if i % 25 == 0:
            print(f"   … {i}/{len(veriler)}")

    _eski_ikn_basliklarini_temizle(service, cal_id, ozet)

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
