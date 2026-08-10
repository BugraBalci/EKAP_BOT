import time
import pandas as pd
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC

# --- TÜRKÇE KARAKTERLERİ HATASIZ KÜÇÜLTME MOTORU ---
def tr_lower(metin):
    if not metin:
        return ""
    metin = metin.replace('I', 'ı').replace('İ', 'i').replace('Ğ', 'ğ').replace('Ü', 'ü').replace('Ş', 'ş').replace('Ö', 'ö').replace('Ç', 'ç')
    return metin.lower()

def verileri_cek(driver, wait, maksimum_sayfa, dislanacak_kelime="lisans"):
    ihale_verileri = []
    sayfa_sayaci = 1
    toplam_eklenen = 0
    toplam_atlanan = 0

    # Kullanıcının girdiği kelimeleri hatasız Türkçe küçük harfe çeviriyoruz
    kelime_listesi = [tr_lower(k.strip()) for k in dislanacak_kelime.split(",") if k.strip()]
    print(f"📊 Veri çekme başladı. Kesin olarak elenecek kelimeler: {kelime_listesi}")

    while True:
        if maksimum_sayfa and maksimum_sayfa > 0 and sayfa_sayaci > maksimum_sayfa:
            print(f"🛑 Belirtilen sayfa limitine ({maksimum_sayfa}) ulaşıldı.")
            break

        print(f"📄 {sayfa_sayaci}. sayfanın verileri çekiliyor...")
        time.sleep(3)

        try:
            # --- YENİ EKAP YAPISI: <ihale-liste-item> veya class="pc-card" ---
            ihale_kartlari = driver.find_elements(By.XPATH, "//ihale-liste-item | //div[contains(@class, 'pc-card')]")

            if not ihale_kartlari:
                print(f"⚠️ {sayfa_sayaci}. sayfada hiç ihale kartı bulunamadı.")
                break

            for kart in ihale_kartlari:
                try:
                    # Yeni Angular yapısındaki özel sınıflardan verileri doğrudan çekiyoruz
                    idare_elem = kart.find_elements(By.CSS_SELECTOR, ".idare")
                    ihale_elem = kart.find_elements(By.CSS_SELECTOR, ".ihale")
                    ikn_elem = kart.find_elements(By.CSS_SELECTOR, ".ikn")
                    ilsaat_elem = kart.find_elements(By.CSS_SELECTOR, ".il-saat")

                    kurum = idare_elem[0].text.strip() if idare_elem else "-"
                    ihale_adi = ihale_elem[0].text.strip() if ihale_elem else ""
                    ikn = ikn_elem[0].text.strip() if ikn_elem else ""
                    il_saat = ilsaat_elem[0].text.strip() if ilsaat_elem else ""

                    # Filtreleme için tüm metni birleştir
                    metin = f"{kurum} {ihale_adi} {ikn} {il_saat}".strip()
                except Exception:
                    metin = kart.text.strip()
                    kurum = "-"
                    ihale_adi = metin
                    ikn = ""
                    il_saat = ""

                if not metin:
                    continue

                # Çekilen ihalenin tüm metnini hatasız Türkçe küçük harfe çevir
                metin_alt = tr_lower(metin)
                
                # --- YENİ KUSURSUZ FİLTRE MANTIĞI ---
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

                ihale_verileri.append({
                    "İhaleyi Veren Kurum": kurum,
                    "İhale Detayları": detay
                })
                toplam_eklenen += 1

            # --- YENİ SAYFALAMA BUTONU SEÇİCİSİ (<dx-button title="İleri">) ---
            try:
                next_btn = driver.find_element(By.XPATH, "//dx-button[@title='İleri'] | //button[@title='İleri'] | //a[contains(@class, 'dx-next-button')] | //button[contains(@aria-label, 'Sonraki')]")
                
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
        df.to_csv(dosya_adi, index=False, encoding='utf-8-sig', sep=';')
        print(f"💾 Veriler '{dosya_adi}' dosyasına kaydedildi.")
    else:
        print("⚠️ Çekilecek hiçbir geçerli veri bulunamadı.")