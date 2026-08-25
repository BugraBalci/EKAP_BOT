#!/usr/bin/env python3
"""GitHub Actions / cron: Selenium ile EKAP tarama + SMTP sabah bülteni.

GUI (main.py) ve API (bot_runner.py) yollarına dokunmaz.
Ortam değişkenleri: SENDER_MAIL, SENDER_PASSWORD, EKAP_EMAIL_RECIPIENTS (alias: RECEIVER_MAILS)
İsteğe bağlı: SMTP_HOST, SMTP_PORT (varsayılan smtp.gmail.com:465 SSL), OKAS_KODU, HARIC_KELIME, SAYFA_LIMITI
"""

from __future__ import annotations

import html
import os
import smtplib
import sys
import time
import traceback
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Dict, List, Sequence
from zoneinfo import ZoneInfo

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait

from browser_utils import tarayiciyi_baslat
from data_scraper import verileri_cek, verileri_kaydet
from ekap_actions import arama_yap_ve_gosterimi_ayarla, ogretici_kapat, okas_kodu_sec

EKAP_URL = "https://ekapv2.kik.gov.tr/ekap/search"
DEFAULT_OKAS = "48000000"
DEFAULT_HARIC = "lisans, araba"
KAYIT_DOSYASI = "ekap_arayuz_sonuclar.csv"
TZ = ZoneInfo("Europe/Istanbul")

TABLO_SUTUNLARI = ("Kurum", "İşin Adı", "İKN", "İl / Saat", "Link")


def _bugun() -> datetime:
    return datetime.now(TZ)


def _okas_kodlari() -> List[str]:
    raw = (os.environ.get("OKAS_KODU") or DEFAULT_OKAS).strip()
    return [x.strip() for x in raw.split(",") if x.strip()]


def _haric_kelime() -> str:
    return os.environ.get("HARIC_KELIME") or DEFAULT_HARIC


def _sayfa_limiti() -> int:
    raw = (os.environ.get("SAYFA_LIMITI") or "0").strip()
    try:
        return int(raw)
    except ValueError:
        return 0


def _alicilar() -> List[str]:
    raw = (
        (os.environ.get("EKAP_EMAIL_RECIPIENTS") or "").strip()
        or (os.environ.get("RECEIVER_MAILS") or "").strip()
    )
    return [x.strip() for x in raw.split(",") if x.strip() and "@" in x]


def _esc(text: str) -> str:
    return html.escape(text or "", quote=True)


def _hucre(kayit: Dict[str, str], anahtar: str) -> str:
    if anahtar == "Kurum":
        return kayit.get("Kurum") or kayit.get("İhaleyi Veren Kurum") or "-"
    if anahtar == "İşin Adı":
        return kayit.get("İşin Adı") or kayit.get("İhale Detayları") or "-"
    return kayit.get(anahtar) or "-"


def html_tablo(veriler: Sequence[Dict[str, str]]) -> str:
    if not veriler:
        return (
            '<p style="margin:16px 0;padding:12px 14px;background:#FFF7ED;'
            'border:1px solid #FDBA74;border-radius:8px;color:#9A3412">'
            "Bu tarama için filtrelere uyan ihale bulunamadı."
            "</p>"
        )

    satirlar = []
    for i, kayit in enumerate(veriler):
        bg = "#F8FAFC" if i % 2 else "#FFFFFF"
        hucreler = []
        for sutun in TABLO_SUTUNLARI:
            deger = _hucre(kayit, sutun)
            if sutun == "Link" and deger.startswith("http"):
                icerik = (
                    f'<a href="{_esc(deger)}" target="_blank" rel="noopener" '
                    'style="color:#1D4ED8;font-weight:bold">Aç</a>'
                )
            else:
                icerik = _esc(deger)
            hucreler.append(
                f'<td style="padding:8px 10px;border-bottom:1px solid #E2E8F0;'
                f'vertical-align:top">{icerik}</td>'
            )
        satirlar.append(f'<tr style="background:{bg}">{"".join(hucreler)}</tr>')

    basliklar = "".join(
        f'<th style="padding:10px;text-align:left;font-size:13px">{_esc(s)}</th>'
        for s in TABLO_SUTUNLARI
    )
    return f"""
    <table cellpadding="0" cellspacing="0"
           style="border-collapse:collapse;width:100%;font-size:13px;
                  font-family:Arial,sans-serif;border:1px solid #CBD5E1">
      <thead>
        <tr style="background:#1E3A5F;color:#fff">{basliklar}</tr>
      </thead>
      <tbody>
        {"".join(satirlar)}
      </tbody>
    </table>
    """


def bulten_html(
    *,
    baslik: str,
    ozet: str,
    okas: str,
    veriler: Sequence[Dict[str, str]],
    hata: str = "",
) -> str:
    tarih = _bugun().strftime("%d.%m.%Y %H:%M")
    hata_kutusu = ""
    if hata:
        hata_kutusu = f"""
        <pre style="background:#FEF2F2;color:#991B1B;padding:12px 14px;
                    border-radius:8px;white-space:pre-wrap;font-size:12px">{_esc(hata)}</pre>
        """
    return f"""
    <html>
      <body style="margin:0;padding:0;background:#F1F5F9">
        <div style="max-width:960px;margin:0 auto;padding:24px;
                    font-family:Arial,sans-serif;color:#0F172A">
          <div style="background:#1E3A5F;color:#fff;padding:18px 20px;border-radius:10px 10px 0 0">
            <div style="font-size:20px;font-weight:bold">{_esc(baslik)}</div>
            <div style="margin-top:6px;font-size:13px;opacity:.9">{_esc(tarih)} · Türkiye saati</div>
          </div>
          <div style="background:#fff;padding:20px;border-radius:0 0 10px 10px;
                      border:1px solid #E2E8F0;border-top:none">
            <p style="margin:0 0 8px 0">{_esc(ozet)}</p>
            <p style="margin:0 0 16px 0;color:#475569;font-size:13px">
              <b>OKAS:</b> {_esc(okas)}
            </p>
            {hata_kutusu}
            {html_tablo(veriler)}
            <p style="margin:18px 0 0 0;color:#64748B;font-size:12px">
              Bu bülten GitHub Actions üzerindeki headless Chrome taramasından üretilmiştir.
            </p>
          </div>
        </div>
      </body>
    </html>
    """


def bulten_metin(
    *,
    baslik: str,
    ozet: str,
    okas: str,
    veriler: Sequence[Dict[str, str]],
    hata: str = "",
) -> str:
    lines = [
        baslik,
        ozet,
        f"OKAS: {okas}",
        "",
    ]
    if hata:
        lines += ["HATA:", hata, ""]
    if not veriler:
        lines.append("Filtrelere uyan ihale bulunamadı.")
    else:
        for kayit in veriler:
            lines.append(
                " | ".join(
                    [
                        _hucre(kayit, "İKN"),
                        _hucre(kayit, "İşin Adı"),
                        _hucre(kayit, "Kurum"),
                        _hucre(kayit, "İl / Saat"),
                        _hucre(kayit, "Link"),
                    ]
                )
            )
    return "\n".join(lines)


def mail_gonder(konu: str, html_govde: str, metin_govde: str) -> None:
    sender_mail = (os.environ.get("SENDER_MAIL") or "").strip()
    sender_password = os.environ.get("SENDER_PASSWORD") or ""
    alicilar = _alicilar()
    if not sender_mail or "@" not in sender_mail:
        raise RuntimeError("SENDER_MAIL ortam değişkeni eksik veya geçersiz.")
    if not sender_password:
        raise RuntimeError("SENDER_PASSWORD ortam değişkeni eksik.")
    if not alicilar:
        raise RuntimeError(
            "EKAP_EMAIL_RECIPIENTS (veya RECEIVER_MAILS) ortam değişkeni eksik veya geçersiz."
        )

    host = (os.environ.get("SMTP_HOST") or "smtp.gmail.com").strip()
    port = int((os.environ.get("SMTP_PORT") or "465").strip())

    msg = MIMEMultipart("alternative")
    msg["Subject"] = konu
    msg["From"] = formataddr(("EKAP Sabah Bülteni", sender_mail))
    msg["To"] = ", ".join(alicilar)
    msg.attach(MIMEText(metin_govde, "plain", "utf-8"))
    msg.attach(MIMEText(html_govde, "html", "utf-8"))

    print(f"📧 Mail gönderiliyor (SMTP_SSL {host}:{port}) → {', '.join(alicilar)}")
    with smtplib.SMTP_SSL(host, port, timeout=30) as server:
        server.login(sender_mail, sender_password)
        server.send_message(msg)
    print("✅ Mail gönderildi.")


def _sayfaya_git(driver, url: str) -> None:
    try:
        driver.get(url)
    except TimeoutException:
        print("⚠️ Sayfa yükleme zaman aşımı; mevcut DOM ile devam ediliyor.")
    try:
        WebDriverWait(driver, 15).until(
            lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
        )
    except TimeoutException:
        print("⚠️ document.readyState beklenirken zaman aşımı; devam ediliyor.")


def ekap_tara(okas_kodlari: Sequence[str], haric: str, limit: int) -> List[Dict[str, str]]:
    driver = None
    toplanan: List[Dict[str, str]] = []
    gorulen = set()

    try:
        driver, wait = tarayiciyi_baslat()
        _sayfaya_git(driver, EKAP_URL)
        time.sleep(3)

        ogretici_kapat(driver, wait)

        for i, okas in enumerate(okas_kodlari):
            if i > 0:
                _sayfaya_git(driver, EKAP_URL)
                time.sleep(3)
                ogretici_kapat(driver, wait)

            okas_kodu_sec(driver, wait, okas)
            print("⏳ EKAP sisteminin OKAS kodunu algılaması bekleniyor...")
            time.sleep(3)
            arama_yap_ve_gosterimi_ayarla(driver, wait, gosterim_sayisi="50")
            time.sleep(5)

            kayitlar = verileri_cek(driver, wait, limit, dislanacak_kelime=haric)
            for kayit in kayitlar:
                anahtar = (kayit.get("İKN") or kayit.get("İhale Detayları") or "").strip()
                if anahtar and anahtar in gorulen:
                    continue
                if anahtar:
                    gorulen.add(anahtar)
                toplanan.append(kayit)
    finally:
        if driver is not None:
            driver.quit()

    verileri_kaydet(toplanan, dosya_adi=KAYIT_DOSYASI)
    return toplanan


def _bilgilendirme_gonder(
    *,
    konu: str,
    baslik: str,
    ozet: str,
    okas: str,
    veriler: Sequence[Dict[str, str]],
    hata: str = "",
) -> None:
    html_govde = bulten_html(baslik=baslik, ozet=ozet, okas=okas, veriler=veriler, hata=hata)
    metin = bulten_metin(baslik=baslik, ozet=ozet, okas=okas, veriler=veriler, hata=hata)
    mail_gonder(konu, html_govde, metin)


def main() -> int:
    okas_kodlari = _okas_kodlari()
    okas_etiket = ", ".join(okas_kodlari)
    haric = _haric_kelime()
    limit = _sayfa_limiti()
    tarih = _bugun().strftime("%d.%m.%Y")

    print(f"🚀 EKAP cron başladı | OKAS={okas_etiket} | hariç={haric} | limit={limit}")

    try:
        veriler = ekap_tara(okas_kodlari, haric, limit)
    except Exception as e:
        hata = f"{type(e).__name__}: {e}\n\n{traceback.format_exc()[-2500:]}"
        print(hata, file=sys.stderr)
        try:
            _bilgilendirme_gonder(
                konu=f"EKAP Sabah Bülteni — hata ({tarih})",
                baslik="EKAP taraması başarısız",
                ozet="Sabah cron çalıştı ancak Selenium taraması tamamlanamadı.",
                okas=okas_etiket,
                veriler=[],
                hata=hata,
            )
        except Exception as mail_e:
            print(f"⚠️ Hata bildirimi de gönderilemedi: {mail_e}", file=sys.stderr)
            return 2
        return 1

    if not veriler:
        _bilgilendirme_gonder(
            konu=f"EKAP Sabah Bülteni — ihale bulunamadı ({tarih})",
            baslik="Bugün listelenecek ihale yok",
            ozet="Tarama tamamlandı; filtrelere uyan açık ihale bulunamadı.",
            okas=okas_etiket,
            veriler=[],
        )
        print("ℹ️ İhale bulunamadı; bilgilendirme maili gönderildi.")
        return 0

    _bilgilendirme_gonder(
        konu=f"EKAP Sabah Bülteni — {len(veriler)} ihale ({tarih})",
        baslik="EKAP sabah ihale bülteni",
        ozet=f"Tarama tamamlandı. Tabloda {len(veriler)} ihale yer alıyor.",
        okas=okas_etiket,
        veriler=veriler,
    )
    print(f"✅ Bülten gönderildi ({len(veriler)} kayıt).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
