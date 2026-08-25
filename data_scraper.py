import time
import pandas as pd
from selenium.webdriver.common.by import By


def tr_lower(metin):
    if not metin:
        return ""
    metin = (
        metin.replace("I", "ı")
        .replace("İ", "i")
        .replace("Ğ", "ğ")
        .replace("Ü", "ü")
        .replace("Ş", "ş")
        .replace("Ö", "ö")
        .replace("Ç", "ç")
    )
    return metin.lower()


def _ihale_link(ikn):
    ikn = (ikn or "").strip()
    if not ikn or ikn == "-":
        return ""
    return f"https://ekapv2.kik.gov.tr/ekap/search/{ikn.replace('/', '_')}"


def _kartlar(driver):
    kartlar = driver.find_elements(By.XPATH, "//ihale-liste-item")
    if kartlar:
        return kartlar
    return driver.find_elements(By.XPATH, "//div[contains(@class, 'pc-card')]")


def _durum_acik_mi(durum_text, kart_metni=""):
    """Kartın durum etiketi kapalıysa ele. Etiket yoksa UI filtresine güven."""
    _ = kart_metni
    metin = tr_lower((durum_text or "").strip())
    if not metin:
        return True
    kapali = (
        "iptal",
        "sonuçlandı",
        "sonuclandi",
        "sözleşme imzalandı",
        "sozlesme imzalandi",
        "teklifler değerlendiriliyor",
        "teklifler degerlendiriliyor",
        "yayımlanmamış",
        "yayimlanmamis",
    )
    if any(k in metin for k in kapali):
        return False
    return True


def verileri_cek(driver, wait, maksimum_sayfa=2, dislanacak_kelime="lisans"):
    ihale_verileri = []
    toplam_eklenen = 0
    toplam_atlanan = 0
    try:
        maksimum_sayfa = int(maksimum_sayfa) if maksimum_sayfa else 2
    except (TypeError, ValueError):
        maksimum_sayfa = 2
    if maksimum_sayfa <= 0:
        maksimum_sayfa = 2

    kelime_listesi = [tr_lower(k.strip()) for k in dislanacak_kelime.split(",") if k.strip()]
    print(f"📊 Veri çekme başladı. Hedef: En fazla {maksimum_sayfa} sayfa.")
    print(f"📊 Kesin olarak elenecek kelimeler: {kelime_listesi}")

    for sayfa_sayaci in range(1, maksimum_sayfa + 1):
        print(f"📄 {sayfa_sayaci}. sayfanın verileri çekiliyor...")
        time.sleep(1.5)

        try:
            ihale_kartlari = _kartlar(driver)

            if not ihale_kartlari:
                print(f"⚠️ {sayfa_sayaci}. sayfada hiç ihale kartı bulunamadı.")
                break

            for kart in ihale_kartlari:
                try:
                    idare_elem = kart.find_elements(By.CSS_SELECTOR, ".idare")
                    ihale_elem = kart.find_elements(By.CSS_SELECTOR, ".ihale")
                    ikn_elem = kart.find_elements(By.CSS_SELECTOR, ".ikn")
                    ilsaat_elem = kart.find_elements(By.CSS_SELECTOR, ".il-saat")
                    durum_elem = kart.find_elements(
                        By.CSS_SELECTOR, ".durum, .ihale-durum, .status, .ihaleDurum"
                    )

                    kurum = idare_elem[0].text.strip() if idare_elem else "-"
                    ihale_adi = ihale_elem[0].text.strip() if ihale_elem else ""
                    ikn = ikn_elem[0].text.strip() if ikn_elem else ""
                    il_saat = ilsaat_elem[0].text.strip() if ilsaat_elem else ""
                    durum_text = durum_elem[0].text.strip() if durum_elem else ""

                    metin = f"{kurum} {ihale_adi} {ikn} {il_saat}".strip()
                except Exception:
                    metin = kart.text.strip()
                    kurum = "-"
                    ihale_adi = metin
                    ikn = ""
                    il_saat = ""
                    durum_text = ""

                if not metin:
                    continue

                if not _durum_acik_mi(durum_text, metin):
                    toplam_atlanan += 1
                    print(
                        f"🚫 KAPALI: {(ihale_adi or metin)[:80]} | {durum_text or 'kapalı durum'}"
                    )
                    continue

                metin_alt = tr_lower(metin)

                elendi = False
                for kelime in kelime_listesi:
                    if kelime in metin_alt:
                        elendi = True
                        toplam_atlanan += 1
                        print(f"🚫 SANSÜR: İçinde '{kelime}' tespit edildi, ihale çöpe atıldı!")
                        break

                if elendi:
                    continue

                detay = ihale_adi if ihale_adi else metin
                if ikn:
                    detay += f" | {ikn}"
                if il_saat:
                    detay += f" | {il_saat}"

                ihale_verileri.append(
                    {
                        "Kurum": kurum,
                        "İşin Adı": ihale_adi if ihale_adi else metin,
                        "İKN": ikn or "-",
                        "İl / Saat": il_saat,
                        "Durum": durum_text or "Teklif Vermeye Açık",
                        "Link": _ihale_link(ikn),
                        "İhaleyi Veren Kurum": kurum,
                        "İhale Detayları": detay,
                    }
                )
                toplam_eklenen += 1

            if sayfa_sayaci >= maksimum_sayfa:
                print(
                    f"🏁 Sayfa sınırına ({maksimum_sayfa}) ulaşıldı, "
                    "diğer sayfaya geçilmiyor."
                )
                break

            try:
                next_btn = driver.find_element(
                    By.XPATH,
                    "//dx-button[@title='İleri'] | //button[@title='İleri'] | "
                    "//a[contains(@class, 'dx-next-button')] | "
                    "//button[contains(@aria-label, 'Sonraki')]",
                )

                cls = next_btn.get_attribute("class") or ""
                if "dx-state-disabled" in cls or not next_btn.is_enabled():
                    print("🏁 Son sayfaya ulaşıldı.")
                    break

                driver.execute_script("arguments[0].click();", next_btn)
                time.sleep(1.5)
            except Exception:
                print("🏁 Sonraki sayfa butonu bulunamadı, tüm sayfalar tarandı.")
                break

        except Exception as e:
            print(f"⚠️ Sayfa çekilirken hata: {e}")
            break

    print(f"🎉 İşlem bitti! Eklenen İhale: {toplam_eklenen} | Sansürlenip Atılan İhale: {toplam_atlanan}")
    return ihale_verileri


def verileri_kaydet(veri_listesi, dosya_adi="ekap_v2_sonuclar.csv"):
    if len(veri_listesi) > 0:
        df = pd.DataFrame(veri_listesi)
        df.to_csv(dosya_adi, index=False, encoding="utf-8-sig", sep=";")
        print(f"💾 Veriler '{dosya_adi}' dosyasına kaydedildi.")
    else:
        print("⚠️ Çekilecek hiçbir geçerli veri bulunamadı.")
