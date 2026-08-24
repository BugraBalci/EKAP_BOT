import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys


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
        pass


def okas_kodu_sec(driver, wait, okas_kodu):
    print(f"📂 'OKAS Kodu Seç' menüsü açılıyor ve '{okas_kodu}' aranıyor...")
    try:
        okas_modal_btn = wait.until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//div[contains(@class, 'dx-button-content') and contains(text(), 'OKAS Kodu Seç')]",
                )
            )
        )
        driver.execute_script("arguments[0].click();", okas_modal_btn)
        time.sleep(3)

        arama_kutusu = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "//input[@aria-label='Search in the tree list']")
            )
        )
        driver.execute_script("arguments[0].focus();", arama_kutusu)
        time.sleep(1)

        arama_kutusu.send_keys(Keys.CONTROL, "a")
        time.sleep(0.5)
        arama_kutusu.send_keys(Keys.BACKSPACE)
        time.sleep(0.5)

        arama_kutusu.send_keys(okas_kodu)
        time.sleep(4)

        checkbox = wait.until(
            EC.presence_of_element_located((By.XPATH, "//*[@aria-label='Satırı seç']"))
        )
        driver.execute_script("arguments[0].click();", checkbox)
        time.sleep(1)

        sec_butonu = wait.until(
            EC.presence_of_element_located((By.XPATH, "//dx-button[@aria-label='Kaydet']"))
        )
        driver.execute_script("arguments[0].click();", sec_butonu)
        time.sleep(2)
        print("✅ OKAS kodu başarıyla seçildi!")
    except Exception as e:
        print(f"❌ OKAS kodu seçilirken HATA: {e}")
        raise


def arama_yap_ve_gosterimi_ayarla(driver, wait, gosterim_sayisi="50"):
    print("🔍 'Filtrele' butonuna basılıyor, ihaleler getiriliyor...")
    try:
        filtrele_butonu = wait.until(
            EC.element_to_be_clickable((By.XPATH, "//button[@id='search-ihale']"))
        )
        driver.execute_script("arguments[0].click();", filtrele_butonu)
        time.sleep(6)

        gosterim_kutusu = wait.until(
            EC.element_to_be_clickable((By.XPATH, "//*[@title='Gösterilecek Kayıt Sayısı']"))
        )
        driver.execute_script("arguments[0].click();", gosterim_kutusu)
        time.sleep(1)

        elli_secenegi = wait.until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    f"//div[contains(@class, 'dx-list-item-content') and text()='{gosterim_sayisi}']",
                )
            )
        )
        driver.execute_script("arguments[0].click();", elli_secenegi)
        time.sleep(3)
    except Exception as e:
        print(f"⚠️ Arama veya gösterim ayarında hata: {e}")
