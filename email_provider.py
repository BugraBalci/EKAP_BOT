"""EntropyWork MSA email provider — API key asla koda yazılmaz, .env'den okunur."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from ekap.report import (
    FIRST_SEEN_DISCLAIMER,
    THIS_WEEK_DISCLAIMER,
    build_subject,
    format_mail_line,
    mail_cell,
)
from ilan_alaka import kayitlari_suz, meta_listelerini_suz

Attachment = Tuple[str, bytes, str]  # filename, content, mime type

ROOT = Path(__file__).resolve().parent
DEFAULT_URL = "https://msa.entropywork.com/api/send-email"
DEFAULT_FROM = "info@entropywork.com"


def load_dotenv(path: Optional[Path] = None) -> None:
    env_path = path or (ROOT / ".env")
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


def _api_key() -> str:
    load_dotenv()
    key = (os.environ.get("ENTROPY_EMAIL_API_KEY") or "").strip()
    if not key:
        raise RuntimeError(
            "ENTROPY_EMAIL_API_KEY bulunamadı. Proje köküne .env dosyası ekleyin "
            "(örnek: .env.example)."
        )
    return key


def _api_url() -> str:
    load_dotenv()
    return (os.environ.get("ENTROPY_EMAIL_API_URL") or DEFAULT_URL).strip()


def _from_address() -> str:
    load_dotenv()
    return (os.environ.get("ENTROPY_EMAIL_FROM") or DEFAULT_FROM).strip()


def send_email(
    to: str,
    subject: str,
    body: str,
    html: Optional[str] = None,
    attachments: Optional[Sequence[Attachment]] = None,
) -> Dict[str, Any]:
    to = (to or "").strip()
    if not to or "@" not in to:
        raise ValueError("Geçerli bir alıcı e-posta adresi gerekli.")

    payload: Dict[str, Any] = {
        "from": _from_address(),
        "to": to,
        "subject": subject,
        "body": body,
    }
    if html:
        payload["html"] = html
    if attachments:
        payload["attachments"] = [
            {
                "filename": name,
                "content": base64.b64encode(data).decode("ascii"),
                "contentType": ctype or "application/octet-stream",
            }
            for name, data, ctype in attachments
            if name and data
        ]

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        _api_url(),
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-API-Key": _api_key(),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(raw) if raw else {"ok": True, "status": resp.status}
            except json.JSONDecodeError:
                return {"ok": True, "status": resp.status, "raw": raw}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        if attachments:
            print(
                f"⚠️ E-posta API eki reddetti (HTTP {e.code}); "
                "ek olmadan yeniden denenecek."
            )
            return send_email(to=to, subject=subject, body=body, html=html)
        raise RuntimeError(f"E-posta API hatası HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"E-posta API'ye bağlanılamadı: {e}") from e


def _esc(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _tablo_satirlari(veriler: List[Dict[str, str]]) -> str:
    rows = []
    for v in veriler:
        link = mail_cell(v, "EKAP Linki")
        link_html = (
            f'<a href="{_esc(link)}" target="_blank" rel="noopener">Aç</a>'
            if link.startswith("http")
            else _esc(link if link != "-" else "")
            or "-"
        )
        rows.append(
            "<tr>"
            f"<td>{_esc(mail_cell(v, 'İKN'))}</td>"
            f"<td>{_esc(mail_cell(v, 'İhale Adı'))}</td>"
            f"<td>{_esc(mail_cell(v, 'İdare'))}</td>"
            f"<td>{_esc(mail_cell(v, 'İl'))}</td>"
            f"<td>{_esc(mail_cell(v, 'İhale Tarihi'))}</td>"
            f"<td>{_esc(mail_cell(v, 'Tür'))}</td>"
            f"<td>{link_html}</td>"
            "</tr>"
        )
    return "".join(rows)


def _yeni_kutu(
    baslik: str,
    aciklama: str,
    veriler: List[Dict[str, str]],
    border: str,
    bg: str,
    head: str,
) -> str:
    n = len(veriler)
    return f"""
    <div style="border:2px solid {border};border-radius:8px;padding:14px 16px;margin:0 0 18px 0;
                background:{bg};font-family:Arial,sans-serif">
      <div style="font-size:16px;font-weight:bold;color:{head};margin-bottom:8px">
        {baslik}
      </div>
      <p style="margin:0 0 10px 0;color:#333;font-size:13px">{aciklama} ({n} adet).</p>
      <table border="1" cellpadding="6" cellspacing="0"
             style="border-collapse:collapse;width:100%;font-size:13px;background:#fff">
        <thead style="background:{head};color:#fff">
          <tr>
            <th>İKN</th><th>İhale Adı</th><th>İdare</th><th>İl</th>
            <th>İhale Tarihi</th><th>Tür</th><th>EKAP Linki</th>
          </tr>
        </thead>
        <tbody>
          {_tablo_satirlari(veriler) if veriler else '<tr><td colspan="7">Kayıt yok</td></tr>'}
        </tbody>
      </table>
    </div>
    """


KAYNAK_UYARI_BASLIK = "⚠️ Bu liste {kaynak} yedek hattından derlendi"
KAYNAK_UYARI_METIN = (
    "EKAP taraması yapılamadığı için kayıtlar ilan.gov.tr ihale duyurularından "
    "toplandı ve iki kademeli alaka süzgecinden (yazılım / donanım / bilişim / "
    "network / lisans whitelist, arsa-taşınmaz-kantin-araç kiralama kara liste) "
    "geçirildi. Kapsam EKAP'tan farklıdır: OKAS kodu yerine anahtar kelime "
    "eşlemesi kullanılır ve yalnızca yayımlanmış, kriterlere uyan ilanlar görünür."
)


def _kaynak_uyarisi(meta: Optional[Dict[str, Any]]) -> tuple[str, str]:
    """Liste yedek hattan geldiyse (başlık, açıklama) döndürür, yoksa ("", "").

    Bu gövdedeki metinler kayıtları "EKAP'ta yayımlanan" diye anlatıyor. Uyarı
    olmazsa ilan.gov.tr listesi EKAP taraması sanılır ve kapsam farkı görünmez.
    """
    if not isinstance(meta, dict):
        return "", ""
    kaynak = (meta.get("kaynak") or "EKAP").strip()
    if not kaynak or kaynak == "EKAP":
        return "", ""

    aciklama = KAYNAK_UYARI_METIN
    not_ = (meta.get("saglik_ozeti") or meta.get("selenium_hatasi") or "").strip()
    if not_:
        aciklama += f" EKAP durumu: {not_}"
    return KAYNAK_UYARI_BASLIK.format(kaynak=kaynak), aciklama


def ihale_sonuclarini_maile_cevir(
    veriler: List[Dict[str, str]],
    okas: str,
    yeni_meta: Optional[Union[List[Dict[str, str]], Dict[str, Any]]] = None,
) -> tuple[str, str, str]:
    veriler, ozet = kayitlari_suz(veriler)
    if ozet.elenen:
        print(
            f"🧹 Mail tablosu: {ozet.elenen} alakasız ilan.gov.tr kaydı elendi, "
            f"{len(veriler)} kaldı."
        )

    yeni_bugun: List[Dict[str, str]] = []
    yeni_dun: List[Dict[str, str]] = []
    yeni_hafta: List[Dict[str, str]] = []

    if isinstance(yeni_meta, list):
        yeni_hafta = meta_listelerini_suz(
            {"yeni_bu_hafta": list(yeni_meta)}, veriler
        ).get("yeni_bu_hafta") or []
    elif isinstance(yeni_meta, dict):
        if yeni_meta.get("tracking") == "first_seen":
            union = (
                list(yeni_meta.get("yeni_bugun") or [])
                + list(yeni_meta.get("yeni_dun") or [])
                + list(yeni_meta.get("yeni_bu_hafta") or [])
            )
            yeni_meta = meta_listelerini_suz(yeni_meta, union)
        else:
            yeni_meta = meta_listelerini_suz(yeni_meta, veriler)
        yeni_bugun = list(yeni_meta.get("yeni_bugun") or [])
        yeni_dun = list(yeni_meta.get("yeni_dun") or [])
        yeni_hafta = list(yeni_meta.get("yeni_bu_hafta") or [])

    uyari_baslik, uyari_aciklama = _kaynak_uyarisi(
        yeni_meta if isinstance(yeni_meta, dict) else None
    )

    subject = build_subject(yeni_bugun, yeni_dun)
    if uyari_baslik:
        subject += " — ilan.gov.tr yedek hattı"

    lines = []
    if uyari_baslik:
        lines += [uyari_baslik, uyari_aciklama, ""]
    lines += [
        f"OKAS: {okas}",
        f"Konu özeti: {subject}",
        FIRST_SEEN_DISCLAIMER,
        THIS_WEEK_DISCLAIMER,
        (
            f"Bugün ilk tespit: {len(yeni_bugun)} | Dün ilk tespit: {len(yeni_dun)} | "
            f"Bu hafta ilk tespit (özet): {len(yeni_hafta)}"
        ),
        "",
        "=== BUGÜN İLK KEZ TESPİT EDİLEN İHALELER ===",
    ]
    if yeni_bugun:
        for v in yeni_bugun:
            lines.append(f"- {format_mail_line(v)}")
    else:
        lines.append("(Yok)")

    lines += ["", "=== DÜN İLK KEZ TESPİT EDİLEN İHALELER ==="]
    if yeni_dun:
        for v in yeni_dun:
            lines.append(f"- {format_mail_line(v)}")
    else:
        lines.append("(Yok)")

    lines += [
        "",
        "=== BU HAFTA İLK KEZ TESPİT EDİLEN İHALELER ===",
        "(Haftalık özet: Pazartesi 00:00'dan itibaren; bugün ve dün dahildir.)",
    ]
    if yeni_hafta:
        for v in yeni_hafta:
            lines.append(f"- {format_mail_line(v)}")
    else:
        lines.append("(Yok)")

    body = "\n".join(lines)

    kutular = ""
    kutular += _yeni_kutu(
        "BUGÜN İLK KEZ TESPİT EDİLEN İHALELER",
        "Bugün (Türkiye saati) botun ilk kez gördüğü ihaleler. Bu tarih EKAP resmi ilan tarihi değildir.",
        yeni_bugun,
        "#1F6FEB",
        "#F0F7FF",
        "#1F6FEB",
    )
    kutular += _yeni_kutu(
        "DÜN İLK KEZ TESPİT EDİLEN İHALELER",
        "Dün ilk kez tespit edilen ihaleler. Sabah cron gecikirse burada görünebilir.",
        yeni_dun,
        "#B45309",
        "#FFF7ED",
        "#B45309",
    )
    kutular += _yeni_kutu(
        "BU HAFTA İLK KEZ TESPİT EDİLEN İHALELER",
        "Pazartesi 00:00'dan şu ana kadar ilk tespit edilenlerin haftalık özeti. Bugün ve dün bu özete dahildir.",
        yeni_hafta,
        "#047857",
        "#ECFDF5",
        "#047857",
    )

    uyari_kutusu = ""
    if uyari_baslik:
        uyari_kutusu = (
            '<div style="border:2px solid #FDBA74;background:#FFF7ED;color:#9A3412;'
            'border-radius:8px;padding:14px 16px;margin:0 0 18px 0">'
            f'<div style="font-size:16px;font-weight:bold;margin-bottom:6px">'
            f"{_esc(uyari_baslik)}</div>"
            f'<p style="margin:0;font-size:13px">{_esc(uyari_aciklama)}</p>'
            "</div>"
        )

    html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#222">
      {uyari_kutusu}
      <h2 style="margin-bottom:6px">{_esc(subject)}</h2>
      <p style="margin-top:0">
        <b>OKAS:</b> {_esc(okas)}
      </p>
      <p style="margin-top:0;color:#444;font-size:13px">
        {_esc(FIRST_SEEN_DISCLAIMER)}
      </p>
      <p style="margin-top:0;color:#0F766E;font-size:13px">
        {_esc(THIS_WEEK_DISCLAIMER)}
      </p>
      {kutular}
    </body></html>
    """
    return subject, body, html


def uyari_mail_gonder(
    to: str,
    *,
    okas: str,
    hata: str,
    run_url: str = "",
) -> Dict[str, Any]:
    """Arama patlayınca sessiz kalmamak için kısa uyarı maili."""
    konu = "EKAP özeti gönderilemedi — arama hatası"
    govde = "\n".join(
        [
            "Sabah EKAP özeti gönderilemedi; ihale listesi çekilemedi.",
            "",
            f"OKAS: {okas}",
            f"Hata: {hata}",
            f"Koşu: {run_url}" if run_url else "",
            "",
            "Bu mail, cron'un çalıştığını ama EKAP taramasının cevap vermediğini bildirir.",
        ]
    ).strip()
    html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#222">
      <h2 style="color:#B91C1C">EKAP özeti gönderilemedi</h2>
      <p>Sabah cron çalıştı ama ihale listesi çekilemedi; bu yüzden normal özet maili yok.</p>
      <p><b>OKAS:</b> {_esc(okas)}</p>
      <pre style="background:#FEF2F2;padding:12px;white-space:pre-wrap">{_esc(hata)}</pre>
      {f'<p><a href="{_esc(run_url)}">GitHub Actions koşusu</a></p>' if run_url else ''}
    </body></html>
    """
    return send_email(to=to, subject=konu, body=govde, html=html)


def sonuclari_email_gonder(
    to: str,
    veriler: List[Dict[str, str]],
    okas: str,
    yeni_bu_hafta: Optional[Union[List[Dict[str, str]], Dict[str, Any]]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    # geriye dönük: yeni_bu_hafta=list veya meta=dict; kwargs.yeni_meta
    meta = kwargs.get("yeni_meta", yeni_bu_hafta)
    attachments = kwargs.get("attachments")
    subject, body, html = ihale_sonuclarini_maile_cevir(veriler, okas, meta)
    return send_email(
        to=to, subject=subject, body=body, html=html, attachments=attachments
    )
