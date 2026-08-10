import time
from selenium.webdriver.support.ui import WebDriverWait

from browser_utils import tarayiciyi_baslat
from ekap_actions import (
    ogretici_kapat, 
    okas_kodu_sec, 
    arama_yap_ve_gosterimi_ayarla
)
from data_scraper import verileri_cek, verileri_kaydet

def ekap_botunu_calistir(okas, durum, haric_kelime, limit):
    hedef_url = "https://ekapv2.kik.gov.tr/ekap/search"
    kayit_dosyasi = "ekap_arayuz_sonuclar.csv"
    
    driver, wait = tarayiciyi_baslat()
    
    try:
        driver.get(hedef_url)
        WebDriverWait(driver, 25).until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(3)
        
        ogretici_kapat(driver, wait)
        okas_kodu_sec(driver, wait, okas) 
        
        print("⏳ EKAP sisteminin OKAS kodunu algılaması bekleniyor...")
        time.sleep(3)
        
        arama_yap_ve_gosterimi_ayarla(driver, wait, gosterim_sayisi="50")
        
        # Sonuçların tamamen ekrana dökülmesi için güvenli bekleme
        time.sleep(5)
        
        toplanan_veriler = verileri_cek(driver, wait, limit, dislanacak_kelime=haric_kelime)
        verileri_kaydet(toplanan_veriler, dosya_adi=kayit_dosyasi)
        
        return toplanan_veriler, kayit_dosyasi
        
    finally:
        driver.quit()