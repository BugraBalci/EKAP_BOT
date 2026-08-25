import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC


def ogretici_kapat(driver, wait):
    print("🔍 Öğretici (Tutorial) penceresi kontrol ediliyor...")
    try:
        kapatma_butonu = wait.until(
            EC.presence_of_element_located((By.XPATH, "//button[@class='close-btn']"))
        )
        driver.execute_script("arguments[0].click();", kapatma_butonu)
        print("❌ Öğretici penceresi başarıyla kapatıldı!")
        time.sleep(1)
    except Exception:
        pass


def okas_kodu_sec(driver, wait, okas_kodu):
    print(f"📂 'OKAS Kodu Seç' menüsü açılıyor ve '{okas_kodu}' aranıyor...")
    try:
        time.sleep(3)
        okas_modal_btn = wait.until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    "//div[contains(@class, 'dx-button-content') and contains(., 'OKAS Kodu Seç')] "
                    "| //button[contains(., 'OKAS')]",
                )
            )
        )
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});", okas_modal_btn
        )
        time.sleep(1)
        driver.execute_script("arguments[0].click();", okas_modal_btn)
        time.sleep(2)

        arama_kutusu = wait.until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    "//input[@aria-label='Search in the tree list'] "
                    "| //input[@role='textbox']",
                )
            )
        )
        driver.execute_script("arguments[0].focus();", arama_kutusu)
        try:
            arama_kutusu.clear()
        except Exception:
            driver.execute_script("arguments[0].value = '';", arama_kutusu)
        arama_kutusu.send_keys(okas_kodu)
        time.sleep(2)

        checkbox = wait.until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    "//span[contains(@class, 'dx-checkbox-icon')] "
                    "| //div[@role='checkbox'] "
                    "| //*[@aria-label='Satırı seç']",
                )
            )
        )
        driver.execute_script("arguments[0].click();", checkbox)
        time.sleep(1)

        sec_btn = wait.until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    "//div[contains(@class, 'dx-button-content') and contains(., 'Seç')] "
                    "| //div[contains(@class, 'dx-button-content') and contains(., 'Kaydet')] "
                    "| //dx-button[@aria-label='Kaydet'] "
                    "| //p[contains(@class, 'detay-button-text')]",
                )
            )
        )
        driver.execute_script("arguments[0].click();", sec_btn)
        time.sleep(2)
        print(f"✅ OKAS kodu '{okas_kodu}' başarıyla seçildi.")
    except Exception as e:
        print(f"❌ OKAS kodu seçilirken HATA: {e}")
        raise e


def arama_yap_ve_gosterimi_ayarla(driver, wait, gosterim_sayisi="50"):
    print("🔍 'Filtrele' butonuna basılıyor, ihaleler getiriliyor...")
    try:
        filtrele_butonu = wait.until(
            EC.presence_of_element_located((By.XPATH, "//button[@id='search-ihale']"))
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", filtrele_butonu)
        driver.execute_script("arguments[0].click();", filtrele_butonu)
        time.sleep(6)

        gosterim_kutusu = wait.until(
            EC.presence_of_element_located((By.XPATH, "//*[@title='Gösterilecek Kayıt Sayısı']"))
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
