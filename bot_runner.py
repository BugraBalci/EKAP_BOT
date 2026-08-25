import csv
import os
import time
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait

from browser_utils import tarayiciyi_baslat
from data_scraper import verileri_cek
from ekap_actions import (
    _arama_formu_hazir,
    arama_yap_ve_gosterimi_ayarla,
    durum_sec,
    ogretici_kapat,
    okas_kodu_sec,
)

ROOT = Path(__file__).resolve().parent
EKAP_URL = "https://ekapv2.kik.gov.tr/ekap/search"
DEFAULT_SELENIUM_SAYFA = 2
MAX_SELENIUM_SAYFA = 3


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


def _sayfa_limiti(limit) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = DEFAULT_SELENIUM_SAYFA
    if n <= 0:
        return DEFAULT_SELENIUM_SAYFA
    return min(n, MAX_SELENIUM_SAYFA)


def _kayit_anahtari(kayit: Dict[str, str]) -> str:
    ikn = (kayit.get("İKN") or "").strip()
    if ikn and ikn != "-":
        return f"ikn:{ikn}"
    kurum = (kayit.get("Kurum") or kayit.get("İhaleyi Veren Kurum") or "").strip()
    ad = (kayit.get("İşin Adı") or kayit.get("İhale Detayları") or "").strip()
    return f"kurum:{kurum}|ad:{ad}"


def _selenium_satir(kayit: Dict[str, str]) -> Dict[str, str]:
    """Kart satırını özet mailinin beklediği sütunlara yaklaştır."""
    out = dict(kayit)
    il_saat = (out.get("İl / Saat") or "").strip()
    if not (out.get("İhale Tarihi") or "").strip():
        out["İhale Tarihi"] = il_saat
    if not (out.get("İl") or "").strip() and " / " in il_saat:
        out["İl"] = il_saat.split(" / ", 1)[0].strip()
    if not (out.get("Durum") or "").strip():
        out["Durum"] = "Teklif Vermeye Açık"
    return out


def _sayfaya_git(driver, url: str) -> None:
    try:
        driver.get(url)
    except TimeoutException:
        print("⚠️ Sayfa yükleme zaman aşımı; mevcut DOM ile devam ediliyor.")
    try:
        WebDriverWait(driver, 8).until(
            lambda d: d.execute_script("return document.readyState")
            in ("interactive", "complete")
        )
    except TimeoutException:
        print("⚠️ document.readyState beklenirken zaman aşımı; devam ediliyor.")


def selenium_tara(
    okas_kodlari: Sequence[str], haric: str, limit: int, durum: str = "Teklif Vermeye Açık"
) -> Tuple[List[Dict[str, str]], List[str]]:
    """Headless Chrome ile her OKAS kodunu ayrı tarar; İKN birleşim kümesi döner."""
    driver = None
    toplanan: List[Dict[str, str]] = []
    gorulen = set()
    hatalar: List[str] = []
    sayfa = _sayfa_limiti(limit)

    try:
        driver, wait = tarayiciyi_baslat()
        form_hazir = False
        for deneme in range(1, 4):
            _sayfaya_git(driver, EKAP_URL)
            time.sleep(2)
            try:
                _arama_formu_hazir(driver, timeout=40)
                form_hazir = True
                break
            except Exception as e:
                print(f"⚠️ Arama formu yüklenmedi (deneme {deneme}/3): {e}")
                if deneme < 3:
                    print("🔄 EKAP sayfası yenileniyor...")
        if not form_hazir:
            raise RuntimeError("EKAP arama formu 3 denemede yüklenmedi.")

        for index, okas in enumerate(okas_kodlari, start=1):
            print(f"\n🔎 [{index}/{len(okas_kodlari)}] OKAS {okas} taranıyor...")
            try:
                ogretici_kapat(driver, wait)
                durum_sec(driver, wait, durum)
                okas_kodu_sec(driver, wait, okas)
                time.sleep(1.5)
                arama_yap_ve_gosterimi_ayarla(driver, wait, gosterim_sayisi="50")
                kayitlar = verileri_cek(
                    driver, wait, maksimum_sayfa=sayfa, dislanacak_kelime=haric
                )
                eklenen = 0
                for kayit in kayitlar:
                    kayit = _selenium_satir(kayit)
                    anahtar = _kayit_anahtari(kayit)
                    if not anahtar or anahtar in gorulen:
                        continue
                    gorulen.add(anahtar)
                    toplanan.append(kayit)
                    eklenen += 1
                print(
                    f"✅ OKAS {okas}: {len(kayitlar)} kayıt, {eklenen} yeni "
                    f"(toplam benzersiz: {len(toplanan)})"
                )
            except Exception as e:
                try:
                    driver.save_screenshot("ekap_hata_ekrani.png")
                    src = driver.page_source or ""
                    print("💾 Hata ekranı: ekap_hata_ekrani.png")
                    print(
                        f"   url={driver.current_url} title={driver.title!r} html_len={len(src)}"
                    )
                    Path("ekap_hata_ekrani.html").write_text(src[:80000], encoding="utf-8")
                except Exception:
                    pass
                msg = f"{okas}: {type(e).__name__}: {e}"
                print(f"⚠️ OKAS {okas} sırasında hata, bir sonrakine geçiliyor: {e}")
                hatalar.append(msg)
            finally:
                if index < len(okas_kodlari):
                    print("🔄 Sayfa sıfırlanıyor...")
                    try:
                        _sayfaya_git(driver, EKAP_URL)
                        time.sleep(1.5)
                    except Exception as reset_e:
                        print(f"⚠️ Sayfa sıfırlama atlandı: {reset_e}")
    finally:
        if driver is not None:
            driver.quit()

    return toplanan, hatalar


def ekap_botunu_calistir(okas, durum, haric_kelime, limit):
    """EKAP v2 araması — API 401 verdiği için Selenium (headless Chrome).

    Returns: (tenders, csv_path, meta)
    """
    kayit_dosyasi = "ekap_arayuz_sonuclar.csv"
    roots = _okas_listesi(str(okas))
    if not roots:
        raise RuntimeError("OKAS kodu boş.")

    sayfa = _sayfa_limiti(limit)
    durum = durum or "Teklif Vermeye Açık"
    print("🔎 EKAP taraması başlıyor (Selenium / headless Chrome)...")
    print(
        f"   OKAS={', '.join(roots)} | durum={durum} | "
        f"sayfa_limiti={sayfa} (API 401, tarayıcı yolu)"
    )

    toplanan, hatalar = selenium_tara(
        roots, haric_kelime or "", sayfa, durum=durum
    )
    if hatalar and not toplanan:
        raise RuntimeError(
            "Selenium taraması sonuç vermedi.\n" + "\n".join(hatalar)
        )
    if hatalar:
        print("⚠️ Bazı OKAS kodları atlandı:\n" + "\n".join(f"- {x}" for x in hatalar))

    verileri_kaydet(toplanan, dosya_adi=kayit_dosyasi)
    meta = {
        "yeni_bu_hafta": [],
        "yeni_bugun": [],
        "yeni_dun": [],
        "kod_hatalari": hatalar,
    }
    return toplanan, kayit_dosyasi, meta
