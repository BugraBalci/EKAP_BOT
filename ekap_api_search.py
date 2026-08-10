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
    raw = await client._make_request(client.okas_endpoint, params)
    return raw.get("loadResult", {}).get("data", []) or []


async def expand_okas_codes(client, root_code: str) -> List[str]:
    root_code = (root_code or "").strip()
    if not root_code:
        return []

    rows = await _okas_query(client, [["kod", "=", root_code]], take=10)
    if not rows:
        print(f"⚠️ OKAS kodu bulunamadı, olduğu gibi kullanılacak: {root_code}", file=sys.stderr, flush=True)
        return [root_code]

    root = rows[0]
    child_count = int(root.get("childCount") or 0)
    if child_count <= 0 and not root.get("hasItem"):
        return [root_code]

    prefix = root_code.rstrip("0") or root_code
    tree = await _okas_query(client, [["kod", "startswith", prefix]], take=500)
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
    return await client._make_request(client.tender_endpoint, params)


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
      yeni_bugun: bugün ilanı çıkanlar (09:00 mailinde "Bugün yeni bir ihale var")
      yeni_dun: dün ilanı çıkanlar (09:00 mailinde "Önceki gün yeni ihale girildi")
      yeni_bu_hafta: ikisinin birleşimi (gösterim)
    """
    from ihale_client import EKAPClient

    client = EKAPClient()
    haricler = kelime_listesi(haric)
    okas_codes = await expand_okas_codes(client, okas)
    bugun = date.today()
    dun = bugun - timedelta(days=1)

    tum = await _sayfala(
        client,
        okas_codes,
        haricler,
        sayfa_limiti,
        sayfa_boyutu,
        ihale_baslangic=bugun,
        ilan_baslangic=None,
        etiket="acik+gelecek",
    )
    yeni_bugun = await _sayfala(
        client,
        okas_codes,
        haricler,
        sayfa_limiti,
        sayfa_boyutu,
        ihale_baslangic=bugun,
        ilan_baslangic=bugun,
        ilan_bitis=bugun,
        etiket="yeni-bugun",
    )
    yeni_dun = await _sayfala(
        client,
        okas_codes,
        haricler,
        sayfa_limiti,
        sayfa_boyutu,
        ihale_baslangic=bugun,
        ilan_baslangic=dun,
        ilan_bitis=dun,
        etiket="yeni-dun",
    )

    # Birleşik liste (önce bugün, sonra dün), İKN tekil
    gorulen: Set[str] = set()
    yeni_birlesik: List[Dict[str, str]] = []
    for row in yeni_bugun + yeni_dun:
        ikn = row.get("İKN") or ""
        if ikn in gorulen:
            continue
        gorulen.add(ikn)
        yeni_birlesik.append(row)

    return {
        "okas": okas,
        "tenders": tum,
        "yeni_bugun": yeni_bugun,
        "yeni_dun": yeni_dun,
        "yeni_bu_hafta": yeni_birlesik,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--okas", required=True)
    parser.add_argument("--haric", default="")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    payload = asyncio.run(ara(args.okas, args.haric, args.limit))
    json.dump(payload, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
