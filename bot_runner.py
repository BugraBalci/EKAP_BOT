import csv
import os
from typing import List


def verileri_kaydet(veri_listesi, dosya_adi="ekap_arayuz_sonuclar.csv"):
    if not veri_listesi:
        print("⚠️ Çekilecek hiçbir geçerli veri bulunamadı.")
        return

    alanlar = list(veri_listesi[0].keys())
    for kayit in veri_listesi[1:]:
        for alan in kayit:
            if alan not in alanlar:
                alanlar.append(alan)

    with open(dosya_adi, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=alanlar, delimiter=";", extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(veri_listesi)
    print(f"💾 Veriler '{dosya_adi}' dosyasına kaydedildi.")


def _okas_listesi(okas: str) -> List[str]:
    roots: List[str] = []
    seen = set()
    for part in (okas or "").replace(";", ",").split(","):
        code = part.strip()
        if not code or code in seen:
            continue
        seen.add(code)
        roots.append(code)
    return roots


def ekap_botunu_calistir(okas, durum, haric_kelime, limit):
    """EKAP v2 araması — ihale-mcp istemcisi.

    Returns: (tenders, csv_path, meta)
    """
    import asyncio
    import logging

    from ekap.config import EkapConfig
    from ekap.logging_setup import setup_logging
    from ekap.pipeline import run_collection

    kayit_dosyasi = "ekap_arayuz_sonuclar.csv"
    roots = _okas_listesi(str(okas))
    if not roots:
        raise RuntimeError("OKAS kodu boş.")

    os.environ["OKAS_KODU"] = ",".join(roots)
    if haric_kelime:
        os.environ["HARIC_KELIME"] = haric_kelime
    try:
        n = int(limit)
        if n > 0:
            os.environ["EKAP_MAX_PAGES"] = str(min(n, 20))
    except (TypeError, ValueError):
        pass
    _ = durum

    setup_logging(logging.INFO)
    config = EkapConfig.from_env(okas=",".join(roots), haric=haric_kelime or "")
    result = asyncio.run(run_collection(config))
    if result.errors and not result.new_tenders and not result.tenders:
        raise RuntimeError("\n".join(result.errors))
    toplanan = result.mail_rows()
    meta = dict(result.extra.get("meta") or {})
    meta.setdefault("yeni_bu_hafta", [])
    meta.setdefault("yeni_bugun", [])
    meta.setdefault("yeni_dun", [])
    meta.setdefault("kod_hatalari", list(result.errors))
    meta["kaynak"] = result.source
    verileri_kaydet(toplanan, dosya_adi=kayit_dosyasi)
    return toplanan, kayit_dosyasi, meta
