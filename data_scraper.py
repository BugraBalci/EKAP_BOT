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


def verileri_cek(driver, wait, maksimum_sayfa, dislanacak_kelime="lisans"):
    ihale_verileri = []
    sayfa_sayaci = 1
    toplam_eklenen = 0
    toplam_atlanan = 0

    kelime_listesi = [tr_lower(k.strip()) for k in dislanacak_kelime.split(",") if k.strip()]
    print(f"📊 Veri çekme başladı. Kesin olarak elenecek kelimeler: {kelime_listesi}")

    while True:
        if maksimum_sayfa and maksimum_sayfa > 0 and sayfa_sayaci > maksimum_sayfa:
            print(f"🛑 Belirtilen sayfa limitine ({maksimum_sayfa}) ulaşıldı.")
            break

        print(f"📄 {sayfa_sayaci}. sayfanın verileri çekiliyor...")
        time.sleep(3)

        try:
            ihale_kartlari = driver.find_elements(
                By.XPATH, "//ihale-liste-item | //div[contains(@class, 'pc-card')]"
            )

            if not ihale_kartlari:
                print(f"⚠️ {sayfa_sayaci}. sayfada hiç ihale kartı bulunamadı.")
                break

            for kart in ihale_kartlari:
                try:
                    idare_elem = kart.find_elements(By.CSS_SELECTOR, ".idare")
                    ihale_elem = kart.find_elements(By.CSS_SELECTOR, ".ihale")
                    ikn_elem = kart.find_elements(By.CSS_SELECTOR, ".ikn")
                    ilsaat_elem = kart.find_elements(By.CSS_SELECTOR, ".il-saat")

                    kurum = idare_elem[0].text.strip() if idare_elem else "-"
                    ihale_adi = ihale_elem[0].text.strip() if ihale_elem else ""
                    ikn = ikn_elem[0].text.strip() if ikn_elem else ""
                    il_saat = ilsaat_elem[0].text.strip() if ilsaat_elem else ""

                    metin = f"{kurum} {ihale_adi} {ikn} {il_saat}".strip()
                except Exception:
                    metin = kart.text.strip()
                    kurum = "-"
                    ihale_adi = metin
                    ikn = ""
                    il_saat = ""

                if not metin:
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
                        "Link": _ihale_link(ikn),
                        "İhaleyi Veren Kurum": kurum,
                        "İhale Detayları": detay,
                    }
                )
                toplam_eklenen += 1

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
                sayfa_sayaci += 1
                time.sleep(4)
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
