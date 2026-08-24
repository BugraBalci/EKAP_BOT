#!/usr/bin/env python3
"""EKAP arama CLI — vendor/ihale-mcp ortamında uv ile çalışır."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

_VENDOR = Path(__file__).resolve().parent / "vendor" / "ihale-mcp"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

# Canlı EKAP: 2 = İhale İlanı Yayımlanmış, Katılıma Açık (sitedeki "Teklif Vermeye Açık")
ACIK_DURUM_IDS = [2]


def tr_lower(metin: str) -> str:
    if not metin:
        return ""
    metin = (
        metin.replace("I", "ı")
        .replace("İ", "i")
        .replace("Ğ", "ğ")
        .replace("Ü", "ü")
        .replace("Ş", "ş")
        .replace("Ö", "ö")
        .replace("Ç", "ç")
    )
    return metin.lower()


def kelime_listesi(haric: str) -> List[str]:
    kelimeler: List[str] = []
    for k in haric.split(","):
        k = tr_lower(k.strip())
        if not k:
            continue
        kelimeler.append(k)
        if k == "lisans":
            kelimeler.append("lisan")
    return kelimeler


def ihale_link(ikn: str) -> str:
    ikn = (ikn or "").strip()
    if not ikn or ikn == "-":
        return ""
    return f"https://ekapv2.kik.gov.tr/ekap/search/{ikn.replace('/', '_')}"


def parse_ihale_tarih(metin: str) -> Optional[datetime]:
    metin = (metin or "").strip()
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(metin, fmt)
        except ValueError:
            continue
    return None


def tender_to_row(t: Dict[str, Any]) -> Dict[str, str]:
    ikn = (t.get("ikn") or "-").strip()
    return {
        "Kurum": (t.get("idareAdi") or "-").strip(),
        "İşin Adı": (t.get("ihaleAdi") or "-").strip(),
        "İKN": ikn,
        "İhale Tarihi": (t.get("ihaleTarihSaat") or "").strip(),
        "Tür": (t.get("ihaleTipAciklama") or "-").strip(),
        "İl": (t.get("ihaleIlAdi") or "-").strip(),
        "Durum": (t.get("ihaleDurumAciklama") or "-").strip(),
        "Link": ihale_link(ikn),
    }


def _http_status(exc: BaseException) -> Optional[int]:
    resp = getattr(exc, "response", None)
    code = getattr(resp, "status_code", None)
    return int(code) if code is not None else None


def _yeniden_denenebilir(exc: BaseException) -> bool:
    code = _http_status(exc)
    if code in {401, 403, 408, 429, 500, 502, 503, 504}:
        return True
    name = type(exc).__name__.lower()
    metin = str(exc).lower()
    return "timeout" in name or "connect" in name or "401" in metin or "unauthorized" in metin


async def _istek_dene(coro_factory, *, deneme: int = 3, etiket: str = "EKAP"):
    last: Optional[BaseException] = None
    for i in range(1, deneme + 1):
        try:
            return await coro_factory()
        except Exception as e:
            last = e
            if not _yeniden_denenebilir(e) or i == deneme:
                raise
            bekle = 2 ** i
            print(
                f"⚠️ {etiket} hata ({_http_status(e) or type(e).__name__}), "
                f"{bekle}s sonra yeniden denenecek ({i}/{deneme})",
                file=sys.stderr,
                flush=True,
            )
            await asyncio.sleep(bekle)
    raise last  # pragma: no cover


async def _okas_query(client, filt: list, take: int = 500) -> List[dict]:
    params = {
        "loadOptions": {
            "filter": {
                "sort": [],
                "group": [],
                "filter": filt,
                "totalSummary": [],
                "groupSummary": [],
                "select": [],
                "preSelect": [],
                "primaryKey": [],
            },
            "take": take,
        }
    }
    raw = await _istek_dene(
        lambda: client._make_request(client.okas_endpoint, params),
        etiket="OKAS",
    )
    return raw.get("loadResult", {}).get("data", []) or []


def parse_okas_roots(okas: str) -> List[str]:
    roots: List[str] = []
    seen: Set[str] = set()
    for part in (okas or "").replace(";", ",").split(","):
        code = part.strip()
        if not code or code in seen:
            continue
        seen.add(code)
        roots.append(code)
    return roots


async def expand_okas_codes(client, root_code: str) -> List[str]:
    root_code = (root_code or "").strip()
    if not root_code:
        return []

    try:
        rows = await _okas_query(client, [["kod", "=", root_code]], take=10)
    except Exception as e:
        print(
            f"⚠️ OKAS ağacı alınamadı ({_http_status(e) or type(e).__name__}), "
            f"kök kod olduğu gibi kullanılacak: {root_code}",
            file=sys.stderr,
            flush=True,
        )
        return [root_code]
    if not rows:
        print(f"⚠️ OKAS kodu bulunamadı, olduğu gibi kullanılacak: {root_code}", file=sys.stderr, flush=True)
        return [root_code]

    root = rows[0]
    child_count = int(root.get("childCount") or 0)
    if child_count <= 0 and not root.get("hasItem"):
        return [root_code]

    prefix = root_code.rstrip("0") or root_code
    try:
        tree = await _okas_query(client, [["kod", "startswith", prefix]], take=500)
    except Exception as e:
        print(
            f"⚠️ OKAS alt kodları alınamadı ({_http_status(e) or type(e).__name__}), "
            f"kök kod kullanılacak: {root_code}",
            file=sys.stderr,
            flush=True,
        )
        return [root_code]
    codes: Set[str] = {root_code}
    for item in tree:
        kod = (item.get("kod") or "").strip()
        if kod.startswith(prefix):
            codes.add(kod)

    out = sorted(codes)
    print(
        f"📂 OKAS '{root_code}' genişletildi → {len(out)} kod (alt başlıklar dahil)",
        file=sys.stderr,
        flush=True,
    )
    return out


def _ihale_tarih_key(row: Dict[str, str]) -> datetime:
    return parse_ihale_tarih(row.get("İhale Tarihi", "")) or datetime.min


def birlestir_satirlar(*listeler: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """İKN birleşim kümesi (OR): sadece A, sadece B veya her ikisi de kalır."""
    gorulen: Set[str] = set()
    out: List[Dict[str, str]] = []
    for liste in listeler:
        for row in liste:
            ikn = row.get("İKN") or ""
            if not ikn or ikn in gorulen:
                continue
            gorulen.add(ikn)
            out.append(row)
    out.sort(key=_ihale_tarih_key, reverse=True)
    return out


def _format_api_date(d: date) -> str:
    return d.strftime("%Y-%m-%d")


async def search_page(
    client,
    okas_codes: List[str],
    skip: int,
    take: int,
    *,
    ihale_baslangic: Optional[date] = None,
    ilan_baslangic: Optional[date] = None,
    ilan_bitis: Optional[date] = None,
) -> Dict[str, Any]:
    params = {
        "searchText": "",
        "filterType": None,
        "ikNdeAra": True,
        "ihaleAdindaAra": True,
        "ihaleIlanindaAra": True,
        "teknikSartnamedeAra": True,
        "idariSartnamedeAra": True,
        "benzerIsMaddesindeAra": True,
        "isinYapilacagiYerMaddesindeAra": True,
        "nitelikTurMiktarMaddesindeAra": True,
        "ihaleBilgilerindeAra": True,
        "sozlesmeTasarisindaAra": True,
        "teklifCetvelindeAra": True,
        "searchType": "GirdigimGibi",
        "iknYili": None,
        "iknSayi": None,
        "ihaleTarihSaatBaslangic": _format_api_date(ihale_baslangic) if ihale_baslangic else None,
        "ihaleTarihSaatBitis": None,
        "ilanTarihSaatBaslangic": _format_api_date(ilan_baslangic) if ilan_baslangic else None,
        "ilanTarihSaatBitis": _format_api_date(ilan_bitis) if ilan_bitis else None,
        "yasaKapsami4734List": [],
        "ihaleTuruIdList": [],
        "ihaleUsulIdList": [],
        "ihaleUsulAltIdList": [],
        "ihaleIlIdList": [],
        "ihaleDurumIdList": ACIK_DURUM_IDS,
        "idareIdList": [],
        "ihaleIlanTuruIdList": [],
        "teklifTuruIdList": [],
        "asiriDusukTeklifIdList": [],
        "istisnaMaddeIdList": [],
        "okasBransKodList": okas_codes,
        "okasBransAdiList": [],
        "titubbKodList": [],
        "gmdnKodList": [],
        "eIhale": None,
        "eEksiltmeYapilacakMi": None,
        "ortakAlimMi": None,
        "kismiTeklifMi": None,
        "fiyatDisiUnsurVarmi": None,
        "ekonomikMaliYeterlilikBelgeleriIsteniyorMu": None,
        "meslekiTeknikYeterlilikBelgeleriIsteniyorMu": None,
        "isDeneyimiGosterenBelgelerIsteniyorMu": None,
        "yerliIstekliyeFiyatAvantajiUygulaniyorMu": None,
        "yabanciIsteklilereIzinVeriliyorMu": None,
        "alternatifTeklifVerilebilirMi": None,
        "konsorsiyumKatilabilirMi": None,
        "altYukleniciCalistirilabilirMi": None,
        "fiyatFarkiVerilecekMi": None,
        "avansVerilecekMi": None,
        "cerceveAnlasmaMi": None,
        "personelCalistirilmasinaDayaliMi": None,
        "orderBy": "ihaleTarihi",
        "siralamaTipi": "desc",
        "paginationSkip": skip,
        "paginationTake": take,
    }
    return await _istek_dene(
        lambda: client._make_request(client.tender_endpoint, params),
        etiket="ihale-arama",
    )


def _filtrele_satirlar(
    tenders: List[dict],
    haricler: List[str],
    gorulen_ikn: Set[str],
    bugun: date,
) -> Tuple[List[Dict[str, str]], int, int]:
    """Gelecek/bugünkü ihale tarihi + sansür. Returns rows, sansur, eski_atilan."""
    sonuclar: List[Dict[str, str]] = []
    atlanan = 0
    eski = 0
    for t in tenders:
        row = tender_to_row(t)
        ikn = row.get("İKN") or ""
        if ikn in gorulen_ikn:
            continue
        gorulen_ikn.add(ikn)

        dt = parse_ihale_tarih(row.get("İhale Tarihi", ""))
        if dt is None or dt.date() < bugun:
            eski += 1
            continue

        metin_alt = tr_lower(" ".join(v for k, v in row.items() if k != "Link"))
        if any(k in metin_alt for k in haricler):
            atlanan += 1
            print(f"🚫 SANSÜR: {(row.get('İşin Adı') or '')[:80]}", file=sys.stderr, flush=True)
            continue
        sonuclar.append(row)
    return sonuclar, atlanan, eski


async def _sayfala(
    client,
    okas_codes: List[str],
    haricler: List[str],
    sayfa_limiti: int,
    sayfa_boyutu: int,
    *,
    ihale_baslangic: Optional[date],
    ilan_baslangic: Optional[date],
    ilan_bitis: Optional[date] = None,
    etiket: str,
) -> List[Dict[str, str]]:
    bugun = date.today()
    sonuclar: List[Dict[str, str]] = []
    atlanan = 0
    eski = 0
    gorulen_ikn: Set[str] = set()
    max_pages = sayfa_limiti if sayfa_limiti and sayfa_limiti > 0 else 100
    total = None

    for sayfa in range(max_pages):
        skip = sayfa * sayfa_boyutu
        print(
            f"📄 API [{etiket}]: sayfa={sayfa + 1}/{max_pages} skip={skip}",
            file=sys.stderr,
            flush=True,
        )
        raw = await search_page(
            client,
            okas_codes,
            skip,
            sayfa_boyutu,
            ihale_baslangic=ihale_baslangic,
            ilan_baslangic=ilan_baslangic,
            ilan_bitis=ilan_bitis,
        )
        tenders = raw.get("list") or []
        total = int(raw.get("totalCount") or 0)
        if sayfa == 0:
            print(f"ℹ️ [{etiket}] API toplam: {total}", file=sys.stderr, flush=True)
        if not tenders:
            break

        rows, a, e = _filtrele_satirlar(tenders, haricler, gorulen_ikn, bugun)
        sonuclar.extend(rows)
        atlanan += a
        eski += e

        if skip + len(tenders) >= total or len(tenders) < sayfa_boyutu:
            break

    print(
        f"🎉 [{etiket}] n={len(sonuclar)} sansür={atlanan} eski/geçersiz={eski} api_total={total}",
        file=sys.stderr,
        flush=True,
    )
    return sonuclar


async def ara(okas: str, haric: str, sayfa_limiti: int, sayfa_boyutu: int = 50) -> Dict[str, Any]:
    """
    Döner:
      tenders: teklif vermeye açık, ihale tarihi >= bugün
      yeni_bugun: bugün yayımlanan ilanlar
      yeni_dun: dün yayımlanan ilanlar
      yeni_bu_hafta: bu hafta yayımlanıp bugün/dün dışında kalan ilanlar

    Birden fazla OKAS kökü virgülle verilirse her kök ayrı aranır, sonuçlar
    İKN birleşim kümesi olarak birleştirilir (kesişim değil).
    """
    from ihale_client import EKAPClient

    client = EKAPClient()
    haricler = kelime_listesi(haric)
    roots = parse_okas_roots(okas)
    bugun = date.today()
    dun = bugun - timedelta(days=1)
    hafta_baslangici = bugun - timedelta(days=bugun.weekday())

    tum_grup: List[List[Dict[str, str]]] = []
    bugun_grup: List[List[Dict[str, str]]] = []
    dun_grup: List[List[Dict[str, str]]] = []
    hafta_grup: List[List[Dict[str, str]]] = []

    for root in roots:
        okas_codes = await expand_okas_codes(client, root)
        print(
            f"🔎 OKAS kökü {root} ayrı aranıyor (birleşim kümesi, kesişim değil)",
            file=sys.stderr,
            flush=True,
        )
        tum_grup.append(
            await _sayfala(
                client,
                okas_codes,
                haricler,
                sayfa_limiti,
                sayfa_boyutu,
                ihale_baslangic=bugun,
                ilan_baslangic=None,
                etiket=f"acik+gelecek:{root}",
            )
        )
        bugun_grup.append(
            await _sayfala(
                client,
                okas_codes,
                haricler,
                sayfa_limiti,
                sayfa_boyutu,
                ihale_baslangic=bugun,
                ilan_baslangic=bugun,
                ilan_bitis=bugun,
                etiket=f"yeni-bugun:{root}",
            )
        )
        dun_grup.append(
            await _sayfala(
                client,
                okas_codes,
                haricler,
                sayfa_limiti,
                sayfa_boyutu,
                ihale_baslangic=bugun,
                ilan_baslangic=dun,
                ilan_bitis=dun,
                etiket=f"yeni-dun:{root}",
            )
        )
        hafta_grup.append(
            await _sayfala(
                client,
                okas_codes,
                haricler,
                sayfa_limiti,
                sayfa_boyutu,
                ihale_baslangic=bugun,
                ilan_baslangic=hafta_baslangici,
                ilan_bitis=bugun,
                etiket=f"yeni-bu-hafta:{root}",
            )
        )

    tum = birlestir_satirlar(*tum_grup)
    yeni_bugun = birlestir_satirlar(*bugun_grup)
    yeni_dun = birlestir_satirlar(*dun_grup)
    yeni_hafta_ham = birlestir_satirlar(*hafta_grup)

    # Bu hafta listesi: bugün ve dün bloklarına girenleri ayıkla, İKN tekil kalsın.
    gorulen: Set[str] = set()
    for row in yeni_bugun + yeni_dun:
        ikn = row.get("İKN") or ""
        if ikn:
            gorulen.add(ikn)

    yeni_hafta: List[Dict[str, str]] = []
    for row in yeni_hafta_ham:
        ikn = row.get("İKN") or ""
        if ikn in gorulen:
            continue
        gorulen.add(ikn)
        yeni_hafta.append(row)

    print(
        f"🔗 Birleşim: açık={len(tum)} bugün={len(yeni_bugun)} dün={len(yeni_dun)} "
        f"hafta={len(yeni_hafta)} (kökler: {', '.join(roots)})",
        file=sys.stderr,
        flush=True,
    )

    return {
        "okas": okas,
        "tenders": tum,
        "yeni_bugun": yeni_bugun,
        "yeni_dun": yeni_dun,
        "yeni_bu_hafta": yeni_hafta,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--okas",
        required=True,
        help="OKAS kodu veya virgülle birden fazla (örn: 48000000,31711000)",
    )
    parser.add_argument("--haric", default="")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    payload = asyncio.run(ara(args.okas, args.haric, args.limit))
    json.dump(payload, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
