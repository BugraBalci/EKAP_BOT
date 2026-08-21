#!/usr/bin/env python3
"""Sabah EKAP özet maili.

Kullanım:
  python3 daily_digest.py
  python3 daily_digest.py --to balcibugra4@gmail.com,diger@ornek.com
  python3 daily_digest.py --okas 48000000,31711000 --haric "lisans,araba"

Alıcılar (--to yoksa) .env içindeki EKAP_EMAIL_RECIPIENTS'tan okunur.
"""

from __future__ import annotations

import argparse
import os
import sys

from bot_runner import ekap_botunu_calistir
from email_provider import load_dotenv, sonuclari_email_gonder


def _alicilar(cli_to: str) -> list[str]:
    load_dotenv()
    raw = cli_to.strip() if cli_to else (os.environ.get("EKAP_EMAIL_RECIPIENTS") or "")
    return [x.strip() for x in raw.split(",") if x.strip() and "@" in x]


def main() -> int:
    parser = argparse.ArgumentParser(description="EKAP günlük özet maili")
    parser.add_argument("--okas", default="48000000,31711000")
    parser.add_argument("--haric", default="lisans, araba")
    parser.add_argument("--limit", type=int, default=0, help="0 = tüm sayfalar")
    parser.add_argument("--to", default="", help="virgülle alıcılar")
    args = parser.parse_args()

    alicilar = _alicilar(args.to)
    if not alicilar:
        print(
            "Alıcı yok. --to ile verin veya .env içine EKAP_EMAIL_RECIPIENTS=a@x.com,b@y.com yazın.",
            file=sys.stderr,
        )
        return 1

    veriler, _dosya, meta = ekap_botunu_calistir(
        args.okas, "Teklif Vermeye Açık", args.haric, args.limit
    )
    if isinstance(meta, list):
        meta = {"yeni_bu_hafta": meta, "yeni_bugun": [], "yeni_dun": []}

    print(
        f"Özet: açık={len(veriler)} | bugün yeni={len(meta.get('yeni_bugun') or [])} | "
        f"dün yeni={len(meta.get('yeni_dun') or [])} | alıcı={len(alicilar)}"
    )

    for v in veriler:
        ad = (v.get("İşin Adı") or "").upper()
        kurum = (v.get("Kurum") or "").upper()
        if "MUS-G8" in ad or ("POSTA" in kurum and "TELGRAF" in kurum):
            print("UYARI beklenmeyen kayıt:", v.get("İKN"), v.get("İşin Adı"), file=sys.stderr)

    for to in alicilar:
        r = sonuclari_email_gonder(to, veriler, args.okas, yeni_meta=meta)
        print(f"MAIL {to}: {r.get('status') or r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
