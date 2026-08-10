import csv
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UV = Path.home() / ".local" / "bin" / "uv"
VENDOR = ROOT / "vendor" / "ihale-mcp"
SEARCH_SCRIPT = ROOT / "ekap_api_search.py"


def verileri_kaydet(veri_listesi, dosya_adi="ekap_arayuz_sonuclar.csv"):
    if not veri_listesi:
        print("⚠️ Çekilecek hiçbir geçerli veri bulunamadı.")
        return

    alanlar = list(veri_listesi[0].keys())
    with open(dosya_adi, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=alanlar, delimiter=";")
        writer.writeheader()
        writer.writerows(veri_listesi)
    print(f"💾 Veriler '{dosya_adi}' dosyasına kaydedildi.")


def ekap_botunu_calistir(okas, durum, haric_kelime, limit):
    """ihale-mcp EKAPClient ile arar; Selenium kullanmaz.

    Returns: (tenders, csv_path, yeni_bu_hafta)
    """
    kayit_dosyasi = "ekap_arayuz_sonuclar.csv"

    if not VENDOR.exists():
        raise RuntimeError(
            f"vendor/ihale-mcp bulunamadı: {VENDOR}\n"
            "Şunu çalıştır: git clone --depth 1 https://github.com/saidsurucu/ihale-mcp vendor/ihale-mcp"
        )

    uv_bin = str(UV if UV.exists() else "uv")
    cmd = [
        uv_bin,
        "run",
        "--python",
        "3.12",
        "--directory",
        str(VENDOR),
        "python",
        str(SEARCH_SCRIPT),
        "--okas",
        str(okas).strip(),
        "--haric",
        haric_kelime or "",
        "--limit",
        str(limit),
    ]

    print("🔎 EKAP API araması başlıyor (ihale-mcp)...")
    print(f"   OKAS={okas} | açık + ihale tarihi≥bugün | sayfa_limiti={limit}")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(VENDOR) + (
        f":{env['PYTHONPATH']}" if env.get("PYTHONPATH") else ""
    )

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(VENDOR),
        env=env,
    )

    if proc.stderr:
        sys.stderr.write(proc.stderr)
        sys.stderr.flush()

    if proc.returncode != 0:
        raise RuntimeError(
            f"API araması başarısız (exit={proc.returncode}).\n{proc.stderr[-2000:]}"
        )

    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"API çıktısı JSON değil: {e}\nSTDOUT: {proc.stdout[:500]}") from e

    # Geriye dönük: düz liste gelirse
    if isinstance(payload, list):
        toplanan_veriler = payload
        yeni_bu_hafta = []
    elif isinstance(payload, dict):
        toplanan_veriler = payload.get("tenders") or []
        yeni_bu_hafta = payload.get("yeni_bu_hafta") or []
    else:
        raise RuntimeError("API beklenmeyen sonuç döndürdü.")

    _ = durum
    verileri_kaydet(toplanan_veriler, dosya_adi=kayit_dosyasi)
    return toplanan_veriler, kayit_dosyasi, yeni_bu_hafta
