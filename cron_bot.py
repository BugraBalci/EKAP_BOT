#!/usr/bin/env python3
"""GitHub Actions / cron: Selenium ile EKAP tarama + SMTP sabah bülteni.

GUI (main.py) ile aynı yolu kullanır: bot_runner → headless Chrome.
EKAP API 401 verdiği için ihale-mcp kullanılmaz.

Ortam değişkenleri: SENDER_MAIL, SENDER_PASSWORD, EKAP_EMAIL_RECIPIENTS (alias: RECEIVER_MAILS)
İsteğe bağlı: SMTP_HOST, SMTP_PORT (varsayılan smtp.gmail.com:465 SSL),
OKAS_KODU (virgülle kodlar), HARIC_KELIME, SAYFA_LIMITI (varsayılan 2, en fazla 3).
"""

from __future__ import annotations

import html
import os
import smtplib
import sys
import traceback
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path
from typing import Dict, List, Sequence
from zoneinfo import ZoneInfo

from bot_runner import ekap_botunu_calistir, verileri_kaydet
from email_provider import ihale_sonuclarini_maile_cevir

from okas_defaults import DEFAULT_OKAS_KODLARI

# Selenium ağaç seçimi: ebeveyn kod işaretlenince alt başlıklar da gelir.
TARANACAK_OKAS_KODLARI = list(DEFAULT_OKAS_KODLARI)
DEFAULT_HARIC = "lisans, araba"
KAYIT_DOSYASI = "ekap_arayuz_sonuclar.csv"
DEFAULT_SAYFA_LIMITI = 2
MAX_SAYFA_LIMITI = 3
TZ = ZoneInfo("Europe/Istanbul")

TABLO_SUTUNLARI = ("Kurum", "İşin Adı", "İKN", "İl / Saat", "Link")


def _bugun() -> datetime:
    return datetime.now(TZ)


def _okas_kodlari() -> List[str]:
    raw = (os.environ.get("OKAS_KODU") or "").strip()
    if raw:
        return [x.strip() for x in raw.split(",") if x.strip()]
    return list(TARANACAK_OKAS_KODLARI)


def _kayit_anahtari(kayit: Dict[str, str]) -> str:
    ikn = (kayit.get("İKN") or "").strip()
    if ikn and ikn != "-":
        return f"ikn:{ikn}"
    kurum = (kayit.get("Kurum") or kayit.get("İhaleyi Veren Kurum") or "").strip()
    ad = (kayit.get("İşin Adı") or kayit.get("İhale Detayları") or "").strip()
    return f"kurum:{kurum}|ad:{ad}"


def _haric_kelime() -> str:
    return os.environ.get("HARIC_KELIME") or DEFAULT_HARIC


def _sayfa_limiti() -> int:
    raw = (os.environ.get("SAYFA_LIMITI") or str(DEFAULT_SAYFA_LIMITI)).strip()
    try:
        n = int(raw)
    except ValueError:
        n = DEFAULT_SAYFA_LIMITI
    if n <= 0:
        return DEFAULT_SAYFA_LIMITI
    return min(n, MAX_SAYFA_LIMITI)


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def _env_metin(*isimler: str) -> str:
    for isim in isimler:
        deger = os.environ.get(isim)
        if deger is None:
            continue
        deger = deger.strip().strip("'").strip('"').replace(";", ",")
        if deger:
            return deger
    return ""


def _alicilar() -> List[str]:
    _load_dotenv()
    raw = _env_metin("EKAP_EMAIL_RECIPIENTS", "RECEIVER_MAILS", "RECEIVER_MAIL")
    print(
        "📬 Alıcı env: "
        f"EKAP_EMAIL_RECIPIENTS={'dolu' if _env_metin('EKAP_EMAIL_RECIPIENTS') else 'bos'} | "
        f"RECEIVER_MAILS={'dolu' if _env_metin('RECEIVER_MAILS') else 'bos'}"
    )
    return [x.strip() for x in raw.split(",") if x.strip() and "@" in x]


def _esc(text: str) -> str:
    return html.escape(text or "", quote=True)


def _hucre(kayit: Dict[str, str], anahtar: str) -> str:
    if anahtar == "Kurum":
        return kayit.get("Kurum") or kayit.get("İhaleyi Veren Kurum") or "-"
    if anahtar == "İşin Adı":
        return kayit.get("İşin Adı") or kayit.get("İhale Detayları") or "-"
    if anahtar == "İl / Saat":
        return kayit.get("İl / Saat") or kayit.get("İl") or kayit.get("İhale Tarihi") or "-"
    return kayit.get(anahtar) or "-"


def _normalize_kayit(kayit: Dict[str, str]) -> Dict[str, str]:
    """API satırını bülten tablosuna çevir (İl + ihale tarihi → İl / Saat)."""
    out = dict(kayit)
    if not (out.get("İl / Saat") or "").strip():
        il = (out.get("İl") or "").strip()
        tarih = (out.get("İhale Tarihi") or "").strip()
        out["İl / Saat"] = " / ".join(p for p in (il, tarih) if p) or "-"
    return out


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


def ekap_tara(
    okas_kodlari: Sequence[str], haric: str, limit: int
) -> tuple[List[Dict[str, str]], List[str], Dict[str, List]]:
    """GUI ile aynı Selenium yolunu kullanır; kök kodlar virgülle birleşim kümesi."""
    okas = ",".join(okas_kodlari)
    print(f"🔎 EKAP Selenium taraması | kodlar={okas} | sayfa_limiti={limit}")
    ham, _dosya, meta = ekap_botunu_calistir(
        okas, "Teklif Vermeye Açık", haric, limit
    )
    hatalar = list((meta or {}).get("kod_hatalari") or [])

    toplanan: List[Dict[str, str]] = []
    gorulen = set()
    for kayit in ham:
        kayit = _normalize_kayit(kayit)
        anahtar = _kayit_anahtari(kayit)
        if not anahtar or anahtar in gorulen:
            continue
        gorulen.add(anahtar)
        toplanan.append(kayit)

    verileri_kaydet(toplanan, dosya_adi=KAYIT_DOSYASI)
    print(f"✅ Selenium birleşim: {len(ham)} ham, {len(toplanan)} benzersiz kayıt")
    return toplanan, hatalar, meta or {}


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


def ana_gorev() -> int:
    _load_dotenv()
    okas_kodlari = _okas_kodlari()
    okas_etiket = ", ".join(okas_kodlari)
    haric = _haric_kelime()
    limit = _sayfa_limiti()
    tarih = _bugun().strftime("%d.%m.%Y")
    limit_etiket = str(limit)

    print(
        f"🚀 EKAP cron başladı | {len(okas_kodlari)} OKAS kodu | "
        f"hariç={haric} | sayfa_limiti={limit_etiket}"
    )
    print(f"   Kodlar: {okas_etiket}")

    try:
        veriler, kod_hatalari, meta = ekap_tara(okas_kodlari, haric, limit)
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

    hata_notu = ""
    if kod_hatalari:
        hata_notu = "Atlanan OKAS kodları:\n" + "\n".join(f"- {x}" for x in kod_hatalari)

    ozet = (
        f"{len(okas_kodlari)} OKAS kodu tarandı (headless Chrome). "
        f"Tabloda {len(veriler)} benzersiz ihale yer alıyor."
    )
    if kod_hatalari:
        ozet += f" {len(kod_hatalari)} kod atlandı."

    print("📧 Selenium taraması bitti; tekilleştirilmiş bülten SMTP_SSL:465 ile hemen gönderiliyor.")
    konu, metin, html_govde = ihale_sonuclarini_maile_cevir(
        veriler, okas_etiket, yeni_meta=meta
    )
    if kod_hatalari and not hata_notu:
        hata_notu = "Atlanan adımlar:\n" + "\n".join(f"- {x}" for x in kod_hatalari)
    if not veriler:
        _bilgilendirme_gonder(
            konu=f"EKAP Sabah Bülteni — ihale bulunamadı ({tarih})",
            baslik="Bugün listelenecek ihale yok",
            ozet=ozet,
            okas=okas_etiket,
            veriler=[],
            hata=hata_notu,
        )
        print("ℹ️ İhale bulunamadı; bilgilendirme maili gönderildi.")
        return 1 if kod_hatalari and len(kod_hatalari) == len(okas_kodlari) else 0

    mail_gonder(konu, html_govde, metin)
    print(f"✅ Bülten gönderildi ({len(veriler)} benzersiz kayıt, {len(okas_kodlari)} OKAS kodu).")
    return 0


def main() -> int:
    return ana_gorev()


if __name__ == "__main__":
    raise SystemExit(main())
