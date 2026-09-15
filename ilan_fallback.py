#!/usr/bin/env python3
"""EKAP kapalıyken devreye giren ilan.gov.tr yedek hattı.

EKAP 428 döndüğünde veya v2 arayüzü render edilmediğinde veri akışının
tamamen kesilmemesi için aynı anahtar kelimeler ilan.gov.tr'nin ihale
duyuruları üzerinden taranır.

Kapsam farkı bilinçlidir: ilan.gov.tr yalnızca yayımlanmış ilanları taşır,
OKAS koduyla filtreleme yoktur ve İKN alanı bulunmaz. Bu yüzden OKAS kökleri
serbest metin karşılıklarına çevrilir (``okas_defaults.OKAS_ANAHTAR_KELIME``)
ve her kayıt ``Kaynak: ilan.gov.tr`` etiketiyle işaretlenir — bültende EKAP
kaydı sanılmasın.

Tarih filtresi ``baslangic`` / ``bitis`` (DD.MM.YYYY) veya ``gun`` ile
verilir; varsayılan pencere son 10 gündür. Alaka süzgeci
(``ilan_alaka``) başlık ve detay metninde hedef/kara liste uygular.

Kullanım:
    from ilan_fallback import okas_ile_tara, gecen_haftayi_tara, IlanYedekHatasi
    kayitlar = okas_ile_tara(okas_kodlari, limit=20, gun=10)
    uygun, ozet = gecen_haftayi_tara(gun=7)

CLI:
    python ilan_fallback.py --okas 72200000,32420000
    python ilan_fallback.py --kelime yazılım,ağ donanımı --gun 7
    python ilan_fallback.py --baslangic 07.09.2026 --bitis 14.09.2026
    python manual_ilan_check.py
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date, datetime
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import requests

from ekap_http import (
    dogrulamayi_gevset,
    ekap_oturumu,
    sertifika_zinciri_hatasi_mi,
)
from ilan_alaka import (
    VARSAYILAN_GUN_MANUEL,
    VARSAYILAN_GUN_SCRAPER,
    AlakaSonucu,
    TaramaOzeti,
    alaka_degerlendir,
    gecen_hafta_araligi,
    haric_listesini_temizle,
    html_temizle,
    kategori_etiketi,
    ozeti_yazdir,
    tarih_araligi,
    tarihi_yaz,
    tr_lower,
)
from okas_defaults import (
    DEFAULT_OKAS_KODLARI,
    GENEL_ANAHTAR_KELIMELER,
    OKAS_ANAHTAR_KELIME,
)

ILAN_TABAN = "https://www.ilan.gov.tr"
ARAMA_YOLU = "/api/api/services/app/Ad/AdsByFilter"
DETAY_YOLU = "/api/api/services/app/AdDetail/GetAdDetail"
KAYNAK_ADI = "ilan.gov.tr"
# ilan.gov.tr AdsByFilter fiilen sayfa başına 20 kayıt veriyor (50 istenince de 20).
SAYFA_BOYUTU = 20

# İlan türü kodları (ats): 2=İcra, 3=İhale, 4=Tebligat, 5=Personel.
IHALE_TURU_ID = 3

BASLIKLAR = {
    "accept": "text/plain",
    "content-type": "application/json-patch+json",
    "origin": ILAN_TABAN,
    "referer": f"{ILAN_TABAN}/ilan/tum-ilanlar",
    "x-request-origin": "IGT-UI",
    "x-requested-with": "XMLHttpRequest",
}

# İhale/satış tarihi bu filtre alanlarından biriyle geliyor.
TARIH_ALANLARI = (
    # ilan.gov.tr ihale ilanlarında gerçek ihale günü bu anahtarla geliyor.
    "İhale ve Teklif Açma Tarihi",
    "İhale Tarihi",
    "Birinci Satış Günü",
    "Son Başvuru Tarihi",
    "Son Teklif Tarihi",
)

# İhale ilanlarının çoğu gerçek İKN'yi taşıyor; ilan numarası yalnızca yedek.
IKN_ALANLARI = ("İhale Kayıt No", "İhale Kayıt Numarası", "İKN")


class IlanYedekHatasi(RuntimeError):
    """ilan.gov.tr yedek hattı da veri döndüremedi."""


def _oturum() -> requests.Session:
    oturum = ekap_oturumu()
    oturum.headers.update(BASLIKLAR)
    return oturum


def okas_kelimeleri(okas_kodlari: Sequence[str]) -> List[str]:
    """OKAS kök kodlarını ilan.gov.tr'de aranabilir kelimelere çevirir."""
    kelimeler: List[str] = []
    for ham in okas_kodlari or []:
        kod = (ham or "").strip()
        if not kod:
            continue
        kelime = OKAS_ANAHTAR_KELIME.get(kod)
        if not kelime:
            # Kod eşlenmemişse kök öneke en yakın eşlemeyi dene (32552420 → 32552).
            for uzunluk in range(len(kod) - 1, 3, -1):
                for eslenen_kod, eslenen in OKAS_ANAHTAR_KELIME.items():
                    if eslenen_kod.startswith(kod[:uzunluk]):
                        kelime = eslenen
                        break
                if kelime:
                    break
        if kelime and kelime not in kelimeler:
            kelimeler.append(kelime)

    if not kelimeler:
        kelimeler = [k for k in GENEL_ANAHTAR_KELIMELER]
    return kelimeler


def alaka_deseni(kelimeler: Sequence[str]) -> "re.Pattern[str]":
    """Gelen ilanları kendi terimlerimizle ikinci kez süzmek için desen üretir.

    ilan.gov.tr'nin serbest metin araması gevşek: "yazılım" sorgusuna tıbbi
    sarf malzeme veya taşınmaz satışı ilanları da dönüyor. İki kural isabeti
    belirgin biçimde artırıyor:

      * Çok kelimeli terimler öbek olarak aranır. "elektronik malzeme"yi
        parçalayınca "malzeme" tek başına kalıyor ve alakasız ilanları alıyor.
      * Kısa tek kelimeler kelime sınırıyla aranır; "ağ" serbest bırakılınca
        "sağlık" gibi kelimelerde eşleşiyor.
    """
    parcalar: List[str] = []
    for ham in kelimeler:
        terim = tr_lower(ham).strip()
        if len(terim) < 3:
            continue
        kalip = re.escape(terim)
        if " " in terim:
            parcalar.append(kalip)
        else:
            parcalar.append(rf"\b{kalip}" if len(terim) <= 6 else kalip)
    if not parcalar:
        parcalar = [re.escape(tr_lower(k)) for k in GENEL_ANAHTAR_KELIMELER]
    return re.compile("|".join(sorted(set(parcalar))))


def _tarih_metni(ham: Optional[str]) -> str:
    if not ham:
        return ""
    try:
        return datetime.fromisoformat(str(ham).replace("Z", "+00:00")).strftime(
            "%d.%m.%Y"
        )
    except ValueError:
        return str(ham)[:10]


def _filtreden_al(filtreler: Dict[str, str], alanlar: Iterable[str]) -> str:
    for alan in alanlar:
        deger = (filtreler.get(alan) or "").strip()
        if deger:
            return deger
    return ""


def _ihale_tarihi(filtreler: Dict[str, str]) -> str:
    """İlan filtrelerinden ihale/satış tarihini çıkarır."""
    return _filtreden_al(filtreler, TARIH_ALANLARI)


def _kayda_cevir(ilan: Dict, kelime: str = "") -> Dict[str, str]:
    """ilan.gov.tr kaydını projenin CSV/mail şemasına çevirir."""
    filtreler = {
        (f.get("key") or ""): (f.get("value") or "")
        for f in (ilan.get("adTypeFilters") or [])
    }

    kurum = (ilan.get("advertiserName") or "-").strip()
    baslik = (ilan.get("title") or "").strip()
    il = (ilan.get("addressCityName") or "").strip()
    ilce = (ilan.get("addressCountyName") or "").strip()
    ilan_no = (ilan.get("adNo") or "").strip()
    ilan_id = str(ilan.get("id") or "").strip()

    yayim = _tarih_metni(ilan.get("publishStartDate"))
    # Yayım tarihi ihale tarihi yerine konmaz: takvim hatırlatıcısı ihaleden 7
    # gün önceye ve ihale saatinden 1 saat önceye kurulduğu için yayım tarihi
    # kullanılırsa hatırlatıcı geçmişe düşer. Gerçek ihale günü yoksa alan boş bırakılır.
    tarih = _ihale_tarihi(filtreler)
    ikn = _filtreden_al(filtreler, IKN_ALANLARI)
    yol = (ilan.get("urlStr") or "").strip()
    link = f"{ILAN_TABAN}{yol}" if yol.startswith("/") else yol
    ihale_turu = (filtreler.get("İhale Türü") or "").strip()
    ilan_turu = (filtreler.get("İlan Türü") or "İHALE").strip()
    kategori = kategori_etiketi(link or yol, ihale_turu, ilan_turu)

    durum = "İlan yayımlandı"
    if yayim:
        durum += f" ({yayim})"

    return {
        "İlan Id": ilan_id,
        "Kurum": kurum,
        "İşin Adı": baslik,
        # İlanda İKN varsa o kullanılır; böylece kayıt EKAP verisiyle eşleşir.
        # Yoksa izlenebilirlik için ilan numarasına düşülür.
        "İKN": ikn or ilan_no or "-",
        "İhale Tarihi": tarih,
        # İhale tarihinden ayrı tutulur: bülten "bugün/dün yeni" kutularını
        # ihalenin yapılacağı güne değil, ilanın yayımlandığı güne göre sayıyor.
        "Yayım Tarihi": yayim,
        "İl / Saat": " / ".join(x for x in (il or ilce, tarih) if x),
        "Tür": ihale_turu or ilan_turu,
        "Kategori / Tür": kategori,
        "İl": il or ilce,
        "Durum": durum,
        "Link": link,
        "Kaynak": KAYNAK_ADI,
        "Anahtar Kelime": kelime,
        "Eşleşen Anahtar Kelime": kelime,
        "Alaka Skoru": "",
        "Detay": "",
        "İhaleyi Veren Kurum": kurum,
        "İhale Detayları": " | ".join(x for x in (baslik, il, tarih) if x),
    }


def _suresi_gecti(ihale_tarihi: str) -> bool:
    """İhale günü geçmişse True. Liste 'teklif vermeye açık' ihaleler içindir.

    Tarih okunamıyorsa kayıt elenmez; belirsizlik yüzünden ilan kaybetmektense
    fazladan bir satır göstermek yeğdir.
    """
    ham = (ihale_tarihi or "").strip()[:10]
    if not ham:
        return False
    try:
        return datetime.strptime(ham, "%d.%m.%Y").date() < date.today()
    except ValueError:
        return False


def _dislandi(kayit: Dict[str, str], dislanacak: Iterable[str]) -> bool:
    metin = tr_lower(f"{kayit.get('Kurum', '')} {kayit.get('İşin Adı', '')}")
    return any(k in metin for k in dislanacak)


def _kayit_anahtari(kayit: Dict[str, str]) -> str:
    if kayit.get("İKN") and kayit["İKN"] != "-":
        return kayit["İKN"]
    return kayit.get("Link") or kayit.get("İlan Id") or ""


def _alaka_uygula(
    kayit: Dict[str, str],
    *,
    ekstra_pozitif: Sequence[str] = (),
) -> AlakaSonucu:
    return alaka_degerlendir(
        kayit.get("İşin Adı") or "",
        kayit.get("Detay") or "",
        kayit.get("Kategori / Tür") or "",
        ekstra_pozitif=ekstra_pozitif,
    )


def _alaka_yaz(kayit: Dict[str, str], sonuc: AlakaSonucu) -> None:
    kayit["Eşleşen Anahtar Kelime"] = ", ".join(sonuc.eslesen)
    kayit["Alaka Skoru"] = str(sonuc.skor)
    kayit["Alaka Nedeni"] = sonuc.neden


def _ppd_anahtarlari(
    gun: Optional[int] = None,
    baslangic: Optional[Union[str, date]] = None,
    bitis: Optional[Union[str, date]] = None,
    varsayilan_gun: Optional[int] = None,
) -> Dict[str, List[str]]:
    aralik = tarih_araligi(
        gun=gun,
        baslangic=baslangic,
        bitis=bitis,
        varsayilan_gun=varsayilan_gun,
    )
    if not aralik:
        return {}
    bas, bit = aralik
    return {"ppdmin": [tarihi_yaz(bas)], "ppdmax": [tarihi_yaz(bit)]}


def ilan_ara_sonuc(
    kelime: str = "",
    *,
    limit: int = 20,
    skip_count: int = 0,
    gun: Optional[int] = None,
    baslangic: Optional[Union[str, date]] = None,
    bitis: Optional[Union[str, date]] = None,
    varsayilan_gun: Optional[int] = None,
    sadece_ihale: bool = True,
    baslikta_ara: bool = False,
    il_plaka: Optional[int] = None,
    timeout: float = 20.0,
    oturum: Optional[requests.Session] = None,
) -> Dict:
    """Tek sayfalık ilan.gov.tr araması; ads + num_found döndürür."""
    kendi_oturumu = oturum is None
    oturum = oturum or _oturum()

    anahtarlar: Dict[str, List] = {}
    if (kelime or "").strip():
        anahtarlar["t" if baslikta_ara else "q"] = [kelime.strip()]
    if sadece_ihale:
        anahtarlar["ats"] = [IHALE_TURU_ID]
    if il_plaka is not None:
        anahtarlar["aci"] = [int(il_plaka)]
    anahtarlar.update(
        _ppd_anahtarlari(
            gun=gun,
            baslangic=baslangic,
            bitis=bitis,
            varsayilan_gun=varsayilan_gun,
        )
    )

    skip_count = max(0, int(skip_count))
    sayfa_limiti = max(1, min(int(limit), 50))
    anahtarlar["currentPage"] = [skip_count // SAYFA_BOYUTU + 1]
    govde = {
        "keys": anahtarlar,
        "skipCount": skip_count,
        "maxResultCount": sayfa_limiti,
    }
    url = f"{ILAN_TABAN}{ARAMA_YOLU}"

    try:
        try:
            yanit = oturum.post(url, json=govde, timeout=timeout)
        except requests.exceptions.SSLError as e:
            # Sunucu ara sertifikayı zincirde sunmuyor (curl da aynı hatayı
            # veriyor). Doğrulamayı ancak tam bu hatada ve yalnızca bu oturum
            # için gevşetiyoruz; başka TLS hatalarında değil.
            if not sertifika_zinciri_hatasi_mi(e):
                raise
            dogrulamayi_gevset(
                oturum, f"{KAYNAK_ADI} ara sertifikayı zincirde sunmuyor"
            )
            yanit = oturum.post(url, json=govde, timeout=timeout)

        yanit.raise_for_status()
        sonuc = (yanit.json() or {}).get("result") or {}
        return {
            "ads": sonuc.get("ads") or [],
            "num_found": int(sonuc.get("numFound") or 0),
        }
    finally:
        if kendi_oturumu:
            oturum.close()


def ilan_ara(
    kelime: str = "",
    *,
    limit: int = 20,
    gun: Optional[int] = None,
    baslangic: Optional[Union[str, date]] = None,
    bitis: Optional[Union[str, date]] = None,
    sadece_ihale: bool = True,
    baslikta_ara: bool = False,
    il_plaka: Optional[int] = None,
    timeout: float = 20.0,
    oturum: Optional[requests.Session] = None,
    skip_count: int = 0,
    varsayilan_gun: Optional[int] = None,
) -> List[Dict]:
    """Tek bir anahtar kelime için ilan.gov.tr araması yapar (ham kayıtlar)."""
    return ilan_ara_sonuc(
        kelime,
        limit=limit,
        skip_count=skip_count,
        gun=gun,
        baslangic=baslangic,
        bitis=bitis,
        varsayilan_gun=varsayilan_gun,
        sadece_ihale=sadece_ihale,
        baslikta_ara=baslikta_ara,
        il_plaka=il_plaka,
        timeout=timeout,
        oturum=oturum,
    )["ads"]


def ilanlari_sayfala(
    kelime: str = "",
    *,
    gun: Optional[int] = None,
    baslangic: Optional[Union[str, date]] = None,
    bitis: Optional[Union[str, date]] = None,
    varsayilan_gun: Optional[int] = None,
    sadece_ihale: bool = True,
    baslikta_ara: bool = False,
    il_plaka: Optional[int] = None,
    timeout: float = 20.0,
    oturum: Optional[requests.Session] = None,
    sayfa_boyutu: int = SAYFA_BOYUTU,
    maksimum: Optional[int] = None,
    bekleme: float = 0.12,
    log: Callable[[str], None] = print,
) -> Tuple[List[Dict], int]:
    """Tarih aralığındaki ilanları sayfa sayfa çeker. (ads, toplam) döner."""
    kendi_oturumu = oturum is None
    oturum = oturum or _oturum()
    toplanan: List[Dict] = []
    toplam = 0
    skip = 0
    sayfa_boyutu = max(1, min(int(sayfa_boyutu), SAYFA_BOYUTU))
    sayfa_no = 0
    max_sayfa = 200

    try:
        while True:
            sayfa_no += 1
            if sayfa_no > max_sayfa:
                log(f"⚠️ Sayfa üst sınırı ({max_sayfa}); tarama kesildi")
                break
            sonuc = ilan_ara_sonuc(
                kelime,
                limit=sayfa_boyutu,
                skip_count=skip,
                gun=gun,
                baslangic=baslangic,
                bitis=bitis,
                varsayilan_gun=varsayilan_gun,
                sadece_ihale=sadece_ihale,
                baslikta_ara=baslikta_ara,
                il_plaka=il_plaka,
                timeout=timeout,
                oturum=oturum,
            )
            ads = sonuc["ads"]
            toplam = sonuc["num_found"] or toplam
            if not ads:
                break
            toplanan.extend(ads)
            skip += len(ads)
            if maksimum and len(toplanan) >= maksimum:
                toplanan = toplanan[:maksimum]
                break
            # API 50 istense de 20 döndürebilir; kısa sayfa tek başına bitiş değildir.
            if toplam and skip >= toplam:
                break
            if bekleme:
                time.sleep(bekleme)
        if kelime:
            log(
                f"   ilan.gov.tr '{kelime}': {len(toplanan)}/{toplam or len(toplanan)} ilan çekildi"
            )
        else:
            log(
                f"   ilan.gov.tr tarih aralığı: {len(toplanan)}/{toplam or len(toplanan)} ilan çekildi"
            )
        return toplanan, toplam or len(toplanan)
    finally:
        if kendi_oturumu:
            oturum.close()


def ilan_detay_al(
    ilan_id: str,
    *,
    timeout: float = 20.0,
    oturum: Optional[requests.Session] = None,
) -> str:
    """İlan gövde metnini (HTML temizlenmiş) döndürür; hata olursa boş string."""
    if not ilan_id:
        return ""
    kendi_oturumu = oturum is None
    oturum = oturum or _oturum()
    url = f"{ILAN_TABAN}{DETAY_YOLU}"
    try:
        try:
            yanit = oturum.get(url, params={"id": ilan_id}, timeout=timeout)
        except requests.exceptions.SSLError as e:
            if not sertifika_zinciri_hatasi_mi(e):
                raise
            dogrulamayi_gevset(
                oturum, f"{KAYNAK_ADI} ara sertifikayı zincirde sunmuyor"
            )
            yanit = oturum.get(url, params={"id": ilan_id}, timeout=timeout)
        yanit.raise_for_status()
        sonuc = (yanit.json() or {}).get("result") or {}
        html = sonuc.get("content") or ""
        return html_temizle(html)
    except Exception:
        return ""
    finally:
        if kendi_oturumu:
            oturum.close()


def _belirsizleri_detaylandir(
    belirsizler: List[Dict[str, str]],
    *,
    oturum: requests.Session,
    detay_limiti: int,
    ozet: TaramaOzeti,
    log: Callable[[str], None],
) -> List[Dict[str, str]]:
    """Başlıkta hedef kelime olmayan ilanların gövdesini çekip yeniden süz."""
    uygunlar: List[Dict[str, str]] = []
    for kayit in belirsizler[: max(0, int(detay_limiti))]:
        detay = ilan_detay_al(kayit.get("İlan Id") or "", oturum=oturum)
        ozet.detay_cekilen += 1
        if not detay:
            ozet.kaydet("whitelist_yok")
            continue
        kayit["Detay"] = detay[:4000]
        sonuc = _alaka_uygula(kayit)
        _alaka_yaz(kayit, sonuc)
        if sonuc.uygun:
            uygunlar.append(kayit)
            ozet.uygun += 1
        else:
            ozet.kaydet(sonuc.neden or "whitelist_yok")
    kalan = max(0, len(belirsizler) - max(0, int(detay_limiti)))
    if kalan:
        ozet.elenen += kalan
        ozet.whitelist_yok += kalan
        log(f"   ℹ️ Detay limiti ({detay_limiti}); {kalan} belirsiz ilan elendi")
    return uygunlar


def ilan_ihale_tara(
    kelimeler: Sequence[str],
    *,
    limit: int = 20,
    gun: Optional[int] = None,
    baslangic: Optional[Union[str, date]] = None,
    bitis: Optional[Union[str, date]] = None,
    dislanacak_kelime: str = "",
    baslikta_ara: bool = False,
    il_plaka: Optional[int] = None,
    alaka_suz: bool = True,
    log: Callable[[str], None] = print,
) -> List[Dict[str, str]]:
    """Verilen kelimeleri sırayla tarar ve tekilleştirilmiş kayıt listesi döndürür."""
    dislanacak = haric_listesini_temizle(dislanacak_kelime)
    ozet = TaramaOzeti()
    kayitlar: List[Dict[str, str]] = []
    gorulen = set()
    oturum = _oturum()

    try:
        for sira, kelime in enumerate(kelimeler, start=1):
            try:
                ilanlar = ilan_ara(
                    kelime,
                    limit=limit,
                    gun=gun,
                    baslangic=baslangic,
                    bitis=bitis,
                    varsayilan_gun=None if gun is not None or baslangic or bitis else VARSAYILAN_GUN_SCRAPER,
                    baslikta_ara=baslikta_ara,
                    il_plaka=il_plaka,
                    oturum=oturum,
                )
            except Exception as e:
                log(f"⚠️ ilan.gov.tr '{kelime}' aramasında hata: {e}")
                continue

            eklenen = 0
            elendi = 0
            for ilan in ilanlar:
                ozet.taranan += 1
                kayit = _kayda_cevir(ilan, kelime)
                anahtar = _kayit_anahtari(kayit)
                if not anahtar or anahtar in gorulen:
                    ozet.kaydet("tekrar")
                    elendi += 1
                    continue
                if dislanacak and _dislandi(kayit, dislanacak):
                    ozet.kaydet("haric")
                    elendi += 1
                    continue
                if alaka_suz:
                    # Arama kelimesi ekstra whitelist sayılmaz: "veri" gibi
                    # gevşek OKAS karşılıkları arsa/kantin ilanını tutmasın.
                    sonuc = _alaka_uygula(kayit)
                    _alaka_yaz(kayit, sonuc)
                    if not sonuc.uygun:
                        ozet.kaydet(sonuc.neden or "whitelist_yok")
                        elendi += 1
                        continue
                if _suresi_gecti(kayit["İhale Tarihi"]):
                    ozet.kaydet("suresi_gecti")
                    elendi += 1
                    continue
                gorulen.add(anahtar)
                kayitlar.append(kayit)
                ozet.uygun += 1
                eklenen += 1

            log(
                f"   [{sira}/{len(kelimeler)}] ilan.gov.tr '{kelime}': "
                f"{len(ilanlar)} ilan, {eklenen} yeni, {elendi} elendi"
            )
    finally:
        oturum.close()

    aralik = tarih_araligi(gun=gun, baslangic=baslangic, bitis=bitis)
    ozeti_yazdir(
        ozet,
        baslangic=aralik[0] if aralik else None,
        bitis=aralik[1] if aralik else None,
        log=log,
    )
    return kayitlar


def okas_ile_tara(
    okas_kodlari: Sequence[str],
    *,
    limit: int = 20,
    gun: Optional[int] = VARSAYILAN_GUN_SCRAPER,
    baslangic: Optional[Union[str, date]] = None,
    bitis: Optional[Union[str, date]] = None,
    dislanacak_kelime: str = "",
    baslikta_ara: bool = False,
    log: Callable[[str], None] = print,
) -> List[Dict[str, str]]:
    """EKAP yerine kullanılan ana giriş noktası: OKAS kökleri → ilan.gov.tr."""
    kelimeler = okas_kelimeleri(okas_kodlari)
    if not kelimeler:
        raise IlanYedekHatasi("OKAS kodları ilan.gov.tr kelimelerine çevrilemedi.")

    log(
        f"🛟 Yedek hat devrede: {len(okas_kodlari)} OKAS kodu → "
        f"{len(kelimeler)} anahtar kelime ({', '.join(kelimeler)})"
    )
    kayitlar = ilan_ihale_tara(
        kelimeler,
        limit=limit,
        gun=gun,
        baslangic=baslangic,
        bitis=bitis,
        dislanacak_kelime=dislanacak_kelime,
        baslikta_ara=baslikta_ara,
        alaka_suz=True,
        log=log,
    )
    if not kayitlar:
        raise IlanYedekHatasi(
            f"{len(kelimeler)} anahtar kelime tarandı ama hiç uygun ilan bulunamadı."
        )
    return kayitlar


def gecen_haftayi_tara(
    *,
    gun: int = VARSAYILAN_GUN_MANUEL,
    baslangic: Optional[Union[str, date]] = None,
    bitis: Optional[Union[str, date]] = None,
    dislanacak_kelime: str = "",
    detay_limiti: int = 80,
    maksimum: Optional[int] = None,
    suresi_geceni_ele: bool = False,
    log: Callable[[str], None] = print,
) -> Tuple[List[Dict[str, str]], TaramaOzeti]:
    """Geçen hafta (veya verilen aralık) yayımlanan ihale ilanlarını çeker ve süzgeçler.

    Anahtar kelime araması yapmaz; tarih + ihale türü ile tüm ilanları sayfalar,
    sonra iki kademeli alaka süzgecini uygular. Başlıkta hedef terim olmayan
    ama kara listeden de geçmeyen kayıtların gövdesi ``detay_limiti`` kadar çekilir.
    """
    if baslangic or bitis:
        aralik = tarih_araligi(
            gun=gun,
            baslangic=baslangic,
            bitis=bitis,
            varsayilan_gun=gun,
        )
        if not aralik:
            raise IlanYedekHatasi("Tarih aralığı çözülemedi.")
        bas, bit = aralik
    else:
        bas, bit = gecen_hafta_araligi(gun)

    log(
        f"🔎 Geçen hafta taraması: {tarihi_yaz(bas)} – {tarihi_yaz(bit)} "
        f"(ilan.gov.tr ihale duyuruları)"
    )

    ozet = TaramaOzeti()
    dislanacak = haric_listesini_temizle(dislanacak_kelime)
    oturum = _oturum()
    uygunlar: List[Dict[str, str]] = []
    belirsizler: List[Dict[str, str]] = []
    gorulen = set()

    try:
        hamlar, toplam = ilanlari_sayfala(
            "",
            baslangic=bas,
            bitis=bit,
            sadece_ihale=True,
            oturum=oturum,
            maksimum=maksimum,
            log=log,
        )
        log(f"   Kaynak toplam (API): {toplam}")

        for ilan in hamlar:
            ozet.taranan += 1
            kayit = _kayda_cevir(ilan, "")
            anahtar = _kayit_anahtari(kayit)
            if not anahtar or anahtar in gorulen:
                ozet.kaydet("tekrar")
                continue
            gorulen.add(anahtar)
            if dislanacak and _dislandi(kayit, dislanacak):
                ozet.kaydet("haric")
                continue
            if suresi_geceni_ele and _suresi_gecti(kayit["İhale Tarihi"]):
                ozet.kaydet("suresi_gecti")
                continue
            sonuc = _alaka_uygula(kayit)
            _alaka_yaz(kayit, sonuc)
            if sonuc.uygun:
                uygunlar.append(kayit)
                ozet.uygun += 1
            elif sonuc.neden == "whitelist_yok" and detay_limiti:
                belirsizler.append(kayit)
            else:
                ozet.kaydet(sonuc.neden or "kara_liste")

        if belirsizler and detay_limiti:
            log(
                f"   📄 {len(belirsizler)} belirsiz ilan; "
                f"en fazla {detay_limiti} tanesinin detayı çekilecek"
            )
            uygunlar.extend(
                _belirsizleri_detaylandir(
                    belirsizler,
                    oturum=oturum,
                    detay_limiti=detay_limiti,
                    ozet=ozet,
                    log=log,
                )
            )
        elif belirsizler:
            ozet.elenen += len(belirsizler)
            ozet.whitelist_yok += len(belirsizler)
    finally:
        oturum.close()

    ozeti_yazdir(ozet, baslangic=bas, bitis=bit, log=log)
    return uygunlar, ozet


def main(argv: Optional[List[str]] = None) -> int:
    ayristirici = argparse.ArgumentParser(description="ilan.gov.tr yedek taraması")
    ayristirici.add_argument(
        "--okas", help="virgülle OKAS kök kodları (varsayılan: proje listesi)"
    )
    ayristirici.add_argument("--kelime", help="doğrudan aranacak kelime(ler), virgülle")
    ayristirici.add_argument(
        "--gun",
        type=int,
        default=VARSAYILAN_GUN_SCRAPER,
        help="son kaç günün ilanları (varsayılan: 10; 0=tarih filtresiz)",
    )
    ayristirici.add_argument(
        "--baslangic",
        help="yayın tarihi başlangıcı (DD.MM.YYYY); --gun yerine kullanılır",
    )
    ayristirici.add_argument(
        "--bitis", help="yayın tarihi bitişi (DD.MM.YYYY); varsayılan: bugün"
    )
    ayristirici.add_argument(
        "--limit", type=int, default=20, help="kelime başına ilan sayısı"
    )
    ayristirici.add_argument(
        "--haric",
        default="",
        help="ek dışlanacak kelimeler, virgülle (hedef listede olanlar yok sayılır)",
    )
    ayristirici.add_argument(
        "--govdede-ara",
        action="store_true",
        help="ilan gövdesini de tara (daha çok sonuç, daha çok gürültü)",
    )
    ayristirici.add_argument(
        "--gecen-hafta",
        action="store_true",
        help="anahtar kelime araması yapmadan geçen haftanın tüm ihale ilanlarını tara",
    )
    args = ayristirici.parse_args(argv)

    gun = None if (args.baslangic or args.bitis) else args.gun

    try:
        if args.gecen_hafta:
            kayitlar, _ozet = gecen_haftayi_tara(
                gun=args.gun if args.gun else VARSAYILAN_GUN_MANUEL,
                baslangic=args.baslangic,
                bitis=args.bitis,
                dislanacak_kelime=args.haric,
            )
            if not kayitlar:
                raise IlanYedekHatasi("Geçen hafta kriterlere uyan ilan bulunamadı.")
        elif args.kelime:
            kelimeler = [k.strip() for k in re.split(r"[;,]", args.kelime) if k.strip()]
            kayitlar = ilan_ihale_tara(
                kelimeler,
                limit=args.limit,
                gun=gun,
                baslangic=args.baslangic,
                bitis=args.bitis,
                dislanacak_kelime=args.haric,
                baslikta_ara=not args.govdede_ara,
            )
            if not kayitlar:
                raise IlanYedekHatasi("Verilen kelimelerle ilan bulunamadı.")
        else:
            kodlar = (
                [k.strip() for k in re.split(r"[;,]", args.okas) if k.strip()]
                if args.okas
                else DEFAULT_OKAS_KODLARI
            )
            kayitlar = okas_ile_tara(
                kodlar,
                limit=args.limit,
                gun=VARSAYILAN_GUN_SCRAPER if gun is None and not args.baslangic and not args.bitis else gun,
                baslangic=args.baslangic,
                bitis=args.bitis,
                dislanacak_kelime=args.haric,
                baslikta_ara=not args.govdede_ara,
            )
    except IlanYedekHatasi as e:
        print(f"⛔ {e}")
        return 1

    print(f"\n✅ {len(kayitlar)} benzersiz ilan:")
    for kayit in kayitlar:
        print(f"   • {kayit['İşin Adı'][:90]}")
        eslesen = kayit.get("Eşleşen Anahtar Kelime") or "-"
        print(
            f"     {kayit['Kurum'][:70]} | {kayit.get('Kategori / Tür') or kayit['İl / Saat']} "
            f"| {eslesen} | {kayit['Link']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
