import tkinter as tk
from tkinter import messagebox
from tkinter import ttk
import threading
import webbrowser
from datetime import datetime
import re

from bot_runner import ekap_botunu_calistir

class EkapBotApp:
    def __init__(self, root):
        self.root = root
        self.root.title("EKAP Bot Yöneticisi")
        self.root.geometry("450x380")
        self.root.resizable(False, False)
        self.root.config(padx=20, pady=20)
        self.arayuzu_olustur()

    def arayuzu_olustur(self):
        lbl_baslik = tk.Label(self.root, text="EKAP Veri Çekme Botu", font=("Arial", 14, "bold"))
        lbl_baslik.pack(pady=(0, 15))

        tk.Label(self.root, text="OKAS Kodu (Örn: 48000000):", font=("Arial", 10, "bold")).pack(anchor="w")
        self.entry_okas = tk.Entry(self.root, width=48)
        self.entry_okas.insert(0, "48000000")
        self.entry_okas.pack(pady=(0, 10))

        tk.Label(self.root, text="İstenmeyen Kelimeler (Virgülle ayırın, örn: lisans,araba):", font=("Arial", 9, "bold")).pack(anchor="w")
        self.entry_haric = tk.Entry(self.root, width=48)
        self.entry_haric.insert(0, "lisans, araba")
        self.entry_haric.pack(pady=(0, 10))

        tk.Label(self.root, text="Taranacak Sayfa Sayısı:", font=("Arial", 10, "bold")).pack(anchor="w")
        self.entry_limit = tk.Entry(self.root, width=48)
        self.entry_limit.insert(0, "10")
        self.entry_limit.pack(pady=(0, 5))

        self.var_tum_sayfalar = tk.BooleanVar(value=False)
        self.chk_tum_sayfalar = tk.Checkbutton(self.root, text="Son sayfaya kadar git (Sınırsız tara)", 
                                               variable=self.var_tum_sayfalar, 
                                               font=("Arial", 9, "italic"),
                                               command=self.tum_sayfalar_degisti)
        self.chk_tum_sayfalar.pack(anchor="w", pady=(0, 15))

        self.btn_baslat = tk.Button(self.root, text="🚀 Botu Başlat", font=("Arial", 12, "bold"), 
                                    bg="#4CAF50", fg="white", height=2, command=self.baslat_tiklandi)
        self.btn_baslat.pack(fill="x")

    def tum_sayfalar_degisti(self):
        if self.var_tum_sayfalar.get():
            self.entry_limit.config(state="disabled")
        else:
            self.entry_limit.config(state="normal")

    def baslat_tiklandi(self):
        okas = self.entry_okas.get()
        durum = "Teklif Vermeye Açık" 
        haric = self.entry_haric.get()
        
        if self.var_tum_sayfalar.get():
            limit = 0 
        else:
            try:
                limit = int(self.entry_limit.get())
            except ValueError:
                messagebox.showwarning("Uyarı", "Sayfa sayısı sadece rakam olmalıdır!")
                return
            
        self.btn_baslat.config(state=tk.DISABLED, text="⏳ Bot Çalışıyor...")
        threading.Thread(target=self._botu_arka_planda_calistir, args=(okas, durum, haric, limit), daemon=True).start()

    def _botu_arka_planda_calistir(self, okas, durum, haric, limit):
        try:
            veriler, dosya = ekap_botunu_calistir(okas, durum, haric, limit)
            self.root.after(0, self.islem_basarili, veriler, dosya)
        except Exception as e:
            self.root.after(0, self.islem_hatali, str(e))

    def islem_basarili(self, veriler, dosya_adi):
        self.btn_baslat.config(state=tk.NORMAL, text="🚀 Botu Başlat")
        messagebox.showinfo("İşlem Tamam", f"Harika! İşlem bitti.\nTarihe göre sıralı veriler '{dosya_adi}' dosyasına kaydedildi.")
        self.sonuclari_goster(veriler)

    def islem_hatali(self, hata_mesaji):
        self.btn_baslat.config(state=tk.NORMAL, text="🚀 Botu Başlat")
        messagebox.showerror("Hata", f"Bot çalışırken bir hata oluştu:\n{hata_mesaji}")

    def sonuclari_goster(self, veriler):
        if not veriler:
            return

        temiz_veriler = []
        for satir in veriler:
            if not any(satir.values()): continue

            kurum_adi = satir.get("İhaleyi Veren Kurum", "").strip()
            detay_metni = satir.get("İhale Detayları", "").strip()

            if detay_metni and "İKN" not in satir:
                ikn_eslesme = re.search(r"(\d{4}/\d+)", detay_metni)
                ikn = ikn_eslesme.group(1) if ikn_eslesme else "-"

                tarih_eslesme = re.search(r"(\d{2}\.\d{2}\.\d{4}\s\d{2}:\d{2})", detay_metni)
                tarih = tarih_eslesme.group(1) if tarih_eslesme else ""

                sehir_eslesme = re.search(r"([A-ZÇĞİÖŞÜa-zçğıöşü]+),\s*\d{2}\.\d{2}\.\d{4}", detay_metni)
                sehir = sehir_eslesme.group(1) if sehir_eslesme else "-"

                durum = "Teklif Vermeye Açık" if "Teklif Vermeye Açık" in detay_metni or "Katılıma Açık" in detay_metni else "Sonuçlandı"
                tur = "Mal" if " Mal " in detay_metni or "Mal Alımı" in detay_metni else ("Hizmet" if " Hizmet " in detay_metni or "Hizmet Alımı" in detay_metni else ("Yapım" if " Yapım " in detay_metni or "Yapım İşi" in detay_metni else "-"))

                isin_adi_raw = detay_metni
                
                if tarih: isin_adi_raw = isin_adi_raw.replace(tarih, "")
                if ikn != "-": isin_adi_raw = isin_adi_raw.replace(ikn, "")
                if sehir != "-": isin_adi_raw = isin_adi_raw.replace(sehir, "")
                if kurum_adi: isin_adi_raw = re.sub(re.escape(kurum_adi), "", isin_adi_raw, flags=re.IGNORECASE)

                for etiket in ["Değerlendirmesi Devam Eden İhaleler", "Sonuçlanmış İhaleler", "İptal Edilen İhaleler", "Teklif Vermeye Açık İhaleler", "Teklif Vermeye Açık", "Katılıma Açık", "Mal Alımı", "Hizmet Alımı", "Yapım İşi", "İhale Kayıt No:", "İhale Tarihi:", "Belli İstekliler Arasında", "Doğrudan Temin"]:
                    isin_adi_raw = re.sub(re.escape(etiket), "", isin_adi_raw, flags=re.IGNORECASE)

                kelimeler = ["Mal", "Hizmet", "Yapım", "Açık", "Pazarlık", "Teklif", "İstisna"]
                for k in kelimeler:
                    pattern = r"(?<!\w)" + k + r"(?!\w)"
                    isin_adi_raw = re.sub(pattern, "", isin_adi_raw, flags=re.IGNORECASE)

                isin_adi = " ".join(isin_adi_raw.split()).strip(" -,:/|")

                if not isin_adi or len(isin_adi) < 3:
                    fallback = detay_metni
                    if ikn != "-": fallback = fallback.replace(ikn, "")
                    if tarih: fallback = fallback.replace(tarih, "")
                    isin_adi = " ".join(fallback.split()).strip(" |")

                temiz_satir = {
                    "Kurum": kurum_adi,
                    "İşin Adı": isin_adi,
                    "İKN": ikn,
                    "İhale Tarihi": tarih,
                    "Tür": tur,
                    "İl": sehir,
                    "Durum": durum
                }
                temiz_veriler.append(temiz_satir)
            else:
                temiz_veriler.append(satir)

        veriler = temiz_veriler

        def tarih_cevir(s):
            tarih_metni = s.get("İhale Tarihi", "")
            try:
                return datetime.strptime(tarih_metni, "%d.%m.%Y %H:%M")
            except ValueError:
                return datetime.max

        veriler = sorted(veriler, key=tarih_cevir, reverse=False)

        sonuc_penceresi = tk.Toplevel(self.root)
        sonuc_penceresi.title("🔍 Çekilen İhale Sonuçları")
        sonuc_penceresi.geometry("1450x700")

        sonuc_penceresi.lift() 
        sonuc_penceresi.attributes('-topmost', True) 
        self.root.after(100, lambda: sonuc_penceresi.attributes('-topmost', False)) 
        sonuc_penceresi.focus_force() 

        tablo_frame = tk.Frame(sonuc_penceresi)
        tablo_frame.pack(expand=True, fill="both", padx=15, pady=15)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Custom.Treeview.Heading", font=("Arial", 11, "bold"), background="#2C3E50", foreground="white", relief="raised", borderwidth=1)
        style.map("Custom.Treeview.Heading", background=[('active', '#34495E')])
        style.configure("Custom.Treeview", font=("Arial", 10), rowheight=40, background="#FFFFFF", fieldbackground="#FFFFFF", borderwidth=1, relief="solid")

        sutunlar = list(veriler[0].keys())
        
        # --- EFSANE AYNA YANSIMASI (KURUM SOLDAN BAŞLAR) ---
        istenen_sira = ("Kurum", "İşin Adı", "İKN", "İhale Tarihi", "Tür", "İl", "Durum")
        
        tablo = ttk.Treeview(tablo_frame, columns=sutunlar, displaycolumns=istenen_sira, show="headings", style="Custom.Treeview")
        
        for sutun in sutunlar:
            tablo.heading(sutun, text=sutun.upper())
            if sutun == "Kurum":
                tablo.column(sutun, width=300, anchor="w")
            elif sutun == "İşin Adı":
                tablo.column(sutun, width=450, anchor="w")
            elif sutun in ["İKN", "İhale Tarihi"]:
                tablo.column(sutun, width=130, anchor="center")
            elif sutun in ["Tür", "İl"]:
                tablo.column(sutun, width=100, anchor="center")
            else: # Durum
                tablo.column(sutun, width=130, anchor="center")
        
        tablo.tag_configure("tek_satir", background="#FFFFFF")
        tablo.tag_configure("cift_satir", background="#E8ECEF")
        
        scrollbar_y = ttk.Scrollbar(tablo_frame, orient="vertical", command=tablo.yview)
        tablo.configure(yscrollcommand=scrollbar_y.set)
        scrollbar_y.pack(side="right", fill="y")

        scrollbar_x = ttk.Scrollbar(tablo_frame, orient="horizontal", command=tablo.xview)
        tablo.configure(xscrollcommand=scrollbar_x.set)
        scrollbar_x.pack(side="bottom", fill="x")
        
        tablo.pack(side="left", fill="both", expand=True)
        
        sayac = 0
        for satir in veriler:
            if any(satir.values()):
                tag = "cift_satir" if sayac % 2 == 0 else "tek_satir"
                tablo.insert("", tk.END, values=list(satir.values()), tags=(tag,))
                sayac += 1

        def cift_tiklandi(event):
            secili_item = tablo.selection()
            if secili_item:
                degerler = tablo.item(secili_item[0], "values")
                for deger in degerler:
                    deger_str = str(deger)
                    eslesme = re.search(r"(\d{4}/\d+)", deger_str)
                    if eslesme:
                        ham_ikn = eslesme.group(1) 
                        formatli_ikn = ham_ikn.replace("/", "_") 
                        hedef_url = f"https://ekapv2.kik.gov.tr/ekap/search/{formatli_ikn}"
                        webbrowser.open(hedef_url)
                        break 

        tablo.bind("<Double-1>", cift_tiklandi)

if __name__ == "__main__":
    root = tk.Tk()
    app = EkapBotApp(root)
    root.mainloop()