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

Kullanım:
    from ilan_fallback import okas_ile_tara, IlanYedekHatasi
    kayitlar = okas_ile_tara(okas_kodlari, limit=20, gun=14)

CLI:
    python ilan_fallback.py --okas 72200000,32420000
    python ilan_fallback.py --kelime yazılım,ağ donanımı --gun 7
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Optional, Sequence

import requests

from ekap_http import (
    dogrulamayi_gevset,
    ekap_oturumu,
    sertifika_zinciri_hatasi_mi,
)
from okas_defaults import (
    DEFAULT_OKAS_KODLARI,
    GENEL_ANAHTAR_KELIMELER,
    OKAS_ANAHTAR_KELIME,
)

ILAN_TABAN = "https://www.ilan.gov.tr"
ARAMA_YOLU = "/api/api/services/app/Ad/AdsByFilter"
KAYNAK_ADI = "ilan.gov.tr"

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


def tr_lower(metin: str) -> str:
    """data_scraper.tr_lower ile aynı Türkçe küçültme (İ/I ayrımı korunur)."""
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


def _kayda_cevir(ilan: Dict, kelime: str) -> Dict[str, str]:
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

    yayim = _tarih_metni(ilan.get("publishStartDate"))
    # Yayım tarihi ihale tarihi yerine konmaz: takvim hatırlatıcısı ihaleden 7
    # gün önceye ve ihale saatinden 1 saat önceye kurulduğu için yayım tarihi
    # kullanılırsa hatırlatıcı geçmişe düşer. Gerçek ihale günü yoksa alan boş bırakılır.
    tarih = _ihale_tarihi(filtreler)
    ikn = _filtreden_al(filtreler, IKN_ALANLARI)
    yol = (ilan.get("urlStr") or "").strip()
    link = f"{ILAN_TABAN}{yol}" if yol.startswith("/") else yol

    durum = "İlan yayımlandı"
    if yayim:
        durum += f" ({yayim})"

    return {
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
        "Tür": (filtreler.get("İlan Türü") or "İHALE").strip(),
        "İl": il or ilce,
        "Durum": durum,
        "Link": link,
        "Kaynak": KAYNAK_ADI,
        "Anahtar Kelime": kelime,
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


def ilan_ara(
    kelime: str,
    *,
    limit: int = 20,
    gun: Optional[int] = None,
    sadece_ihale: bool = True,
    baslikta_ara: bool = False,
    il_plaka: Optional[int] = None,
    timeout: float = 20.0,
    oturum: Optional[requests.Session] = None,
) -> List[Dict]:
    """Tek bir anahtar kelime için ilan.gov.tr araması yapar (ham kayıtlar)."""
    kendi_oturumu = oturum is None
    oturum = oturum or _oturum()

    anahtarlar: Dict[str, List] = {"t" if baslikta_ara else "q": [kelime]}
    if sadece_ihale:
        anahtarlar["ats"] = [IHALE_TURU_ID]
    if il_plaka is not None:
        anahtarlar["aci"] = [int(il_plaka)]
    if gun:
        bugun = datetime.now(timezone.utc)
        anahtarlar["ppdmin"] = [(bugun - timedelta(days=int(gun))).strftime("%d.%m.%Y")]
        anahtarlar["ppdmax"] = [bugun.strftime("%d.%m.%Y")]

    govde = {
        "keys": anahtarlar,
        "skipCount": 0,
        "maxResultCount": max(1, min(int(limit), 50)),
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
        return ((yanit.json() or {}).get("result") or {}).get("ads") or []
    finally:
        if kendi_oturumu:
            oturum.close()


def ilan_ihale_tara(
    kelimeler: Sequence[str],
    *,
    limit: int = 20,
    gun: Optional[int] = None,
    dislanacak_kelime: str = "",
    baslikta_ara: bool = False,
    il_plaka: Optional[int] = None,
    alaka_suz: bool = True,
    log: Callable[[str], None] = print,
) -> List[Dict[str, str]]:
    """Verilen kelimeleri sırayla tarar ve tekilleştirilmiş kayıt listesi döndürür."""
    dislanacak = [tr_lower(k.strip()) for k in (dislanacak_kelime or "").split(",") if k.strip()]
    desen = alaka_deseni(kelimeler) if alaka_suz else None

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
                kayit = _kayda_cevir(ilan, kelime)
                anahtar = kayit["İKN"] if kayit["İKN"] != "-" else kayit["Link"]
                if not anahtar or anahtar in gorulen:
                    continue
                if dislanacak and _dislandi(kayit, dislanacak):
                    elendi += 1
                    continue
                if desen is not None and not desen.search(tr_lower(kayit["İşin Adı"])):
                    elendi += 1
                    continue
                if _suresi_gecti(kayit["İhale Tarihi"]):
                    elendi += 1
                    continue
                gorulen.add(anahtar)
                kayitlar.append(kayit)
                eklenen += 1

            log(
                f"   [{sira}/{len(kelimeler)}] ilan.gov.tr '{kelime}': "
                f"{len(ilanlar)} ilan, {eklenen} yeni, {elendi} elendi"
            )
    finally:
        oturum.close()

    return kayitlar


def okas_ile_tara(
    okas_kodlari: Sequence[str],
    *,
    limit: int = 20,
    gun: Optional[int] = 14,
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
        dislanacak_kelime=dislanacak_kelime,
        baslikta_ara=baslikta_ara,
        log=log,
    )
    if not kayitlar:
        raise IlanYedekHatasi(
            f"{len(kelimeler)} anahtar kelime tarandı ama hiç uygun ilan bulunamadı."
        )
    return kayitlar


def main(argv: Optional[List[str]] = None) -> int:
    ayristirici = argparse.ArgumentParser(description="ilan.gov.tr yedek taraması")
    ayristirici.add_argument(
        "--okas", help="virgülle OKAS kök kodları (varsayılan: proje listesi)"
    )
    ayristirici.add_argument("--kelime", help="doğrudan aranacak kelime(ler), virgülle")
    ayristirici.add_argument("--gun", type=int, default=14, help="son kaç günün ilanları")
    ayristirici.add_argument(
        "--limit", type=int, default=20, help="kelime başına ilan sayısı"
    )
    ayristirici.add_argument(
        "--haric", default="lisans, araba", help="dışlanacak kelimeler, virgülle"
    )
    ayristirici.add_argument(
        "--govdede-ara",
        action="store_true",
        help="ilan gövdesini de tara (daha çok sonuç, daha çok gürültü)",
    )
    args = ayristirici.parse_args(argv)

    try:
        if args.kelime:
            kelimeler = [k.strip() for k in re.split(r"[;,]", args.kelime) if k.strip()]
            kayitlar = ilan_ihale_tara(
                kelimeler,
                limit=args.limit,
                gun=args.gun,
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
                gun=args.gun,
                dislanacak_kelime=args.haric,
                baslikta_ara=not args.govdede_ara,
            )
    except IlanYedekHatasi as e:
        print(f"⛔ {e}")
        return 1

    print(f"\n✅ {len(kayitlar)} benzersiz ilan:")
    for kayit in kayitlar:
        print(f"   • {kayit['İşin Adı'][:90]}")
        print(f"     {kayit['Kurum'][:70]} | {kayit['İl / Saat']} | {kayit['Link']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
