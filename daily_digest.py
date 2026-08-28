#!/usr/bin/env python3
"""Sabah EKAP özet maili.

Kullanım:
  python3 daily_digest.py
  python3 daily_digest.py --to balcibugra4@gmail.com,diger@ornek.com
  python3 daily_digest.py --okas 32230000,72200000 --haric "lisans,araba"

Alıcılar (--to yoksa) .env içindeki EKAP_EMAIL_RECIPIENTS'tan okunur.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback

from bot_runner import ekap_botunu_calistir
from calendar_sync import ICS_DOSYA_ADI, google_takvime_yaz, ics_olustur
from email_provider import load_dotenv, sonuclari_email_gonder, uyari_mail_gonder
from okas_defaults import DEFAULT_OKAS_VIRGUL


def _actions_run_url() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY") or ""
    run_id = os.environ.get("GITHUB_RUN_ID") or ""
    if not repo or not run_id:
        return ""
    server = os.environ.get("GITHUB_SERVER_URL") or "https://github.com"
    return f"{server}/{repo}/actions/runs/{run_id}"


def _alicilar(cli_to: str) -> list[str]:
    load_dotenv()
    raw = cli_to.strip() if cli_to else (os.environ.get("EKAP_EMAIL_RECIPIENTS") or "")
    return [x.strip() for x in raw.split(",") if x.strip() and "@" in x]


def main() -> int:
    parser = argparse.ArgumentParser(description="EKAP günlük özet maili")
    parser.add_argument("--okas", default=DEFAULT_OKAS_VIRGUL)
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

    try:
        veriler, _dosya, meta = ekap_botunu_calistir(
            args.okas, "Teklif Vermeye Açık", args.haric, args.limit
        )
    except Exception as e:
        hata = f"{type(e).__name__}: {e}\n\n{traceback.format_exc()[-2500:]}"
        print(hata, file=sys.stderr)
        run_url = _actions_run_url()
        mail_hatasi = 0
        for to in alicilar:
            try:
                r = uyari_mail_gonder(to, okas=args.okas, hata=hata, run_url=run_url)
                print(f"UYARI MAIL {to}: {r.get('status') or r}")
            except Exception as mail_e:
                mail_hatasi += 1
                print(f"UYARI MAIL {to} gönderilemedi: {mail_e}", file=sys.stderr)
        return 1 if mail_hatasi == 0 else 2

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

    attachments = None
    if veriler:
        try:
            google_takvime_yaz(veriler)
        except Exception as e:
            print(f"Google Takvim senkronu başarısız: {e}", file=sys.stderr)
        try:
            ics_bytes = ics_olustur(veriler)
            attachments = [(ICS_DOSYA_ADI, ics_bytes, "text/calendar")]
        except Exception as e:
            print(f"ICS dosyası oluşturulamadı: {e}", file=sys.stderr)

    for to in alicilar:
        r = sonuclari_email_gonder(
            to, veriler, args.okas, yeni_meta=meta, attachments=attachments
        )
        print(f"MAIL {to}: {r.get('status') or r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
