#!/usr/bin/env python3
"""Geçen hafta yayımlanan ilan.gov.tr ihalelerini manuel inceleme için çeker.

Ekip geri bildirimi: ilan.gov.tr yedek hattından gelen kayıtlarda bizim
işimizle örtüşenler çok az. Bu script tarih aralığını (varsayılan son 7 gün)
ilan.gov.tr üzerinden tarar, iki kademeli alaka süzgecini uygular ve
incelenebilir bir CSV üretir.

    python manual_ilan_check.py
    python manual_ilan_check.py --gun 10
    python manual_ilan_check.py --baslangic 07.09.2026 --bitis 13.09.2026
    python manual_ilan_check.py --cikti /tmp/ilanlar.csv --detay-limiti 0
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import List, Optional

from ilan_alaka import (
    MANUEL_CSV_KOLONLAR,
    VARSAYILAN_GUN_MANUEL,
    manuel_satira_cevir,
)
from ilan_fallback import IlanYedekHatasi, gecen_haftayi_tara

VARSAYILAN_CIKTI = "ilan_gov_tr_gecen_hafta.csv"


def csv_yaz(kayitlar: List[dict], dosya: Path) -> None:
    satirlar = [manuel_satira_cevir(k) for k in kayitlar]
    dosya.parent.mkdir(parents=True, exist_ok=True)
    with dosya.open("w", encoding="utf-8-sig", newline="") as f:
        yazar = csv.DictWriter(
            f, fieldnames=list(MANUEL_CSV_KOLONLAR), delimiter=";", extrasaction="ignore"
        )
        yazar.writeheader()
        yazar.writerows(satirlar)


def main(argv: Optional[List[str]] = None) -> int:
    ayristirici = argparse.ArgumentParser(
        description="Geçen hafta yayımlanan ilan.gov.tr ihalelerini CSV'ye aktar"
    )
    ayristirici.add_argument(
        "--gun",
        type=int,
        default=VARSAYILAN_GUN_MANUEL,
        help="son kaç gün (varsayılan: 7)",
    )
    ayristirici.add_argument("--baslangic", help="yayın tarihi başlangıcı (DD.MM.YYYY)")
    ayristirici.add_argument("--bitis", help="yayın tarihi bitişi (DD.MM.YYYY)")
    ayristirici.add_argument(
        "--cikti",
        default=VARSAYILAN_CIKTI,
        help=f"CSV çıktı yolu (varsayılan: {VARSAYILAN_CIKTI})",
    )
    ayristirici.add_argument(
        "--haric",
        default="",
        help="ek dışlanacak kelimeler, virgülle",
    )
    ayristirici.add_argument(
        "--detay-limiti",
        type=int,
        default=80,
        help="başlıkta hedef kelime olmayan ilanlar için çekilecek detay sayısı "
        "(0=detay çekme)",
    )
    ayristirici.add_argument(
        "--maksimum",
        type=int,
        default=0,
        help="çekilecek ham ilan üst sınırı (0=sınırsız, güvenlik için API sayfalar)",
    )
    ayristirici.add_argument(
        "--suresi-geceni-ele",
        action="store_true",
        help="ihale günü geçmiş kayıtları da ele (varsayılan: yayım tarihine bak)",
    )
    args = ayristirici.parse_args(argv)

    try:
        kayitlar, _ozet = gecen_haftayi_tara(
            gun=args.gun,
            baslangic=args.baslangic,
            bitis=args.bitis,
            dislanacak_kelime=args.haric,
            detay_limiti=args.detay_limiti,
            maksimum=args.maksimum or None,
            suresi_geceni_ele=args.suresi_geceni_ele,
        )
    except IlanYedekHatasi as e:
        print(f"⛔ {e}")
        return 1
    except Exception as e:
        print(f"⛔ ilan.gov.tr taraması başarısız: {type(e).__name__}: {e}")
        return 1

    cikti = Path(args.cikti)
    csv_yaz(kayitlar, cikti)
    print(f"💾 {len(kayitlar)} kayıt '{cikti}' dosyasına yazıldı.")
    print(
        f"   Kolonlar: {' | '.join(MANUEL_CSV_KOLONLAR)}"
    )
    if kayitlar:
        print("\n📋 İlk kayıtlar:")
        for kayit in kayitlar[:8]:
            satir = manuel_satira_cevir(kayit)
            print(
                f"   • {satir['Yayın Tarihi']} | {satir['İhale Başlığı'][:70]} | "
                f"{satir['Eşleşen Anahtar Kelime']}"
            )
        if len(kayitlar) > 8:
            print(f"   … ve {len(kayitlar) - 8} kayıt daha")
    else:
        print("⚠️ Kriterlere uyan ilan yok; CSV yalnızca başlık satırı içerir.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
