"""EntropyWork MSA email provider — API key asla koda yazılmaz, .env'den okunur."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent
DEFAULT_URL = "https://msa.entropywork.com/api/send-email"


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


def send_email(
    to: str,
    subject: str,
    body: str,
    html: Optional[str] = None,
) -> Dict[str, Any]:
    to = (to or "").strip()
    if not to or "@" not in to:
        raise ValueError("Geçerli bir alıcı e-posta adresi gerekli.")

    payload: Dict[str, Any] = {
        "to": to,
        "subject": subject,
        "body": body,
    }
    if html:
        payload["html"] = html

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
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(raw) if raw else {"ok": True, "status": resp.status}
            except json.JSONDecodeError:
                return {"ok": True, "status": resp.status, "raw": raw}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
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
            "</tr>"
        )
    return "".join(rows)


def ihale_sonuclarini_maile_cevir(
    veriler: List[Dict[str, str]],
    okas: str,
    yeni_bu_hafta: Optional[List[Dict[str, str]]] = None,
) -> tuple[str, str, str]:
    yeni_bu_hafta = yeni_bu_hafta or []
    n = len(veriler)
    n_yeni = len(yeni_bu_hafta)
    subject = f"EKAP İhale Özeti — OKAS {okas} ({n} açık, {n_yeni} bu hafta yeni)"

    lines = [
        f"OKAS: {okas}",
        f"Bu hafta yeni çıkan: {n_yeni}",
        f"Teklif vermeye açık (ihale tarihi ≥ bugün, filtrelenmiş): {n}",
        "",
        "=== BU HAFTA YENİ ÇIKAN İHALELER ===",
    ]
    if yeni_bu_hafta:
        for v in yeni_bu_hafta:
            lines.append(
                f"- {v.get('İKN', '-')} | {v.get('İşin Adı', '-')} | "
                f"{v.get('Kurum', '-')} | {v.get('Link', '')}"
            )
    else:
        lines.append("(Bu hafta yeni ilan yok)")

    lines += ["", "=== TÜM TEKLİF VERMEYE AÇIK LİSTE ==="]
    for v in veriler:
        lines.append(
            f"- {v.get('İKN', '-')} | {v.get('İşin Adı', '-')} | "
            f"{v.get('Kurum', '-')} | {v.get('İhale Tarihi', '-')} | {v.get('Link', '')}"
        )

    body = "\n".join(lines)

    yeni_box = f"""
    <div style="border:2px solid #1F6FEB;border-radius:8px;padding:14px 16px;margin:0 0 22px 0;
                background:#F0F7FF;font-family:Arial,sans-serif">
      <div style="font-size:16px;font-weight:bold;color:#0B3D91;margin-bottom:8px">
        🆕 Bu hafta yeni çıkan ihaleler
      </div>
      <p style="margin:0 0 10px 0;color:#333;font-size:13px">
        Bu hafta ilanı yayımlanan, teklif vermeye açık kayıtlar ({n_yeni} adet).
      </p>
      <table border="1" cellpadding="6" cellspacing="0"
             style="border-collapse:collapse;width:100%;font-size:13px;background:#fff">
        <thead style="background:#1F6FEB;color:#fff">
          <tr>
            <th>İKN</th><th>İşin Adı</th><th>Kurum</th><th>İhale Tarihi</th>
            <th>İl</th><th>Durum</th><th>Link</th>
          </tr>
        </thead>
        <tbody>
          {_tablo_satirlari(yeni_bu_hafta) if yeni_bu_hafta else '<tr><td colspan="7">Bu hafta yeni ilan yok</td></tr>'}
        </tbody>
      </table>
    </div>
    """

    html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#222">
      <h2 style="margin-bottom:6px">EKAP İhale Sonuçları</h2>
      <p style="margin-top:0">
        <b>OKAS:</b> {_esc(okas)} —
        <b>{n}</b> teklif vermeye açık (ihale tarihi ≥ bugün, sansür uygulanmış)
      </p>
      {yeni_box}
      <h3 style="margin:18px 0 8px 0">Tüm teklif vermeye açık liste</h3>
      <table border="1" cellpadding="6" cellspacing="0"
             style="border-collapse:collapse;font-size:13px">
        <thead style="background:#2C3E50;color:#fff">
          <tr>
            <th>İKN</th><th>İşin Adı</th><th>Kurum</th><th>İhale Tarihi</th>
            <th>İl</th><th>Durum</th><th>Link</th>
          </tr>
        </thead>
        <tbody>
          {_tablo_satirlari(veriler) if veriler else '<tr><td colspan="7">Kayıt yok</td></tr>'}
        </tbody>
      </table>
    </body></html>
    """
    return subject, body, html


def sonuclari_email_gonder(
    to: str,
    veriler: List[Dict[str, str]],
    okas: str,
    yeni_bu_hafta: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    subject, body, html = ihale_sonuclarini_maile_cevir(veriler, okas, yeni_bu_hafta)
    return send_email(to=to, subject=subject, body=body, html=html)
