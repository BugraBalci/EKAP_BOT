#!/usr/bin/env python3
"""ilan.gov.tr alaka süzgeci ve tarih aralığı birim testleri (ağ yok)."""

from __future__ import annotations

import csv
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path

from ilan_alaka import (
    MANUEL_CSV_KOLONLAR,
    alaka_degerlendir,
    gecen_hafta_araligi,
    haric_listesini_temizle,
    html_temizle,
    kategori_etiketi,
    kayitlari_suz,
    manuel_satira_cevir,
    meta_listelerini_suz,
    tarih_araligi,
    tarihi_coz,
    terimleri_bul,
    tr_lower,
)
from ilan_fallback import _kayda_cevir
from manual_ilan_check import csv_yaz


class TrLowerTests(unittest.TestCase):
    def test_i_noktali(self):
        self.assertEqual(tr_lower("BİLİŞİM"), "bilişim")
        self.assertEqual(tr_lower("İHALE"), "ihale")
        self.assertEqual(tr_lower("IŞIK"), "ışık")

    def test_s_cedilla(self):
        self.assertEqual(tr_lower("İNŞAAT"), "inşaat")


class TerimEslemeTests(unittest.TestCase):
    def test_ag_saglik_karmasasin(self):
        self.assertEqual(terimleri_bul("sağlık müdürlüğü ihalesi", ["ağ"]), [])
        self.assertEqual(terimleri_bul("yerel ağ kurulumu", ["ağ"]), ["ağ"])

    def test_yazilim_eslesir(self):
        self.assertEqual(
            terimleri_bul("Saha yönetimi yazılımı geliştirme", ["yazılım", "entegrasyon"]),
            ["yazılım"],
        )

    def test_tibbi_sarf_obek(self):
        self.assertEqual(
            terimleri_bul("Tıbbi sarf malzeme satın alınacaktır", ["tıbbi sarf"]),
            ["tıbbi sarf"],
        )


class AlakaFiltreTests(unittest.TestCase):
    def test_yazilim_uygun(self):
        sonuc = alaka_degerlendir(
            "Saha işgücü yönetimi yazılımı geliştirme ve entegrasyonları"
        )
        self.assertTrue(sonuc.uygun)
        self.assertIn("yazılım", sonuc.eslesen)
        self.assertIn("entegrasyon", sonuc.eslesen)
        self.assertGreaterEqual(sonuc.skor, 2)

    def test_lisans_whitelist(self):
        sonuc = alaka_degerlendir("Microsoft Office lisans alımı")
        self.assertTrue(sonuc.uygun)
        self.assertIn("lisans", [tr_lower(x) for x in sonuc.eslesen])

    def test_insaat_elenir(self):
        sonuc = alaka_degerlendir("Onarım ve güçlendirme inşaat işi yaptırılacak")
        self.assertFalse(sonuc.uygun)
        self.assertEqual(sonuc.neden, "kara_liste")

    def test_tibbi_sarf_elenir(self):
        sonuc = alaka_degerlendir("Tıbbi sarf malzeme satın alınacaktır")
        self.assertFalse(sonuc.uygun)
        self.assertEqual(sonuc.neden, "kara_liste")

    def test_yemek_elenir(self):
        sonuc = alaka_degerlendir("Personel yemek hizmeti alınacaktır")
        self.assertFalse(sonuc.uygun)

    def test_arac_kiralama_elenir(self):
        sonuc = alaka_degerlendir("Binek araç kiralama hizmeti")
        self.assertFalse(sonuc.uygun)

    def test_insaat_yazilimi_kalir(self):
        """Kara liste, başlıkta yazılım varsa ezemaz."""
        sonuc = alaka_degerlendir("İnşaat şantiye yönetim yazılımı alımı")
        self.assertTrue(sonuc.uygun)
        self.assertIn("yazılım", sonuc.eslesen)

    def test_emlak_kategori_elenir(self):
        sonuc = alaka_degerlendir(
            "Belediyeye ait taşınmaz ihale ile kiraya verilecektir",
            kategori="Emlak / Kiraya Verme",
        )
        self.assertFalse(sonuc.uygun)
        self.assertEqual(sonuc.neden, "kara_liste")

    def test_hedef_yok_dusuk(self):
        sonuc = alaka_degerlendir("Muhtelif hırdavat, elektrik, makine malzemeleri")
        self.assertFalse(sonuc.uygun)
        self.assertEqual(sonuc.neden, "whitelist_yok")

    def test_detay_metninden_eslesir(self):
        sonuc = alaka_degerlendir(
            "Hizmet alımı yapılacaktır",
            detay="Kurumsal ağ donanımı ve sunucu bakımı işi.",
        )
        self.assertTrue(sonuc.uygun)
        self.assertTrue(any(tr_lower(x) in ("ağ donanımı", "sunucu") for x in sonuc.eslesen))

    def test_internet_sadece_baslikta(self):
        sablon = "İlan internet sitesi üzerinden yayımlanmıştır. https://www.ilan.gov.tr/x"
        sonuc = alaka_degerlendir("Kumaş satın alınacaktır", detay=sablon)
        self.assertFalse(sonuc.uygun)
        self.assertEqual(sonuc.neden, "whitelist_yok")

        baslikta = alaka_degerlendir("Kurumsal internet altyapısı yenileme işi")
        self.assertTrue(baslikta.uygun)
        self.assertIn("internet", [tr_lower(x) for x in baslikta.eslesen])

    def test_koruyucu_donanim_elenir(self):
        sonuc = alaka_degerlendir("Kişisel koruyucu donanım (KKD) satın alınacaktır")
        self.assertFalse(sonuc.uygun)

    def test_fiber_silindir_elenir_fiber_ag_kalir(self):
        self.assertFalse(
            alaka_degerlendir("Fiber Silindir Hava Tüplü Solunum Seti alımı").uygun
        )
        self.assertTrue(alaka_degerlendir("Fiber ağ altyapı malzemeleri alınacaktır").uygun)

    def test_lisans_detayda_sayilmaz(self):
        sonuc = alaka_degerlendir(
            "Elektrik Enerjisi Alımı Yapılacaktır",
            detay="Üretim lisansı kapsamında tedarik yapılacaktır.",
        )
        self.assertFalse(sonuc.uygun)

    def test_kantin_elenir(self):
        sonuc = alaka_degerlendir("Personel kantin işletmesi hizmet alımı")
        self.assertFalse(sonuc.uygun)
        self.assertEqual(sonuc.neden, "kara_liste")

    def test_arsa_whitelist_olsa_da_elenir(self):
        sonuc = alaka_degerlendir("Belediye arsa satışı ilanı — internet üzerinden")
        self.assertFalse(sonuc.uygun)
        self.assertEqual(sonuc.neden, "kara_liste")

    def test_tasinmaz_whitelist_olsa_da_elenir(self):
        sonuc = alaka_degerlendir("Taşınmaz kiraya verilecektir", kategori="Emlak")
        self.assertFalse(sonuc.uygun)

    def test_yapim_kategorisi_internet_elenir(self):
        sonuc = alaka_degerlendir(
            "İnternet alt yapısı yapım ihalesi",
            kategori="Yapım / İnşaat / Yapım İşi",
        )
        self.assertFalse(sonuc.uygun)
        self.assertEqual(sonuc.neden, "kara_liste")


class BultenSuzgecTests(unittest.TestCase):
    def test_ilan_kayitlari_elenir_ekap_kalir(self):
        kayitlar = [
            {
                "Kaynak": "ilan.gov.tr",
                "İşin Adı": "Belediyeye ait taşınmaz satışı",
                "Link": "https://www.ilan.gov.tr/ilan/1/emlak-x",
                "Kategori / Tür": "Emlak / Kiraya Verme",
                "İKN": "ILN1",
            },
            {
                "Kaynak": "ilan.gov.tr",
                "İşin Adı": "Personel kantin işletmesi",
                "Link": "https://www.ilan.gov.tr/ilan/2/kantin",
                "İKN": "ILN2",
            },
            {
                "Kaynak": "ilan.gov.tr",
                "İşin Adı": "Binek araç kiralama hizmeti",
                "Link": "https://www.ilan.gov.tr/ilan/3/vasita",
                "İKN": "ILN3",
            },
            {
                "Kaynak": "ilan.gov.tr",
                "İşin Adı": "Kurumsal ağ donanımı ve yazılım lisansı alımı",
                "Link": "https://www.ilan.gov.tr/ilan/4/yazilim",
                "Kategori / Tür": "Mal Alımı",
                "İKN": "2026/99",
            },
            {
                "Kaynak": "EKAP",
                "İşin Adı": "OKAS ile gelen kayıt",
                "Link": "https://ekapv2.kik.gov.tr/x",
                "İKN": "2026/1",
            },
        ]
        temiz, ozet = kayitlari_suz(kayitlar)
        adlar = [k["İşin Adı"] for k in temiz]
        self.assertEqual(ozet.elenen, 3)
        self.assertIn("Kurumsal ağ donanımı ve yazılım lisansı alımı", adlar)
        self.assertIn("OKAS ile gelen kayıt", adlar)
        self.assertNotIn("Belediyeye ait taşınmaz satışı", adlar)
        self.assertNotIn("Personel kantin işletmesi", adlar)
        self.assertNotIn("Binek araç kiralama hizmeti", adlar)

    def test_meta_kutulari_hizalanir(self):
        uygun = {
            "Kaynak": "ilan.gov.tr",
            "İşin Adı": "Yazılım lisansı alımı",
            "Link": "https://www.ilan.gov.tr/ilan/9/yazilim",
            "İKN": "OK-1",
        }
        gurultu = {
            "Kaynak": "ilan.gov.tr",
            "İşin Adı": "Arsa satışı",
            "Link": "https://www.ilan.gov.tr/ilan/8/arsa",
            "İKN": "NO-1",
        }
        meta = {"yeni_bugun": [uygun, gurultu], "yeni_dun": [gurultu]}
        temiz, _ = kayitlari_suz([uygun, gurultu])
        meta = meta_listelerini_suz(meta, temiz)
        self.assertEqual(len(meta["yeni_bugun"]), 1)
        self.assertEqual(meta["yeni_bugun"][0]["İKN"], "OK-1")
        self.assertEqual(meta["yeni_dun"], [])

    def test_mail_tablosunda_gurultu_yok(self):
        from email_provider import ihale_sonuclarini_maile_cevir

        veriler = [
            {
                "Kaynak": "ilan.gov.tr",
                "İşin Adı": "Taşınmaz satışı ihalesi",
                "Kurum": "X Belediyesi",
                "Link": "https://www.ilan.gov.tr/ilan/1/tasinmaz",
                "İKN": "ILN-T",
                "İhale Tarihi": "20.09.2026",
                "İl": "Ankara",
                "Durum": "İlan yayımlandı",
            },
            {
                "Kaynak": "ilan.gov.tr",
                "İşin Adı": "Network switch ve yazılım lisansı alımı",
                "Kurum": "Y Üniversitesi",
                "Link": "https://www.ilan.gov.tr/ilan/2/network",
                "İKN": "ILN-N",
                "İhale Tarihi": "21.09.2026",
                "İl": "İzmir",
                "Durum": "İlan yayımlandı",
            },
        ]
        _konu, metin, html = ihale_sonuclarini_maile_cevir(
            veriler, "72200000", yeni_meta={"kaynak": "ilan.gov.tr", "yeni_bugun": veriler}
        )
        self.assertIn("Network switch ve yazılım lisansı alımı", html)
        self.assertNotIn("Taşınmaz satışı ihalesi", html)
        self.assertNotIn("Taşınmaz satışı ihalesi", metin)
        self.assertIn("alaka süzgecinden", html.lower() + metin.lower())


class HaricTemizlemeTests(unittest.TestCase):
    def test_lisans_whitelistten_cikarilmaz(self):
        self.assertEqual(haric_listesini_temizle("lisans, araba"), ["araba"])
        self.assertEqual(haric_listesini_temizle(""), [])


class TarihAraligiTests(unittest.TestCase):
    def test_gecen_hafta_7_gun(self):
        bugun = date(2026, 9, 14)
        bas, bit = gecen_hafta_araligi(7, bugun=bugun)
        self.assertEqual(bas, date(2026, 9, 7))
        self.assertEqual(bit, bugun)

    def test_scraper_varsayilan_10_gun(self):
        bugun = date(2026, 9, 14)
        aralik = tarih_araligi(varsayilan_gun=10, bugun=bugun)
        self.assertEqual(aralik, (date(2026, 9, 4), bugun))

    def test_acik_baslangic_bitis(self):
        aralik = tarih_araligi(baslangic="07.09.2026", bitis="13.09.2026")
        self.assertEqual(aralik, (date(2026, 9, 7), date(2026, 9, 13)))

    def test_iso_tarih(self):
        self.assertEqual(tarihi_coz("2026-09-07"), date(2026, 9, 7))

    def test_gun_sifir_filtresiz(self):
        self.assertIsNone(tarih_araligi(gun=0))


class KayitVeCsvTests(unittest.TestCase):
    def test_kategori_url(self):
        etiket = kategori_etiketi(
            "https://www.ilan.gov.tr/ilan/1/ihale-duyurulari-hizmet-alim-ihaleleri-x",
            "Kiralama ve Hizmet Alımı",
            "İHALE",
        )
        self.assertIn("Hizmet Alımı", etiket)

    def test_kayda_cevir_alanlari(self):
        ilan = {
            "id": "2221081",
            "adNo": "ILN02547254",
            "advertiserName": "ÇAMLIBEL ELEKTRİK DAĞITIM A.Ş.",
            "title": "Saha yazılımı geliştirme ve entegrasyonları hizmeti",
            "addressCityName": "SİVAS",
            "addressCountyName": "Merkez",
            "publishStartDate": "2026-09-13T21:00:00Z",
            "urlStr": "/ilan/2221081/ihale-duyurulari-hizmet-alim-ihaleleri-saha-yazilimi",
            "adTypeFilters": [
                {"key": "İlan Türü", "value": "İHALE"},
                {"key": "İhale Türü", "value": "Kiralama ve Hizmet Alımı"},
                {"key": "İhale ve Teklif Açma Tarihi", "value": "23.09.2026"},
                {"key": "İhale Kayıt No", "value": "2026/1"},
            ],
        }
        kayit = _kayda_cevir(ilan, "yazılım")
        self.assertEqual(kayit["İlan Id"], "2221081")
        self.assertEqual(kayit["Kurum"], "ÇAMLIBEL ELEKTRİK DAĞITIM A.Ş.")
        self.assertIn("yazılım", kayit["İşin Adı"])
        self.assertTrue(kayit["Link"].startswith("https://www.ilan.gov.tr/"))
        self.assertIn("Hizmet Alımı", kayit["Kategori / Tür"])

        satir = manuel_satira_cevir(kayit)
        self.assertEqual(list(satir.keys()), list(MANUEL_CSV_KOLONLAR))
        self.assertEqual(satir["Kurum"], kayit["Kurum"])
        self.assertEqual(satir["İhale Başlığı"], kayit["İşin Adı"])
        self.assertEqual(satir["İlan Bağlantısı / URL"], kayit["Link"])

    def test_csv_kolonlari_ve_ayrac(self):
        kayit = {
            "Yayım Tarihi": "13.09.2026",
            "İşin Adı": "Yazılım lisansı alımı",
            "Kurum": "Test Kurum",
            "Kategori / Tür": "Hizmet Alımı / Mal Alımı",
            "Link": "https://www.ilan.gov.tr/ilan/1/x",
            "Eşleşen Anahtar Kelime": "yazılım, lisans",
        }
        with tempfile.TemporaryDirectory() as tmp:
            yol = Path(tmp) / "ilan_gov_tr_gecen_hafta.csv"
            csv_yaz([kayit], yol)
            icerik = yol.read_text(encoding="utf-8-sig")
            self.assertIn("Yayın Tarihi;İhale Başlığı;Kurum;Kategori / Tür;", icerik)
            okuyucu = csv.DictReader(io.StringIO(icerik), delimiter=";")
            self.assertEqual(okuyucu.fieldnames, list(MANUEL_CSV_KOLONLAR))
            satir = next(okuyucu)
            self.assertEqual(satir["Eşleşen Anahtar Kelime"], "yazılım, lisans")
            self.assertEqual(satir["Yayın Tarihi"], "13.09.2026")

    def test_html_temizle(self):
        self.assertIn(
            "sunucu",
            html_temizle("<p>Dell <b>sunucu</b> alımı</p><script>x()</script>").lower(),
        )
        self.assertNotIn("script", html_temizle("<script>gizle()</script>metin").lower())


if __name__ == "__main__":
    unittest.main()
