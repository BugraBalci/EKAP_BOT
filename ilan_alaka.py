#!/usr/bin/env python3
"""ilan.gov.tr kayıtları için iki kademeli alaka (örtüşme) süzgeci.

ilan.gov.tr serbest metin araması gevşek: "yazılım" sorgusu emlak, vasıta
ve tıbbi sarf ilanlarını da döndürüyor. Yerel süzgeç iki kural uygular:

  1. Kara liste (blacklist) — inşaat, temizlik, yemek vb. terimler;
     whitelist isabeti yoksa ilan elenir.
  2. Hedef anahtar kelimeler (whitelist) — yazılım, bilişim, sunucu,
     donanım, network, siber, lisans, entegrasyon vb. Eşleşme varsa
     ilan tutulur ve eşleşen terimler rapora yazılır.

Karşılaştırmalar ``tr_lower`` ile yapılır (İ/I, Ş/ş ayrımı korunur).
Yumuşak kara liste, başlıkta hedef terim varsa ezemaz: "inşaat yönetim
yazılımı" gibi örtüşen işler kaçmasın. Kesin kara liste (arsa, taşınmaz,
kantin, araç kiralama, emlak/vasıta kategorisi) whitelist'i ezer; bunlar
bülten tablosuna hiç girmez.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

# Varsayılan tarama penceresi: son 7–10 gün. Manuel kontrol 7 gün,
# yedek hat/scraper 10 gün kullanır.
VARSAYILAN_GUN_MANUEL = 7
VARSAYILAN_GUN_SCRAPER = 10

# Kısa tek kelimeler kelime sınırına alınır; "ağ" "sağlık" içinde eşleşmesin.
KISA_TERIM_SINIRI = 6

# İlan gövdesinde (şablon metin, "internet sitesi", URL) sık geçtiği için
# yalnızca başlıkta sayılan terimler.
SADECE_BASLIK_TERIMLERI: Tuple[str, ...] = (
    "internet",
    "lisans",
    "bilgi işlem",
)

# Whitelist isabeti, şu kalıplar metindeyse sayılmaz (KKD, atık deposu vb.).
ZAYIF_ISTISNA: Dict[str, Tuple[str, ...]] = {
    "donanım": ("koruyucu donanım", "giyim ve donanım", "kişisel koruyucu"),
    "fiber": ("fiber silindir", "cam fiber", "fiber takviye"),
    "depolama": ("ara depolama", "atık"),
    "otomasyon": ("bina otomasyon", "iklimlendirme", "tesisat ve otomasyon"),
}

HEDEF_ANAHTAR_KELIMELER: Tuple[str, ...] = (
    "yazılım",
    "bilişim",
    "sunucu",
    "donanım",
    "network",
    "siber",
    "lisans",
    "entegrasyon",
    "bilgisayar",
    "internet",
    "telekomünikasyon",
    "haberleşme",
    "kablosuz",
    "modem",
    "telsiz",
    "verici",
    "frekans",
    "yedekleme",
    "ip telefon",
    "video konferans",
    "mobil telefon",
    "mesajlaşma",
    "elektronik posta",
    "ağ donanımı",
    "yerel ağ",
    "ağ hizmeti",
    "elektronik malzeme",
    "bilgi işlem",
    "siber güvenlik",
    "güvenlik duvarı",
    "firewall",
    "switch",
    "router",
    "bulut",
    "sanallaştırma",
    "otomasyon",
    "lisanslama",
    "microsoft",
    "oracle",
    "vmware",
    "backup",
    "storage",
    "depolama",
    "hosting",
    "fiber",
    "erp",
    "crm",
    "web uygulaması",
    "uygulama yazılımı",
    "veri merkezi",
    "veri tabanı",
    "veritabanı",
    "sunucu barındırma",
    "dizüstü",
    "notebook",
    "workstation",
)

DISLANACAK_KELIMELER: Tuple[str, ...] = (
    "inşaat",
    "temizlik",
    "yemek",
    "araç kiralama",
    "akaryakıt",
    "tıbbi sarf",
    "tıbbi malzeme",
    "asfalt",
    "hafriyat",
    "beton",
    "kırtasiye",
    "catering",
    "yemekhane",
    "gıda",
    "yakıt",
    "kömür",
    "peyzaj",
    "sulama",
    "üniforma",
    "kıyafet",
    "lastik",
    "tohum",
    "gübre",
    "tarım",
    "mobilya",
    "tekstil",
    "taşınmaz",
    "kuaför",
    "personel taşıma",
    "servis aracı",
    "binek araç",
    "araç alımı",
    "güvenlik görevlisi",
    "özel güvenlik",
    "kartuş",
    "toner",
    "hurda",
    "mübadele",
    "arsa",
    "konut",
    "kantin",
    "tehlikeli atık",
    "ara depolama",
    "koruyucu donanım",
    "kişisel koruyucu",
    "yapım işi",
    "kiraya verme",
)

# URL slug / ihale türü üzerinden ek dışlama (başlıkta hedef yoksa).
DISLANACAK_KATEGORI: Tuple[str, ...] = (
    "emlak",
    "vasıta",
    "yapım işi",
    "yapim-ve-insaat",
    "kiraya verme",
    "hayvan",
)

# Whitelist olsa bile bültenden düşer: emlak / kiralama / kantin gürültüsü.
KESIN_DISLANACAK: Tuple[str, ...] = (
    "arsa",
    "taşınmaz",
    "kantin",
    "araç kiralama",
    "kiraya verme",
    "konut",
    "mübadele",
    "binek araç",
    "araç alımı",
    "personel taşıma",
    "servis aracı",
)

# Emlak, vasıta, yapım işi kategorisi "internet" gibi şablon kelimelerle kalmaz.
KESIN_DISLANACAK_KATEGORI: Tuple[str, ...] = DISLANACAK_KATEGORI + ("inşaat",)

ILAN_KAYNAK_ISARETLERI: Tuple[str, ...] = ("ilan.gov.tr",)

URL_KATEGORI_ESLEME: Tuple[Tuple[str, str], ...] = (
    ("yapim-ve-insaat", "Yapım / İnşaat"),
    ("mal-alim", "Mal Alımı"),
    ("hizmet-alim", "Hizmet Alımı"),
    ("danismanlik", "Danışmanlık"),
    ("emlak", "Emlak"),
    ("vasita", "Vasıta"),
    ("hayvan", "Hayvan ve Hayvancılık"),
    ("endustriyel", "Endüstriyel Ürünler"),
)

MANUEL_CSV_KOLONLAR: Tuple[str, ...] = (
    "Yayın Tarihi",
    "İhale Başlığı",
    "Kurum",
    "Kategori / Tür",
    "İlan Bağlantısı / URL",
    "Eşleşen Anahtar Kelime",
)


@dataclass
class AlakaSonucu:
    uygun: bool
    skor: int = 0
    eslesen: List[str] = field(default_factory=list)
    dislanan: List[str] = field(default_factory=list)
    neden: str = ""


@dataclass
class TaramaOzeti:
    taranan: int = 0
    uygun: int = 0
    elenen: int = 0
    kara_liste: int = 0
    whitelist_yok: int = 0
    suresi_gecen: int = 0
    tekrar: int = 0
    detay_cekilen: int = 0
    haric: int = 0

    def kaydet(self, neden: str) -> None:
        self.elenen += 1
        if neden == "kara_liste":
            self.kara_liste += 1
        elif neden == "whitelist_yok":
            self.whitelist_yok += 1
        elif neden == "suresi_gecti":
            self.suresi_gecen += 1
        elif neden == "tekrar":
            self.tekrar += 1
        elif neden == "haric":
            self.haric += 1


def tr_lower(metin: str) -> str:
    """Türkçe İ/I ayrımını koruyan küçültme."""
    if not metin:
        return ""
    metin = (
        metin.replace("I", "ı")
        .replace("İ", "i")
        .replace("Ğ", "ğ")
        .replace("Ü", "ü")
        .replace("Ş", "ş")
        .replace("Ö", "ö")
        .replace("Ç", "ç")
    )
    return metin.lower()


def html_temizle(html: str) -> str:
    """İlan detay HTML'inden düz metin çıkarır."""
    if not html:
        return ""
    metin = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    metin = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", metin)
    metin = re.sub(r"<[^>]+>", " ", metin)
    metin = re.sub(r"&nbsp;|&amp;|&quot;|&#39;|&lt;|&gt;", " ", metin)
    # Şablon URL/site adı "internet" eşleşmesini şişirmesin.
    metin = re.sub(r"https?://\S+", " ", metin)
    metin = re.sub(r"www\.\S+", " ", metin, flags=re.I)
    metin = re.sub(r"ilan\.gov\.tr", " ", metin, flags=re.I)
    return re.sub(r"\s+", " ", metin).strip()


def _terim_deseni(terim: str) -> re.Pattern[str]:
    kalip = re.escape(terim)
    if " " in terim:
        return re.compile(kalip)
    if len(terim) <= KISA_TERIM_SINIRI:
        return re.compile(rf"\b{kalip}\b")
    return re.compile(kalip)


def terimleri_bul(metin: str, terimler: Iterable[str]) -> List[str]:
    """``metin`` içinde geçen benzersiz terimleri (orijinal yazımıyla) döndürür."""
    kucuk = tr_lower(metin)
    if not kucuk:
        return []
    bulunan: List[str] = []
    gorulen = set()
    for ham in terimler:
        terim = tr_lower(ham).strip()
        if len(terim) < 2 or terim in gorulen:
            continue
        if _terim_deseni(terim).search(kucuk):
            gorulen.add(terim)
            bulunan.append(ham.strip())
    return bulunan


def kategori_from_url(url: str) -> str:
    """ilan.gov.tr URL slug'ından okunabilir kategori adı çıkarır."""
    yol = tr_lower(url or "")
    for slug, ad in URL_KATEGORI_ESLEME:
        if slug in yol:
            return ad
    return ""


def kategori_etiketi(url: str = "", ihale_turu: str = "", ilan_turu: str = "") -> str:
    """Manuel inceleme kolonundaki 'Kategori / Tür' değerini üretir."""
    kategori = kategori_from_url(url)
    tur = (ihale_turu or "").strip() or (ilan_turu or "").strip()
    if kategori and tur and tr_lower(kategori) != tr_lower(tur):
        return f"{kategori} / {tur}"
    return kategori or tur or "İHALE"


def _zayif_eslesmeleri_ele(eslesen: Sequence[str], metin: str) -> List[str]:
    """KKD/atık gibi kalıplarda 'donanım', 'fiber' isabetini düşür."""
    kucuk = tr_lower(metin)
    kalan: List[str] = []
    for terim in eslesen:
        istisnalar = ZAYIF_ISTISNA.get(tr_lower(terim), ())
        if istisnalar and any(tr_lower(i) in kucuk for i in istisnalar):
            continue
        kalan.append(terim)
    return kalan


def alaka_degerlendir(
    baslik: str,
    detay: str = "",
    kategori: str = "",
    ekstra_pozitif: Sequence[str] = (),
    hedef: Sequence[str] = HEDEF_ANAHTAR_KELIMELER,
    kara: Sequence[str] = DISLANACAK_KELIMELER,
) -> AlakaSonucu:
    """Başlık + detay + kategori üzerinde iki kademeli süzgeç uygular."""
    sadece_baslik = {tr_lower(t) for t in SADECE_BASLIK_TERIMLERI}
    baslik_terimleri = list(hedef) + [k for k in ekstra_pozitif if k]
    detay_terimleri = [
        t for t in baslik_terimleri if tr_lower(t).strip() not in sadece_baslik
    ]
    eslesen = terimleri_bul(baslik or "", baslik_terimleri)
    for terim in terimleri_bul(detay or "", detay_terimleri):
        if terim not in eslesen:
            eslesen.append(terim)
    metin = f"{baslik or ''} {detay or ''}"
    eslesen = _zayif_eslesmeleri_ele(eslesen, metin)
    dislanan = terimleri_bul(metin, kara)
    dislanan += [
        k
        for k in terimleri_bul(kategori, DISLANACAK_KATEGORI)
        if k not in dislanan
    ]
    kesin = terimleri_bul(metin, KESIN_DISLANACAK)
    kesin += [
        k
        for k in terimleri_bul(kategori, KESIN_DISLANACAK_KATEGORI)
        if k not in kesin
    ]

    if kesin:
        return AlakaSonucu(
            uygun=False,
            skor=0,
            eslesen=list(eslesen),
            dislanan=kesin,
            neden="kara_liste",
        )
    if eslesen:
        return AlakaSonucu(
            uygun=True,
            skor=len(eslesen),
            eslesen=eslesen,
            dislanan=dislanan,
            neden="uygun",
        )
    if dislanan:
        return AlakaSonucu(
            uygun=False, skor=0, eslesen=[], dislanan=dislanan, neden="kara_liste"
        )
    return AlakaSonucu(uygun=False, skor=0, eslesen=[], dislanan=[], neden="whitelist_yok")


def tarihi_coz(ham: Optional[Union[str, date]]) -> Optional[date]:
    """DD.MM.YYYY / YYYY-MM-DD / date nesnesini date'e çevirir."""
    if ham is None or ham == "":
        return None
    if isinstance(ham, date) and not isinstance(ham, datetime):
        return ham
    if isinstance(ham, datetime):
        return ham.date()
    metin = str(ham).strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(metin[:10], fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Tarih okunamadı: {ham!r} (DD.MM.YYYY beklenir)")


def tarihi_yaz(gun: date) -> str:
    return gun.strftime("%d.%m.%Y")


def tarih_araligi(
    gun: Optional[int] = None,
    baslangic: Optional[Union[str, date]] = None,
    bitis: Optional[Union[str, date]] = None,
    varsayilan_gun: Optional[int] = None,
    bugun: Optional[date] = None,
) -> Optional[Tuple[date, date]]:
    """Başlangıç/bitiş veya son N gün aralığını üretir.

    Hiçbir parametre yoksa ve varsayılan_gun da None ise tarih filtresi
    uygulanmaz (None döner). gun=0 açıkça 'filtresiz' demektir.
    """
    ref = bugun or date.today()
    bas = tarihi_coz(baslangic) if baslangic else None
    bit = tarihi_coz(bitis) if bitis else None

    if bas or bit:
        return (bas or (bit - timedelta(days=varsayilan_gun or VARSAYILAN_GUN_SCRAPER)), bit or ref)

    if gun == 0:
        return None
    if gun is None and varsayilan_gun is None:
        return None
    kac = int(gun if gun is not None else varsayilan_gun)
    return (ref - timedelta(days=kac), ref)


def gecen_hafta_araligi(gun: int = VARSAYILAN_GUN_MANUEL, bugun: Optional[date] = None) -> Tuple[date, date]:
    """Geçen hafta (varsayılan son 7 gün, bugün dahil) yayın aralığı."""
    ref = bugun or date.today()
    return (ref - timedelta(days=int(gun)), ref)


def ozeti_yazdir(
    ozet: TaramaOzeti,
    *,
    baslangic: Optional[date] = None,
    bitis: Optional[date] = None,
    log=print,
) -> None:
    """Manuel kontrol için terminal özeti."""
    log("")
    log("📊 Tarama özeti")
    if baslangic and bitis:
        log(f"   Aralık : {tarihi_yaz(baslangic)} – {tarihi_yaz(bitis)}")
    log(f"   Taranan: {ozet.taranan}")
    log(f"   Kriterlere uyan: {ozet.uygun}")
    log(f"   Elenen : {ozet.elenen}")
    if ozet.kara_liste:
        log(f"     • kara liste / alakasız kategori: {ozet.kara_liste}")
    if ozet.whitelist_yok:
        log(f"     • hedef anahtar kelime yok: {ozet.whitelist_yok}")
    if ozet.haric:
        log(f"     • ek hariç listesi: {ozet.haric}")
    if ozet.suresi_gecen:
        log(f"     • ihale günü geçmiş: {ozet.suresi_gecen}")
    if ozet.tekrar:
        log(f"     • tekrar: {ozet.tekrar}")
    if ozet.detay_cekilen:
        log(f"     • detay çekilen: {ozet.detay_cekilen}")


def manuel_satira_cevir(kayit: dict) -> dict:
    """Scraper kaydını manuel inceleme CSV kolonlarına indirger."""
    eslesen = kayit.get("Eşleşen Anahtar Kelime") or kayit.get("Anahtar Kelime") or ""
    if isinstance(eslesen, (list, tuple)):
        eslesen = ", ".join(eslesen)
    return {
        "Yayın Tarihi": kayit.get("Yayım Tarihi") or kayit.get("Yayın Tarihi") or "",
        "İhale Başlığı": kayit.get("İşin Adı") or kayit.get("İhale Başlığı") or "",
        "Kurum": kayit.get("Kurum") or "",
        "Kategori / Tür": kayit.get("Kategori / Tür") or kayit.get("Tür") or "",
        "İlan Bağlantısı / URL": kayit.get("Link") or kayit.get("İlan Bağlantısı / URL") or "",
        "Eşleşen Anahtar Kelime": eslesen,
    }


def haric_listesini_temizle(
    dislanacak_kelime: str,
    hedef: Sequence[str] = HEDEF_ANAHTAR_KELIMELER,
) -> List[str]:
    """GUI'den gelen 'lisans, araba' gibi listeden whitelist terimlerini çıkarır."""
    pozitif = {tr_lower(k) for k in hedef}
    temiz: List[str] = []
    for ham in (dislanacak_kelime or "").split(","):
        terim = tr_lower(ham.strip())
        if not terim or terim in pozitif:
            continue
        temiz.append(terim)
    return temiz


def ilan_kaynagi_mi(kayit: dict) -> bool:
    """Kayıt ilan.gov.tr yedek hattından mı geldi? EKAP satırlarına dokunulmaz."""
    kaynak = tr_lower(str(kayit.get("Kaynak") or ""))
    link = tr_lower(
        str(kayit.get("Link") or kayit.get("İlan Bağlantısı / URL") or "")
    )
    return any(
        isaret in kaynak or isaret in link for isaret in ILAN_KAYNAK_ISARETLERI
    )


def kayit_anahtari(kayit: dict) -> str:
    """Bülten kutularını ana listeyle hizalamak için kararlı anahtar."""
    ikn = (kayit.get("İKN") or "").strip()
    if ikn and ikn != "-":
        return f"ikn:{ikn}"
    link = (kayit.get("Link") or kayit.get("İlan Bağlantısı / URL") or "").strip()
    if link:
        return f"link:{link}"
    ilan_id = (kayit.get("İlan Id") or "").strip()
    if ilan_id:
        return f"id:{ilan_id}"
    kurum = (kayit.get("Kurum") or kayit.get("İhaleyi Veren Kurum") or "").strip()
    ad = (kayit.get("İşin Adı") or kayit.get("İhale Başlığı") or "").strip()
    return f"kurum:{kurum}|ad:{ad}"


def kayit_alaka_degerlendir(
    kayit: dict,
    ekstra_pozitif: Sequence[str] = (),
) -> AlakaSonucu:
    """Scraper/mail kaydını başlık + detay + kategori üzerinden süzgeçler."""
    return alaka_degerlendir(
        kayit.get("İşin Adı") or kayit.get("İhale Başlığı") or "",
        kayit.get("Detay") or "",
        kayit.get("Kategori / Tür") or kayit.get("Tür") or "",
        ekstra_pozitif=ekstra_pozitif,
    )


def _alaka_alanlarini_yaz(kayit: dict, sonuc: AlakaSonucu) -> None:
    kayit["Eşleşen Anahtar Kelime"] = ", ".join(sonuc.eslesen)
    kayit["Alaka Skoru"] = str(sonuc.skor)
    kayit["Alaka Nedeni"] = sonuc.neden


def kayitlari_suz(
    kayitlar: Optional[Sequence[dict]] = None,
    *,
    sadece_ilan_kaynagi: bool = True,
) -> Tuple[List[dict], TaramaOzeti]:
    """Bülten/mail için zorunlu alaka süzgeci.

    ``sadece_ilan_kaynagi=True`` iken EKAP (OKAS) kayıtları olduğu gibi kalır;
    ilan.gov.tr satırları whitelist/blacklist'ten geçmek zorundadır.
    """
    ozet = TaramaOzeti()
    uygunlar: List[dict] = []
    for kayit in kayitlar or ():
        ozet.taranan += 1
        if sadece_ilan_kaynagi and not ilan_kaynagi_mi(kayit):
            uygunlar.append(kayit)
            ozet.uygun += 1
            continue
        sonuc = kayit_alaka_degerlendir(kayit)
        _alaka_alanlarini_yaz(kayit, sonuc)
        if sonuc.uygun:
            uygunlar.append(kayit)
            ozet.uygun += 1
        else:
            ozet.kaydet(sonuc.neden or "whitelist_yok")
    return uygunlar, ozet


def meta_listelerini_suz(
    meta: Optional[dict],
    izinli: Sequence[dict],
) -> dict:
    """Bugün/dün/hafta kutularını ana listedeki temiz kayıtlarla hizalar."""
    if not isinstance(meta, dict):
        return {}
    izinli_anahtar = {kayit_anahtari(k) for k in izinli if kayit_anahtari(k)}
    out = dict(meta)
    for alan in ("yeni_bugun", "yeni_dun", "yeni_bu_hafta"):
        ham = list(out.get(alan) or [])
        if not ham:
            continue
        suzulmus, _ozet = kayitlari_suz(ham)
        if izinli_anahtar:
            suzulmus = [
                k for k in suzulmus if kayit_anahtari(k) in izinli_anahtar
            ]
        out[alan] = suzulmus
    return out
