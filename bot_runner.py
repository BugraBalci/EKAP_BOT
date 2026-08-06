import time
from selenium.webdriver.support.ui import WebDriverWait

# Kendi yazdığın arka plan modülleri
from browser_utils import tarayiciyi_baslat
from ekap_actions import (
    ogretici_kapat, 
    okas_kodu_sec, 
    ihale_durumu_sec, 
    arama_yap_ve_gosterimi_ayarla
)
from data_scraper import verileri_cek, verileri_kaydet

def ekap_botunu_calistir(okas, durum, haric_kelime, limit):
    """Arayüzden gelen verilerle botu çalıştırıp, toplanan verileri geri döndürür."""
    hedef_url = "https://ekapv2.kik.gov.tr/ekap/search"
    kayit_dosyasi = "ekap_arayuz_sonuclar.csv"
    
    driver, wait = tarayiciyi_baslat()
    
    try:
        driver.get(hedef_url)
        WebDriverWait(driver, 25).until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(3)
        
        ogretici_kapat(driver, wait)
        okas_kodu_sec(driver, wait, okas) 
        ihale_durumu_sec(driver, wait, durum)
        arama_yap_ve_gosterimi_ayarla(driver, wait, gosterim_sayisi="50")
        
        toplanan_veriler = verileri_cek(driver, wait, limit, dislanacak_kelime=haric_kelime)
        verileri_kaydet(toplanan_veriler, dosya_adi=kayit_dosyasi)
        
        return toplanan_veriler, kayit_dosyasi
        
    finally:
        driver.quit()