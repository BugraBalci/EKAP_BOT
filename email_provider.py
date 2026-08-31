"""EntropyWork MSA email provider — API key asla koda yazılmaz, .env'den okunur."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from calendar_sync import google_calendar_button_html, google_calendar_template_url

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
        link = (v.get("Link") or "").strip()
        link_html = (
            f'<a href="{_esc(link)}" target="_blank" rel="noopener">Aç</a>'
            if link
            else "-"
        )
        rows.append(
            "<tr>"
            f"<td>{_esc(v.get('İKN', ''))}</td>"
            f"<td>{_esc(v.get('İşin Adı', ''))}</td>"
            f"<td>{_esc(v.get('Kurum', ''))}</td>"
            f"<td>{_esc(v.get('İhale Tarihi', ''))}</td>"
            f"<td>{_esc(v.get('İl', ''))}</td>"
            f"<td>{_esc(v.get('Durum', ''))}</td>"
            f"<td>{link_html}</td>"
            f"<td>{google_calendar_button_html(v)}</td>"
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
            <th>İKN</th><th>İşin Adı</th><th>Kurum</th><th>İhale Tarihi</th>
            <th>İl</th><th>Durum</th><th>Link</th><th>Takvim</th>
          </tr>
        </thead>
        <tbody>
          {_tablo_satirlari(veriler) if veriler else '<tr><td colspan="8">Kayıt yok</td></tr>'}
        </tbody>
      </table>
    </div>
    """


def build_subject(
    yeni_bugun: List[Dict[str, str]],
    yeni_dun: List[Dict[str, str]],
) -> str:
    """Mail başlığı: bugün / dün girilen ihale sayıları (OKAS kodu yok)."""
    n_bugun = len(yeni_bugun)
    n_dun = len(yeni_dun)
    return f"Bugün {n_bugun} ihale, dün {n_dun} ihale girildi"


def ihale_sonuclarini_maile_cevir(
    veriler: List[Dict[str, str]],
    okas: str,
    yeni_meta: Optional[Union[List[Dict[str, str]], Dict[str, Any]]] = None,
) -> tuple[str, str, str]:
    yeni_bugun: List[Dict[str, str]] = []
    yeni_dun: List[Dict[str, str]] = []
    yeni_hafta: List[Dict[str, str]] = []

    if isinstance(yeni_meta, list):
        yeni_hafta = yeni_meta
    elif isinstance(yeni_meta, dict):
        yeni_bugun = list(yeni_meta.get("yeni_bugun") or [])
        yeni_dun = list(yeni_meta.get("yeni_dun") or [])
        yeni_hafta = list(yeni_meta.get("yeni_bu_hafta") or [])

    n = len(veriler)
    subject = build_subject(yeni_bugun, yeni_dun)

    lines = [
        f"OKAS: {okas}",
        f"Konu özeti: {subject}",
        (
            "Not: 'Yeni' ifadesi ihale tarihini degil, ilanin EKAP'ta yayimlandigi gunu anlatir. "
            "Bu yuzden ihale tarihi daha ileri bir tarih olabilir."
        ),
        (
            "Takvim: HTML tablosundaki 'Takvime Ekle' dugmesi Google Takvim sablonunu acar; "
            "etkinlik ihaleden 1 hafta once hatirlatici olarak eklenir."
        ),
        (
            f"Bugün yeni: {len(yeni_bugun)} | Önceki gün yeni: {len(yeni_dun)} | "
            f"Bu hafta yeni: {len(yeni_hafta)} | Açık liste: {n}"
        ),
        "",
        "=== BUGÜN YAYIMLANAN İLANLAR ===",
    ]
    if yeni_bugun:
        for v in yeni_bugun:
            lines.append(
                f"- {v.get('İKN')} | {v.get('İşin Adı')} | ihale: {v.get('İhale Tarihi')} | {v.get('Link')}"
                f" | takvim: {google_calendar_template_url(v)}"
            )
    else:
        lines.append("(Yok)")

    lines += ["", "=== ÖNCEKİ GÜN YAYIMLANAN İLANLAR ==="]
    if yeni_dun:
        for v in yeni_dun:
            lines.append(
                f"- {v.get('İKN')} | {v.get('İşin Adı')} | ihale: {v.get('İhale Tarihi')} | {v.get('Link')}"
                f" | takvim: {google_calendar_template_url(v)}"
            )
    else:
        lines.append("(Yok)")

    lines += ["", "=== BU HAFTA YAYIMLANAN DIGER ILANLAR ==="]
    if yeni_hafta:
        for v in yeni_hafta:
            lines.append(
                f"- {v.get('İKN')} | {v.get('İşin Adı')} | ihale: {v.get('İhale Tarihi')} | {v.get('Link')}"
                f" | takvim: {google_calendar_template_url(v)}"
            )
    else:
        lines.append("(Yok)")

    lines += ["", "=== TÜM TEKLİF VERMEYE AÇIK LİSTE ==="]
    for v in veriler:
        lines.append(
            f"- {v.get('İKN')} | {v.get('İşin Adı')} | {v.get('İhale Tarihi')} | {v.get('Link')}"
            f" | takvim: {google_calendar_template_url(v)}"
        )

    body = "\n".join(lines)

    kutular = ""
    kutular += _yeni_kutu(
        "Bugün yayimlanan ilanlar",
        "Bugün EKAP'ta ilanı yayımlanan kayıtlar. Ihale tarihi daha ileri bir gun olabilir.",
        yeni_bugun,
        "#1F6FEB",
        "#F0F7FF",
        "#1F6FEB",
    )
    kutular += _yeni_kutu(
        "Önceki gün yayimlanan ilanlar",
        "Dün EKAP'ta yayımlanan kayitlar. Sabah cron gecikirse burada gorunebilir.",
        yeni_dun,
        "#B45309",
        "#FFF7ED",
        "#B45309",
    )
    kutular += _yeni_kutu(
        "Bu hafta yayimlanan diger ilanlar",
        "Bu haftada yayimlanmis ama bugun ve onceki gun listelerinde yer almayan kayitlar.",
        yeni_hafta,
        "#047857",
        "#ECFDF5",
        "#047857",
    )

    html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#222">
      <h2 style="margin-bottom:6px">{_esc(subject)}</h2>
      <p style="margin-top:0">
        <b>OKAS:</b> {_esc(okas)} —
        <b>{n}</b> teklif vermeye açık (ihale tarihi ≥ bugün, sansür uygulanmış)
      </p>
      <p style="margin-top:0;color:#444;font-size:13px">
        'Yeni' etiketi ihale tarihini degil, ilanin EKAP'ta yayimlandigi gunu anlatir.
        Bu yuzden ihale tarihi daha ileri bir gunde olabilir.
      </p>
      <p style="margin-top:0;color:#0F766E;font-size:13px">
        Tablodaki <b>📅 Takvime Ekle</b> bağlantısı Google Takvim şablonunu açar;
        etkinlik ihaleden <b>1 hafta önce</b> hatırlatıcı olarak eklenir.
      </p>
      {kutular}
      <h3 style="margin:18px 0 8px 0">Tüm teklif vermeye açık liste</h3>
      <table border="1" cellpadding="6" cellspacing="0"
             style="border-collapse:collapse;font-size:13px">
        <thead style="background:#2C3E50;color:#fff">
          <tr>
            <th>İKN</th><th>İşin Adı</th><th>Kurum</th><th>İhale Tarihi</th>
            <th>İl</th><th>Durum</th><th>Link</th><th>Takvim</th>
          </tr>
        </thead>
        <tbody>
          {_tablo_satirlari(veriler) if veriler else '<tr><td colspan="8">Kayıt yok</td></tr>'}
        </tbody>
      </table>
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
            "Bu mail, cron'un çalıştığını ama EKAP API'nin cevap vermediğini bildirir.",
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
