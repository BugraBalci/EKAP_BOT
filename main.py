import tkinter as tk
from tkinter import messagebox
from tkinter import ttk
import threading
import webbrowser
import re

from bot_runner import ekap_botunu_calistir
from email_provider import sonuclari_email_gonder


class EkapBotApp:
    def __init__(self, root):
        self.root = root
        self.root.title("EKAP Bot Yöneticisi")
        self.root.geometry("480x460")
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

        self.var_tum_sayfalar = tk.BooleanVar(value=True)
        self.chk_tum_sayfalar = tk.Checkbutton(
            self.root,
            text="Tüm teklife açık sonuçları getir (OKAS alt kodları dahil)",
            variable=self.var_tum_sayfalar,
            font=("Arial", 9, "italic"),
            command=self.tum_sayfalar_degisti,
        )
        self.chk_tum_sayfalar.pack(anchor="w", pady=(0, 10))
        self.tum_sayfalar_degisti()

        tk.Label(self.root, text="E-posta (sonuçları gönder):", font=("Arial", 10, "bold")).pack(anchor="w")
        self.entry_email = tk.Entry(self.root, width=48)
        self.entry_email.pack(pady=(0, 5))

        self.var_email_gonder = tk.BooleanVar(value=False)
        self.chk_email = tk.Checkbutton(
            self.root,
            text="Arama bitince sonuçları e-posta ile gönder",
            variable=self.var_email_gonder,
            font=("Arial", 9, "italic"),
        )
        self.chk_email.pack(anchor="w", pady=(0, 15))

        self.btn_baslat = tk.Button(
            self.root,
            text="🚀 Botu Başlat",
            font=("Arial", 12, "bold"),
            bg="#4CAF50",
            fg="white",
            height=2,
            command=self.baslat_tiklandi,
        )
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
        email = self.entry_email.get().strip()
        email_gonder = self.var_email_gonder.get()

        if email_gonder and (not email or "@" not in email):
            messagebox.showwarning("Uyarı", "E-posta gönderimi için geçerli bir adres girin.")
            return

        if self.var_tum_sayfalar.get():
            limit = 0
        else:
            try:
                limit = int(self.entry_limit.get())
            except ValueError:
                messagebox.showwarning("Uyarı", "Sayfa sayısı sadece rakam olmalıdır!")
                return

        self.btn_baslat.config(state=tk.DISABLED, text="⏳ Bot Çalışıyor...")
        threading.Thread(
            target=self._botu_arka_planda_calistir,
            args=(okas, durum, haric, limit, email if email_gonder else None),
            daemon=True,
        ).start()

    def _botu_arka_planda_calistir(self, okas, durum, haric, limit, email):
        try:
            veriler, dosya, yeni = ekap_botunu_calistir(okas, durum, haric, limit)
            email_notu = ""
            if email:
                try:
                    sonuclari_email_gonder(email, veriler, okas, yeni_bu_hafta=yeni)
                    email_notu = f"\n📧 Sonuçlar e-posta ile gönderildi: {email}"
                except Exception as mail_hata:
                    email_notu = f"\n⚠️ E-posta gönderilemedi: {mail_hata}"
            self.root.after(0, self.islem_basarili, veriler, dosya, email_notu)
        except Exception as e:
            self.root.after(0, self.islem_hatali, str(e))

    def islem_basarili(self, veriler, dosya_adi, email_notu=""):
        self.btn_baslat.config(state=tk.NORMAL, text="🚀 Botu Başlat")
        messagebox.showinfo(
            "İşlem Tamam",
            f"Harika! İşlem bitti.\n"
            f"{len(veriler)} kayıt '{dosya_adi}' dosyasına kaydedildi."
            f"{email_notu}",
        )
        self.sonuclari_goster(veriler)

    def islem_hatali(self, hata_mesaji):
        self.btn_baslat.config(state=tk.NORMAL, text="🚀 Botu Başlat")
        messagebox.showerror("Hata", f"Bot çalışırken bir hata oluştu:\n{hata_mesaji}")

    def sonuclari_goster(self, veriler):
        if not veriler:
            messagebox.showinfo("Sonuç Yok", "Filtreye uyan ihale bulunamadı.")
            return

        # Sıra: EKAP API / site sırası (ihaleTarihi desc) — yeniden sıralama yok
        sonuc_penceresi = tk.Toplevel(self.root)
        sonuc_penceresi.title("🔍 Çekilen İhale Sonuçları")
        sonuc_penceresi.geometry("1450x700")

        sonuc_penceresi.lift()
        sonuc_penceresi.attributes("-topmost", True)
        self.root.after(100, lambda: sonuc_penceresi.attributes("-topmost", False))
        sonuc_penceresi.focus_force()

        tablo_frame = tk.Frame(sonuc_penceresi)
        tablo_frame.pack(expand=True, fill="both", padx=15, pady=15)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Custom.Treeview.Heading",
            font=("Arial", 11, "bold"),
            background="#2C3E50",
            foreground="white",
            relief="raised",
            borderwidth=1,
        )
        style.map("Custom.Treeview.Heading", background=[("active", "#34495E")])
        style.configure(
            "Custom.Treeview",
            font=("Arial", 10),
            rowheight=40,
            background="#FFFFFF",
            fieldbackground="#FFFFFF",
            borderwidth=1,
            relief="solid",
        )

        istenen_sira = ("Kurum", "İşin Adı", "İKN", "İhale Tarihi", "Tür", "İl", "Durum", "Link")
        tablo = ttk.Treeview(
            tablo_frame,
            columns=istenen_sira,
            displaycolumns=istenen_sira,
            show="headings",
            style="Custom.Treeview",
        )

        for sutun in istenen_sira:
            tablo.heading(sutun, text=sutun.upper())
            if sutun == "Kurum":
                tablo.column(sutun, width=260, anchor="w")
            elif sutun == "İşin Adı":
                tablo.column(sutun, width=380, anchor="w")
            elif sutun in ["İKN", "İhale Tarihi"]:
                tablo.column(sutun, width=120, anchor="center")
            elif sutun in ["Tür", "İl"]:
                tablo.column(sutun, width=90, anchor="center")
            elif sutun == "Link":
                tablo.column(sutun, width=220, anchor="w")
            else:
                tablo.column(sutun, width=160, anchor="center")

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
            degerler = [satir.get(k, "") for k in istenen_sira]
            if any(degerler):
                tag = "cift_satir" if sayac % 2 == 0 else "tek_satir"
                tablo.insert("", tk.END, values=degerler, tags=(tag,))
                sayac += 1

        def cift_tiklandi(event):
            secili_item = tablo.selection()
            if not secili_item:
                return
            degerler = tablo.item(secili_item[0], "values")
            # Önce Link sütunu
            for deger in degerler:
                deger_str = str(deger)
                if deger_str.startswith("http"):
                    webbrowser.open(deger_str)
                    return
            for deger in degerler:
                eslesme = re.search(r"(\d{4}/\d+)", str(deger))
                if eslesme:
                    formatli_ikn = eslesme.group(1).replace("/", "_")
                    webbrowser.open(f"https://ekapv2.kik.gov.tr/ekap/search/{formatli_ikn}")
                    break

        tablo.bind("<Double-1>", cift_tiklandi)


if __name__ == "__main__":
    root = tk.Tk()
    app = EkapBotApp(root)
    root.mainloop()
