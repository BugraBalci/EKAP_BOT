#!/usr/bin/env python3
"""EKAP arama CLI — vendor/ihale-mcp ortamında uv ile çalışır."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

_VENDOR = Path(__file__).resolve().parent / "vendor" / "ihale-mcp"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

# Canlı EKAP: 2 = İhale İlanı Yayımlanmış, Katılıma Açık
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
    return [tr_lower(k.strip()) for k in haric.split(",") if k.strip()]


def tender_to_row(t: Dict[str, Any]) -> Dict[str, str]:
    return {
        "Kurum": (t.get("idareAdi") or "-").strip(),
        "İşin Adı": (t.get("ihaleAdi") or "-").strip(),
        "İKN": (t.get("ikn") or "-").strip(),
        "İhale Tarihi": (t.get("ihaleTarihSaat") or "").strip(),
        "Tür": (t.get("ihaleTipAciklama") or "-").strip(),
        "İl": (t.get("ihaleIlAdi") or "-").strip(),
        "Durum": (t.get("ihaleDurumAciklama") or "-").strip(),
    }


async def search_page(client, okas: str, skip: int, take: int) -> Dict[str, Any]:
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
        "ihaleTarihSaatBaslangic": None,
        "ihaleTarihSaatBitis": None,
        "ilanTarihSaatBaslangic": None,
        "ilanTarihSaatBitis": None,
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
        "okasBransKodList": [okas],
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
        "siralamaTipi": "asc",
        "paginationSkip": skip,
        "paginationTake": take,
    }
    return await client._make_request(client.tender_endpoint, params)


async def ara(okas: str, haric: str, sayfa_limiti: int, sayfa_boyutu: int = 50) -> List[Dict[str, str]]:
    from ihale_client import EKAPClient

    client = EKAPClient()
    haricler = kelime_listesi(haric)
    sonuclar: List[Dict[str, str]] = []
    atlanan = 0

    if sayfa_limiti and sayfa_limiti > 0:
        max_pages = sayfa_limiti
    else:
        max_pages = 20  # güvenlik tavanı (~1000 kayıt)

    for sayfa in range(max_pages):
        skip = sayfa * sayfa_boyutu
        print(
            f"📄 API: OKAS={okas} sayfa={sayfa + 1}/{max_pages} skip={skip}",
            file=sys.stderr,
            flush=True,
        )
        raw = await search_page(client, okas, skip, sayfa_boyutu)
        tenders = raw.get("list") or []
        total = int(raw.get("totalCount") or 0)

        if sayfa == 0:
            print(f"ℹ️ Toplam katılıma açık: {total}", file=sys.stderr, flush=True)

        if not tenders:
            break

        for t in tenders:
            row = tender_to_row(t)
            metin_alt = tr_lower(" ".join(row.values()))
            if any(k in metin_alt for k in haricler):
                atlanan += 1
                print(
                    f"🚫 SANSÜR: {(row.get('İşin Adı') or '')[:80]}",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            sonuclar.append(row)

        if skip + len(tenders) >= total or len(tenders) < sayfa_boyutu:
            break

    print(
        f"🎉 API bitti. Eklenen={len(sonuclar)} | Sansürlenen={atlanan}",
        file=sys.stderr,
        flush=True,
    )
    return sonuclar


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--okas", required=True)
    parser.add_argument("--haric", default="")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    rows = asyncio.run(ara(args.okas, args.haric, args.limit))
    json.dump(rows, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
