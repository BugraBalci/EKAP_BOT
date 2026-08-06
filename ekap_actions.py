import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys # KLAVYE KOMUTLARI İÇİN EKLENDİ

def ogretici_kapat(driver, wait):
    print("🔍 Öğretici (Tutorial) penceresi kontrol ediliyor...")
    try:
        kapatma_butonu = wait.until(
            EC.element_to_be_clickable((By.XPATH, "//button[@class='close-btn']"))
        )
        kapatma_butonu.click()
        print("❌ Öğretici penceresi başarıyla kapatıldı!")
        time.sleep(1)
    except Exception:
        print("ℹ️ Öğretici penceresi bulunamadı, devam ediliyor.")

def okas_kodu_sec(driver, wait, okas_kodu):
    print(f"📂 'OKAS Kodu Seç' menüsü açılıyor ve '{okas_kodu}' aranıyor...")
    try:
        # 1. OKAS menüsünü aç
        print("-> Adım 1: OKAS Menü butonu aranıyor...")
        okas_modal_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//div[contains(@class, 'dx-button-content') and contains(text(), 'OKAS Kodu Seç')]")))
        driver.execute_script("arguments[0].click();", okas_modal_btn)
        time.sleep(3) # Menünün animasyonla açılmasını bekle

        # 2. Arama kutusuna OKAS kodunu yaz (SENİN BULDUĞUN YENİ KOD İLE)
        print("-> Adım 2: Arama kutusu aranıyor...")
        arama_kutusu = wait.until(EC.presence_of_element_located((By.XPATH, "//input[@aria-label='Search in the tree list']")))
        
        # Kutuyu zorla aktifleştirip içine yazıyoruz
        driver.execute_script("arguments[0].focus();", arama_kutusu)
        time.sleep(1)
        arama_kutusu.clear()
        arama_kutusu.send_keys(okas_kodu)
        print(f"-> Adım 2 Tamam: Kutuya '{okas_kodu}' yazıldı. Sitenin filtrelemesi bekleniyor...")
        time.sleep(4) # Sistemin arama sonucunu getirmesi için bekle

        # 3. Çıkan sonuca tik at 
        print("-> Adım 3: 'Satırı seç' (Checkbox) aranıyor...")
        checkbox = wait.until(EC.presence_of_element_located((By.XPATH, "//*[@aria-label='Satırı seç']")))
        driver.execute_script("arguments[0].click();", checkbox)
        print("-> Adım 3 Tamam: Tik atıldı.")
        time.sleep(1)

        # 4. Seç (Kaydet) Butonuna bas (SENİN BULDUĞUN KOD İLE GÜÇLENDİRİLDİ)
        print("-> Adım 4: 'Kaydet' butonu aranıyor...")
        sec_butonu = wait.until(EC.presence_of_element_located((By.XPATH, "//dx-button[@aria-label='Kaydet']")))
        driver.execute_script("arguments[0].click();", sec_butonu)
        print("-> Adım 4 Tamam: Seç butonuna basıldı.")
        time.sleep(2)
        
        print("✅ OKAS kodu başarıyla seçildi ve onaylandı!")
        
    except Exception as e:
        print(f"❌ OKAS kodu seçilirken HATA: Hangi adımda patladığını üstteki loglardan kontrol et!")
def ihale_durumu_sec(driver, wait, durum_metni):
    if durum_metni == "Tümü":
        print("📁 İhale Durumu 'Tümü' seçildi, filtreleme yapılmıyor.")
        return

    print(f"📁 İhale Durumu '{durum_metni}' olarak ayarlanıyor...")
    try:
        dropdown_ikon = wait.until(EC.element_to_be_clickable((By.XPATH, "(//div[contains(@class, 'dx-dropdowneditor-icon')])[1]")))
        driver.execute_script("arguments[0].click();", dropdown_ikon)
        time.sleep(2)

        secenek = wait.until(EC.presence_of_element_located((By.XPATH, f"//div[contains(@class, 'dx-list-item-content') and contains(text(), '{durum_metni}')]")))
        driver.execute_script("arguments[0].click();", secenek)
        time.sleep(1)
    except Exception as e:
        print(f"⚠️ İhale durumu seçilirken hata: {e}")

def arama_yap_ve_gosterimi_ayarla(driver, wait, gosterim_sayisi="50"):
    print("🔍 'Filtrele' butonuna basılıyor, ihaleler getiriliyor...")
    try:
        filtrele_butonu = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@id='search-ihale']")))
        driver.execute_script("arguments[0].click();", filtrele_butonu)
        print("⚙️ Arama yapıldı. Sonuçların yüklenmesi bekleniyor...")
        time.sleep(6) 

        print("⚙️ Sayfa görünümü 50'ye ayarlanıyor...")
        gosterim_kutusu = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@title='Gösterilecek Kayıt Sayısı']")))
        driver.execute_script("arguments[0].click();", gosterim_kutusu)
        time.sleep(1)

        elli_secenegi = wait.until(EC.presence_of_element_located((By.XPATH, f"//div[contains(@class, 'dx-list-item-content') and text()='{gosterim_sayisi}']")))
        driver.execute_script("arguments[0].click();", elli_secenegi)
        time.sleep(3)
    except Exception as e:
        print(f"⚠️ Arama veya gösterim ayarında hata: {e}")