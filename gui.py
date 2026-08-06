import tkinter as tk
from tkinter import messagebox
from tkinter import ttk
import threading
import time
from datetime import datetime
from selenium.webdriver.support.ui import WebDriverWait

# Kendi yazdığın diğer Python dosyalarından gelen fonksiyonlar
from browser_utils import tarayiciyi_baslat
from ekap_actions import (
    ogretici_kapat, 
    okas_kodu_sec, 
    ihale_durumu_sec, 
    arama_yap_ve_gosterimi_ayarla
)
from data_scraper import verileri_cek, verileri_kaydet

# --- İSTEDİĞİN GÖRSEL TASARIMA UYGUN TABLO PENCERESİ ---
def sonuclari_goster(veriler):
    if not veriler:
        return

    # Verileri "İhale Tarihi"ne göre sıralama (En yakın tarih en üstte)
    def tarih_cevir(satir):
        tarih_metni = satir.get("İhale Tarihi", "")
        try:
            # Örnek format: 15.08.2026 14:30 (Kendi sitendeki formata göre ayarlayabilirsin)
            return datetime.strptime(tarih_metni, "%d.%m.%Y %H:%M")
        except ValueError:
            # Eğer tarih formatı bozuksa veya yoksa en sona at
            return datetime.max

    # Listeyi tarihe göre sırala (Ters çevirerek en yakın tarihi öne alıyoruz)
    veriler = sorted(veriler, key=tarih_cevir, reverse=False)

    sonuc_penceresi = tk.Toplevel()
    sonuc_penceresi.title("🔍 Çekilen İhale Sonuçları")
    sonuc_penceresi.geometry("1100x600")

    tablo_frame = tk.Frame(sonuc_penceresi)
    tablo_frame.pack(expand=True, fill="both", padx=15, pady=15)

    # --- TTK STİL AYARLARI (Büyük yazı tipi ve şık başlık) ---
    style = ttk.Style()
    style.theme_use("clam")
    
    # Üst başlık (Heading) tasarımı
    style.configure("Custom.Treeview.Heading", 
                    font=("Arial", 11, "bold"), 
                    background="#333333", 
                    foreground="white", 
                    relief="flat")
    style.map("Custom.Treeview.Heading", background=[('active', '#444444')])
    
    # Satır (Row) tasarımı
    style.configure("Custom.Treeview", 
                    font=("Arial", 10), 
                    rowheight=35, 
                    fieldbackground="#F9F9F9",
                    background="#F9F9F9")

    # Verilerdeki anahtarları (sütun başlıklarını) dinamik al
    sutunlar = list(veriler[0].keys())

    tablo = ttk.Treeview(tablo_frame, columns=sutunlar, show="headings", style="Custom.Treeview")
    
    # Sütunları dinamik olarak oluştur
    for sutun in sutunlar:
        tablo.heading(sutun, text=sutun.upper())
        tablo.column(sutun, width=200, anchor="w")
    
    # Dikey Kaydırma Çubuğu
    scrollbar_y = ttk.Scrollbar(tablo_frame, orient="vertical", command=tablo.yview)
    tablo.configure(yscrollcommand=scrollbar_y.set)
    scrollbar_y.pack(side="right", fill="y")

    # Yatay Kaydırma Çubuğu
    scrollbar_x = ttk.Scrollbar(tablo_frame, orient="horizontal", command=tablo.xview)
    tablo.configure(xscrollcommand=scrollbar_x.set)
    scrollbar_x.pack(side="bottom", fill="x")
    
    tablo.pack(side="left", fill="both", expand=True)
    
    # Sıralanmış verileri tabloya bas
    for satir in veriler:
        # İçi tamamen boş olan hayalet satırları engellemek için kontrol
        if any(satir.values()):
            tablo.insert("", tk.END, values=list(satir.values()))

# --- ARKA PLAN İŞLEMİ ---
def botu_calistir(okas, durum, haric_kelime, limit):
    hedef_url = "https://ekapv2.kik.gov.tr/ekap/search"
    kayit_dosyasi = "ekap_arayuz_sonuclar.csv"
    
    driver, wait = tarayiciyi_baslat()
    
    try:
        driver.get(hedef_url)
        print("✅ Siteye girildi. Sayfanın yüklenmesi bekleniyor...")
        WebDriverWait(driver, 25).until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(3)
        
        ogretici_kapat(driver, wait)
        
        # Arayüzden gelen 'okas' değişkeni doğrudan buraya aktarılıyor, kod artık tamamen dinamik!
        okas_kodu_sec(driver, wait, okas) 
        
        ihale_durumu_sec(driver, wait, durum)
        arama_yap_ve_gosterimi_ayarla(driver, wait, gosterim_sayisi="50")
        
        toplanan_veriler = verileri_cek(driver, wait, limit, dislanacak_kelime=haric_kelime)
        verileri_kaydet(toplanan_veriler, dosya_adi=kayit_dosyasi)
        
        # Sonuçları yeni ekranda göster
        root.after(0, sonuclari_goster, toplanan_veriler)
        root.after(0, lambda: messagebox.showinfo("İşlem Tamam", f"Harika! İşlem bitti.\nTarihe göre sıralı veriler '{kayit_dosyasi}' dosyasına kaydedildi."))
        
    except Exception as e:
        print(f"❌ Kritik bir hata oluştu: {e}")
        root.after(0, lambda: messagebox.showerror("Hata", f"Bot çalışırken bir hata oluştu:\n{e}"))
        
    finally:
        driver.quit()
        root.after(0, lambda: btn_baslat.config(state=tk.NORMAL, text="🚀 Botu Başlat"))

def baslat_tiklandi():
    okas = entry_okas.get()
    durum = entry_durum.get()
    haric_kelime = entry_haric.get()
    
    try:
        limit = int(entry_limit.get())
    except ValueError:
        messagebox.showwarning("Uyarı", "Sayfa sayısı sadece rakam olmalıdır!")
        return
        
    btn_baslat.config(state=tk.DISABLED, text="⏳ Bot Çalışıyor...")
    threading.Thread(target=botu_calistir, args=(okas, durum, haric_kelime, limit), daemon=True).start()

# --- ANA ARAYÜZ TASARIMI ---
root = tk.Tk()
root.title("EKAP Bot Yöneticisi")
root.geometry("400x380")
root.resizable(False, False) 
root.config(padx=20, pady=20) 

lbl_baslik = tk.Label(root, text="EKAP Veri Çekme Botu", font=("Arial", 14, "bold"))
lbl_baslik.pack(pady=(0, 15))

tk.Label(root, text="OKAS Kodu (İstediğini Yazabilirsin):", font=("Arial", 10)).pack(anchor="w")
entry_okas = tk.Entry(root, width=45)
entry_okas.insert(0, "48000000") 
entry_okas.pack(pady=(0, 10))

tk.Label(root, text="İhale Durumu:", font=("Arial", 10)).pack(anchor="w")
entry_durum = tk.Entry(root, width=45)
entry_durum.insert(0, "Teklif Vermeye Açık") 
entry_durum.pack(pady=(0, 10))

tk.Label(root, text="İstenmeyen (Silinecek) Kelime:", font=("Arial", 10)).pack(anchor="w")
entry_haric = tk.Entry(root, width=45)
entry_haric.insert(0, "lisans") 
entry_haric.pack(pady=(0, 10))

tk.Label(root, text="Taranacak Sayfa Sayısı:", font=("Arial", 10)).pack(anchor="w")
entry_limit = tk.Entry(root, width=45)
entry_limit.insert(0, "10") 
entry_limit.pack(pady=(0, 20))

btn_baslat = tk.Button(root, text="🚀 Botu Başlat", font=("Arial", 11, "bold"), bg="#4CAF50", fg="white", height=2, command=baslat_tiklandi)
btn_baslat.pack(fill="x")

root.mainloop()