import csv
import os
import time
from datetime import date, datetime, timedelta
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
from ekap_health import saglik_bekle, saglik_kontrol
from ilan_alaka import kayitlari_suz, ozeti_yazdir
from ilan_fallback import IlanYedekHatasi, okas_ile_tara
from selenium_teshis import spa_hidrasyon_bekle, teshis_ozeti, teshis_raporu

ROOT = Path(__file__).resolve().parent
EKAP_URL = "https://ekapv2.kik.gov.tr/ekap/search"
DEFAULT_SELENIUM_SAYFA = 2
MAX_SELENIUM_SAYFA = 3

# SPA render edilmezse formu beklemenin anlamı yok; erken çık.
SPA_BEKLEME_SN = 20.0
YEDEK_HAT_GUN = 10
YEDEK_HAT_LIMIT = 20


def _bayrak(isim: str, varsayilan: bool = True) -> bool:
    ham = (os.environ.get(isim) or "").strip().lower()
    if not ham:
        return varsayilan
    return ham in ("1", "true", "yes", "evet", "on")


def _sayi(isim: str, varsayilan: float) -> float:
    try:
        return float((os.environ.get(isim) or "").strip())
    except ValueError:
        return varsayilan


def verileri_kaydet(veri_listesi, dosya_adi="ekap_arayuz_sonuclar.csv"):
    if not veri_listesi:
        print("⚠️ Çekilecek hiçbir geçerli veri bulunamadı.")
        return

    alanlar = list(veri_listesi[0].keys())
    for kayit in veri_listesi[1:]:
        for alan in kayit:
            if alan not in alanlar:
                alanlar.append(alan)

    with open(dosya_adi, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=alanlar, delimiter=";", extrasaction="ignore"
        )
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
        teshis_notu = ""
        for deneme in range(1, 4):
            _sayfaya_git(driver, EKAP_URL)
            time.sleep(2)

            # SPA hiç render edilmediyse formu 40 sn beklemek boşa zaman.
            if not spa_hidrasyon_bekle(driver, timeout=SPA_BEKLEME_SN):
                rapor = teshis_raporu(driver, onek="ekap_hata_ekrani")
                teshis_notu = teshis_ozeti(rapor)
                print(f"⚠️ SPA render edilmedi (deneme {deneme}/3): {teshis_notu}")
                if deneme < 3:
                    print("🔄 EKAP sayfası yenileniyor...")
                continue

            try:
                _arama_formu_hazir(driver, timeout=40)
                form_hazir = True
                break
            except Exception as e:
                rapor = teshis_raporu(driver, onek="ekap_hata_ekrani")
                teshis_notu = teshis_ozeti(rapor)
                print(f"⚠️ Arama formu yüklenmedi (deneme {deneme}/3): {e}")
                print(f"   Teşhis: {teshis_notu}")
                if deneme < 3:
                    print("🔄 EKAP sayfası yenileniyor...")
        if not form_hazir:
            mesaj = "EKAP arama formu 3 denemede yüklenmedi."
            if teshis_notu:
                mesaj += f" Teşhis: {teshis_notu}"
            raise RuntimeError(mesaj)

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
                    rapor = teshis_raporu(driver, onek=f"ekap_hata_{okas}")
                    print(f"   Teşhis: {teshis_ozeti(rapor)}")
                except Exception as teshis_e:
                    print(f"⚠️ Teşhis alınamadı: {teshis_e}")
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


def _saglik_kapisi(log=print):
    """Selenium açılmadan önce EKAP'ın ayakta olduğunu doğrular.

    EKAP_SAGLIK_KONTROL=0 ile tamamen kapatılabilir. EKAP_SAGLIK_DENEME>1
    verilirse sağlıklı olana kadar artan aralıklarla beklenir.
    """
    if not _bayrak("EKAP_SAGLIK_KONTROL", True):
        log("ℹ️ Sağlık kontrolü EKAP_SAGLIK_KONTROL=0 ile atlandı.")
        return None

    deneme = int(_sayi("EKAP_SAGLIK_DENEME", 1))
    if deneme > 1:
        return saglik_bekle(
            deneme=deneme,
            bekleme=_sayi("EKAP_SAGLIK_BEKLEME", 60.0),
            log=log,
        )

    rapor = saglik_kontrol()
    rapor.yazdir(log)
    return rapor


def _yedek_hattan_tara(roots, haric_kelime, gerekce: str, log=print):
    """EKAP kullanılamazken ilan.gov.tr üzerinden veri toplar."""
    log(f"🛟 EKAP kullanılamıyor ({gerekce}); ilan.gov.tr yedek hattına geçiliyor.")
    kayitlar = okas_ile_tara(
        roots,
        limit=YEDEK_HAT_LIMIT,
        gun=YEDEK_HAT_GUN,
        dislanacak_kelime=haric_kelime or "",
        log=log,
    )
    temiz, ozet = kayitlari_suz(kayitlar)
    if ozet.elenen:
        log(
            f"🧹 Zorunlu alaka süzgeci: {ozet.elenen} alakasız ilan elendi, "
            f"{len(temiz)} kaldı."
        )
        ozeti_yazdir(ozet, log=log)
    if not temiz:
        raise IlanYedekHatasi(
            "ilan.gov.tr tarandı ama alaka süzgecinden geçen ihale kalmadı."
        )
    log(f"✅ Yedek hat {len(temiz)} ilgili ilan getirdi (kaynak: ilan.gov.tr).")
    return temiz


def _yeni_kayitlari_isaretle(kayitlar, meta) -> None:
    """Bültenin 'bugün/dün yeni' kutularını ilan yayım tarihine göre doldurur.

    Doldurulmazsa mail konusu, liste dolu olsa bile "Bugün 0 ihale, dün 0
    ihale girildi" diyor. Ölçüt ihale tarihi değil yayım tarihi: ihale
    haftalar sonra olabilir.
    """
    bugun = date.today()
    dun = bugun - timedelta(days=1)
    hafta_basi = bugun - timedelta(days=7)

    for kayit in kayitlar:
        ham = (kayit.get("Yayım Tarihi") or "").strip()
        try:
            yayim = datetime.strptime(ham, "%d.%m.%Y").date()
        except ValueError:
            continue
        if yayim == bugun:
            meta["yeni_bugun"].append(kayit)
        elif yayim == dun:
            meta["yeni_dun"].append(kayit)
        elif yayim >= hafta_basi:
            meta["yeni_bu_hafta"].append(kayit)


def ekap_botunu_calistir(okas, durum, haric_kelime, limit):
    """EKAP v2 araması — Selenium ile DOM kazıma (headless Chrome).

    Sıra: sağlık kontrolü → Selenium taraması → ilan.gov.tr yedek hattı.
    EKAP_FALLBACK=0 verilirse yedek hat devreye girmez ve hata yükseltilir.

    Returns: (tenders, csv_path, meta)
    """
    kayit_dosyasi = "ekap_arayuz_sonuclar.csv"
    roots = _okas_listesi(str(okas))
    if not roots:
        raise RuntimeError("OKAS kodu boş.")

    sayfa = _sayfa_limiti(limit)
    durum = durum or "Teklif Vermeye Açık"
    yedek_acik = _bayrak("EKAP_FALLBACK", True)
    meta = {
        "yeni_bu_hafta": [],
        "yeni_bugun": [],
        "yeni_dun": [],
        "kod_hatalari": [],
        "kaynak": "EKAP",
    }

    # EKAP otomasyona "Kısa bir güvenlik kontrolü" doğrulama sayfası gösterdiği
    # sürece Selenium denemesi ~2 dk harcayıp kesin başarısız oluyor.
    # EKAP_SELENIUM=0 ile o denemeyi atlayıp doğrudan yedek hattan üretiriz.
    if not _bayrak("EKAP_SELENIUM", True):
        gerekce = "EKAP_SELENIUM=0 (Selenium denemesi atlandı)"
        print(f"ℹ️ {gerekce}; doğrudan ilan.gov.tr yedek hattı kullanılacak.")
        toplanan = _yedek_hattan_tara(roots, haric_kelime, gerekce)
        meta["kaynak"] = "ilan.gov.tr"
        meta["saglik_ozeti"] = gerekce
        _yeni_kayitlari_isaretle(toplanan, meta)
        verileri_kaydet(toplanan, dosya_adi=kayit_dosyasi)
        return toplanan, kayit_dosyasi, meta

    saglik = _saglik_kapisi()
    if saglik is not None and not saglik.saglikli:
        meta["saglik_ozeti"] = saglik.ozet
        if not yedek_acik:
            raise RuntimeError(f"EKAP sağlık kontrolünden geçemedi: {saglik.ozet}")
        toplanan = _yedek_hattan_tara(roots, haric_kelime, saglik.ozet)
        meta["kaynak"] = "ilan.gov.tr"
        _yeni_kayitlari_isaretle(toplanan, meta)
        verileri_kaydet(toplanan, dosya_adi=kayit_dosyasi)
        return toplanan, kayit_dosyasi, meta

    print("🔎 EKAP taraması başlıyor (Selenium / headless Chrome)...")
    print(f"   OKAS={', '.join(roots)} | durum={durum} | sayfa_limiti={sayfa}")

    try:
        toplanan, hatalar = selenium_tara(
            roots, haric_kelime or "", sayfa, durum=durum
        )
    except Exception as e:
        if not yedek_acik:
            raise
        meta["selenium_hatasi"] = f"{type(e).__name__}: {e}"
        try:
            toplanan = _yedek_hattan_tara(roots, haric_kelime, meta["selenium_hatasi"])
        except IlanYedekHatasi as yedek_e:
            raise RuntimeError(
                f"Selenium taraması başarısız ({e}); yedek hat da veri vermedi: {yedek_e}"
            ) from e
        meta["kaynak"] = "ilan.gov.tr"
        _yeni_kayitlari_isaretle(toplanan, meta)
        verileri_kaydet(toplanan, dosya_adi=kayit_dosyasi)
        return toplanan, kayit_dosyasi, meta

    meta["kod_hatalari"] = hatalar
    if hatalar and not toplanan:
        if not yedek_acik:
            raise RuntimeError(
                "Selenium taraması sonuç vermedi.\n" + "\n".join(hatalar)
            )
        try:
            toplanan = _yedek_hattan_tara(
                roots, haric_kelime, f"{len(hatalar)} OKAS kodu hata verdi"
            )
            meta["kaynak"] = "ilan.gov.tr"
            _yeni_kayitlari_isaretle(toplanan, meta)
        except IlanYedekHatasi as yedek_e:
            raise RuntimeError(
                "Selenium taraması sonuç vermedi ve yedek hat da başarısız:\n"
                + "\n".join(hatalar)
                + f"\n{yedek_e}"
            ) from yedek_e
    elif hatalar:
        print("⚠️ Bazı OKAS kodları atlandı:\n" + "\n".join(f"- {x}" for x in hatalar))

    verileri_kaydet(toplanan, dosya_adi=kayit_dosyasi)
    return toplanan, kayit_dosyasi, meta
