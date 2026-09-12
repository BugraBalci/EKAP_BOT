#!/usr/bin/env python3
"""EKAP v2 API'si için oturum/çerez/başlık önkoşullarını yöneten katman.

EKAP'ın arama API'si doğrudan çağrıldığında HTTP 428 (Precondition Required)
döndürüyor. 428 "istek hatalı" demez; "kabul etmem için önce sağlanması
gereken bir koşul var" der. EKAP'ta bu koşul üç katmanlı:

  1. Oturum ve WAF çerezleri — F5 (``TS*``), BIG-IP (``BIGipServer*``) ve
     ingress (``INGRESSCOOKIE``) çerezleri yalnızca önce HTML kabuğu GET
     edilince set ediliyor. Çerezsiz giden istek WAF'ın önünden geçemiyor.
  2. Tarayıcı başlıkları — ``Origin``, ``Referer``, ``X-Requested-With`` ve
     ``api-version``; isteğin gerçekten arama ekranından geldiğini gösterir.
  3. ``X-Ekap-Sec-1..4`` — ön yüzün AES-CBC ile ürettiği imza başlıkları.
     Eksikse uç nokta 401 döner, 428 değil; yani 428 görüyorsak sorun
     imzadan önce 1. ve 2. katmandadır.

``onkosul_teshisi`` bu katmanları tek tek ekleyip hangisinin 428'i çözdüğünü
ölçer. Amaç bir korumayı aşmak değil; "EKAP mı kapalı, yoksa imza şeması mı
değişti" sorusunu kesin yanıtlamaktır. Sağlık kontrolü için ekap_health.py
yeterlidir; bu modül yalnızca elle teşhis içindir.

CLI:
    python ekap_session.py --teshis      # önkoşul matrisini ölç
    python ekap_session.py --anahtar     # bundle'dan r8fact anahtarını bul
    python ekap_session.py --ara yazılım # önkoşullarla gerçek bir arama isteği
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import requests

from ekap_http import ekap_oturumu

V2_TABAN = "https://ekapv2.kik.gov.tr"
KABUK_YOLU = "/ekap/search"
IHALE_ARAMA_YOLU = "/b_ihalearama/api/Ihale/GetListByParameters"
OKAS_YOLU = "/b_ihalearama/api/IhtiyacKalemleri/GetAll"

# Başlık adları anahtarla birlikte değişebiliyor; gerekirse ortamdan ezilir.
SEC_BASLIK_ONEKI = os.environ.get("EKAP_SEC_BASLIK_ONEKI", "X-Ekap-Sec-")

# EKAP ön yüzünün imza anahtarı (environment.r8fact). EKAP bunu düzenli
# değiştiriyor; değiştiğinde imzalı istekler 401 döner. Öncelik sırası:
# EKAP_R8FACT_KEY ortam değişkeni → canlı bundle taraması → bu gömülü değer.
SON_BILINEN_ANAHTAR = b"pfS7Xdn3YVkOzs3V79XUc91SD47mQD0g"

# WAF/oturum çerezlerinin hangi katmandan geldiğini adlandırmak için önekler.
WAF_CEREZ_ONEKLERI = {
    "TS": "F5 ASM (WAF) oturum çerezi",
    "BIGipServer": "F5 LTM sunucu yapışkanlığı",
    "INGRESSCOOKIE": "nginx ingress yapışkanlığı",
    "ASP.NET": "eski EKAP oturumu",
}

TARAYICI_BASLIKLARI = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": V2_TABAN,
    "Referer": f"{V2_TABAN}{KABUK_YOLU}",
    "api-version": "v1",
    "X-Requested-With": "XMLHttpRequest",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

_PAKET_DESENI = re.compile(r'<script[^>]+src="([^"]+\.js)"')
_R8FACT_DESENI = re.compile(r"""r8fact["']?\s*[:=]\s*["']([^"']{16,64})["']""")


class EkapOnkosulHatasi(RuntimeError):
    """API önkoşulu karşılanmadı (tipik olarak HTTP 428)."""

    def __init__(
        self, durum_kodu: Optional[int], govde: str = "", ipucu: str = ""
    ) -> None:
        mesaj = f"EKAP API önkoşulu karşılanmadı (HTTP {durum_kodu})"
        if ipucu:
            mesaj += f" — {ipucu}"
        super().__init__(mesaj)
        self.durum_kodu = durum_kodu
        self.govde = govde
        self.ipucu = ipucu


@dataclass
class TeshisSatiri:
    """Önkoşul matrisinin tek bir satırı."""

    ad: str
    durum_kodu: Optional[int] = None
    sure_ms: int = 0
    cerez_sayisi: int = 0
    imzali: bool = False
    ozet: str = ""

    def satir(self) -> str:
        isaret = "✅" if self.durum_kodu == 200 else "❌"
        kod = self.durum_kodu if self.durum_kodu is not None else "-"
        return (
            f"{isaret} {self.ad:34s} HTTP {kod} {self.sure_ms}ms "
            f"çerez={self.cerez_sayisi} imza={'var' if self.imzali else 'yok'}"
            f"{'  | ' + self.ozet if self.ozet else ''}"
        )


class EkapSession:
    """Önce kabuğu GET edip çerez toplayan, sonra API'yi çağıran oturum."""

    def __init__(
        self,
        *,
        timeout: float = 25.0,
        dogrula_ssl: Optional[bool] = None,
        anahtar: Optional[bytes] = None,
        log: Callable[[str], None] = print,
    ) -> None:
        self.timeout = timeout
        self.dogrula_ssl = dogrula_ssl
        self.log = log
        self._anahtar = anahtar
        self._oturum: Optional[requests.Session] = None
        self._hazir = False

    @property
    def oturum(self) -> requests.Session:
        if self._oturum is None:
            self._oturum = ekap_oturumu(dogrula=self.dogrula_ssl)
        return self._oturum

    def kapat(self) -> None:
        if self._oturum is not None:
            self._oturum.close()
            self._oturum = None
        self._hazir = False

    def __enter__(self) -> "EkapSession":
        return self

    def __exit__(self, *_) -> None:
        self.kapat()

    # --- 1. katman: oturum ve WAF çerezleri -------------------------------

    def hazirla(self, zorla: bool = False) -> Dict[str, str]:
        """SPA kabuğunu GET ederek oturum/WAF çerezlerini toplar."""
        if self._hazir and not zorla:
            return self.cerezler
        if zorla:
            self.oturum.cookies.clear()

        yanit = self.oturum.get(
            f"{V2_TABAN}{KABUK_YOLU}",
            timeout=self.timeout,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Upgrade-Insecure-Requests": "1",
            },
        )
        waf = self.waf_cerezleri
        self.log(
            f"🍪 Kabuk GET → HTTP {yanit.status_code}, {len(self.cerezler)} çerez "
            f"({', '.join(waf) if waf else 'WAF çerezi yok'})"
        )
        if not waf:
            self.log(
                "⚠️ WAF çerezi gelmedi; API büyük olasılıkla 428 döner. "
                "Kabuk isteği bir ara sunucu tarafından kesiliyor olabilir."
            )
        self._hazir = True
        return self.cerezler

    @property
    def cerezler(self) -> Dict[str, str]:
        return {c.name: (c.value or "") for c in self.oturum.cookies}

    @property
    def waf_cerezleri(self) -> Dict[str, str]:
        """Toplanan çerezleri ait oldukları WAF katmanına göre etiketler."""
        bulunan: Dict[str, str] = {}
        for ad in self.cerezler:
            for onek, aciklama in WAF_CEREZ_ONEKLERI.items():
                if ad.startswith(onek):
                    bulunan[ad] = aciklama
                    break
        return bulunan

    # --- 3. katman: imza anahtarı ve başlıkları ---------------------------

    def r8fact_anahtari(self, bundle_tara: bool = True) -> bytes:
        """Kullanılacak AES anahtarını belirler.

        Sıra: açıkça verilen anahtar → EKAP_R8FACT_KEY → canlı bundle →
        gömülü son bilinen anahtar.
        """
        if self._anahtar:
            return self._anahtar

        ortam = (os.environ.get("EKAP_R8FACT_KEY") or "").strip()
        if ortam:
            self._anahtar = ortam.encode()
            return self._anahtar

        if bundle_tara:
            bulunan = self._bundle_anahtari()
            if bulunan:
                self._anahtar = bulunan
                return self._anahtar

        self.log(
            "ℹ️ Anahtar bundle'da bulunamadı; gömülü son bilinen anahtar denenecek. "
            "EKAP anahtarı değiştirdiyse EKAP_R8FACT_KEY ile güncel değeri geçin."
        )
        self._anahtar = SON_BILINEN_ANAHTAR
        return self._anahtar

    def _bundle_anahtari(self) -> Optional[bytes]:
        """Kabuk paketlerinde ``r8fact`` değerini arar.

        Anahtarı sabit yazmak yerine canlı paketten okumak, EKAP anahtarı
        döndürdüğünde modülün kendini onarmasını sağlıyor.
        """
        try:
            kabuk = self.oturum.get(f"{V2_TABAN}{KABUK_YOLU}", timeout=self.timeout)
            kabuk.raise_for_status()
        except requests.RequestException as e:
            self.log(f"⚠️ Kabuk paket listesi alınamadı: {e}")
            return None

        for yol in _PAKET_DESENI.findall(kabuk.text or ""):
            url = yol if yol.startswith("http") else f"{V2_TABAN}/{yol.lstrip('/')}"
            try:
                paket = self.oturum.get(url, timeout=self.timeout)
                if paket.status_code != 200:
                    continue
                eslesme = _R8FACT_DESENI.search(paket.text or "")
            except requests.RequestException:
                continue
            if eslesme:
                anahtar = eslesme.group(1)
                self.log(f"🔑 r8fact anahtarı bulundu: {url} ({len(anahtar)} bayt)")
                return anahtar.encode()
        return None

    def guvenlik_basliklari(self) -> Dict[str, str]:
        """AES-CBC ile imzalanmış X-Ekap-Sec başlıklarını üretir."""
        try:
            from cryptography.hazmat.primitives import padding
            from cryptography.hazmat.primitives.ciphers import (
                Cipher,
                algorithms,
                modes,
            )
        except ImportError:
            self.log(
                "⚠️ cryptography kurulu değil; imzalı başlıklar üretilemiyor "
                "(pip install cryptography)."
            )
            return {}

        anahtar = self.r8fact_anahtari()
        iv = os.urandom(16)

        def sifrele(metin: str) -> str:
            dolgulayici = padding.PKCS7(128).padder()
            dolgulu = dolgulayici.update(metin.encode()) + dolgulayici.finalize()
            sifreleyici = Cipher(algorithms.AES(anahtar), modes.CBC(iv)).encryptor()
            return base64.b64encode(
                sifreleyici.update(dolgulu) + sifreleyici.finalize()
            ).decode()

        guid = str(uuid.uuid4())
        return {
            f"{SEC_BASLIK_ONEKI}1": sifrele(guid),
            f"{SEC_BASLIK_ONEKI}2": base64.b64encode(iv).decode(),
            f"{SEC_BASLIK_ONEKI}3": guid,
            f"{SEC_BASLIK_ONEKI}4": sifrele(str(int(time.time() * 1000))),
        }

    # --- İstek ------------------------------------------------------------

    def api_istek(
        self,
        endpoint: str,
        govde: Dict,
        *,
        imzali: bool = True,
        yeniden_dene: bool = True,
    ) -> requests.Response:
        """Önkoşulları kurup POST atar; 428 alırsa çerezleri yenileyip bir kez tekrar dener."""
        self.hazirla()

        basliklar = dict(TARAYICI_BASLIKLARI)
        if imzali:
            basliklar.update(self.guvenlik_basliklari())

        yanit = self.oturum.post(
            f"{V2_TABAN}{endpoint}",
            json=govde,
            headers=basliklar,
            timeout=self.timeout,
        )

        if yanit.status_code == 428 and yeniden_dene:
            self.log("🔄 428 alındı; oturum çerezleri yenilenip istek bir kez tekrarlanıyor.")
            self.hazirla(zorla=True)
            return self.api_istek(endpoint, govde, imzali=imzali, yeniden_dene=False)

        return yanit

    def api_json(self, endpoint: str, govde: Dict, *, imzali: bool = True) -> Dict:
        """API'yi çağırıp JSON döndürür; önkoşul hatasında EkapOnkosulHatasi atar."""
        yanit = self.api_istek(endpoint, govde, imzali=imzali)

        if yanit.status_code == 200:
            return yanit.json()

        ipucu = ""
        if yanit.status_code == 401:
            ipucu = "imzalı başlıklar reddedildi"
            if imzali:
                ipucu += (
                    "; imza anahtarı (r8fact) veya başlık adları değişmiş olabilir — "
                    "python ekap_session.py --teshis ile katmanları ölçün"
                )
        elif yanit.status_code == 428:
            ipucu = "çerez/başlık katmanı WAF tarafından kabul edilmedi"
        elif yanit.status_code == 406:
            ipucu = "WAF isteği tamamen eledi (ekap_health.py ile sunucu durumuna bakın)"

        raise EkapOnkosulHatasi(yanit.status_code, (yanit.text or "")[:600], ipucu)

    def onkosul_teshisi(
        self, endpoint: str = IHALE_ARAMA_YOLU, govde: Optional[Dict] = None
    ) -> List[TeshisSatiri]:
        """Katmanları tek tek ekleyerek 428'in hangi adımda düzeldiğini ölçer."""
        govde = govde or arama_govdesi(take=1)
        url = f"{V2_TABAN}{endpoint}"
        satirlar: List[TeshisSatiri] = []

        def olc(ad: str, basliklar: Dict[str, str], imzali: bool, cerezli: bool):
            basla = time.monotonic()
            try:
                if cerezli:
                    yanit = self.oturum.post(
                        url, json=govde, headers=basliklar, timeout=self.timeout
                    )
                else:
                    # Ayrı bir oturum: çerez taşımadan aynı isteği at.
                    with ekap_oturumu(dogrula=self.dogrula_ssl) as temiz:
                        yanit = temiz.post(
                            url, json=govde, headers=basliklar, timeout=self.timeout
                        )
                sure = int((time.monotonic() - basla) * 1000)
                ozet = "" if yanit.status_code == 200 else (yanit.text or "(boş gövde)")[:80]
                satirlar.append(
                    TeshisSatiri(
                        ad,
                        yanit.status_code,
                        sure,
                        len(self.cerezler) if cerezli else 0,
                        imzali,
                        ozet.replace("\n", " ").strip(),
                    )
                )
            except requests.RequestException as e:
                sure = int((time.monotonic() - basla) * 1000)
                satirlar.append(
                    TeshisSatiri(ad, None, sure, 0, imzali, f"{type(e).__name__}: {e}")
                )

        olc("1) çerezsiz + başlıksız", {"Content-Type": "application/json"}, False, False)
        olc("2) çerezsiz + tarayıcı başlıkları", dict(TARAYICI_BASLIKLARI), False, False)

        self.hazirla(zorla=True)
        olc("3) oturum çerezleri + başlıklar", dict(TARAYICI_BASLIKLARI), False, True)

        imza = self.guvenlik_basliklari()
        if imza:
            olc(
                "4) çerez + başlık + imza",
                {**TARAYICI_BASLIKLARI, **imza},
                True,
                True,
            )
        else:
            self.log("ℹ️ 4. adım atlandı: imzalı başlık üretilemedi.")

        return satirlar


def arama_govdesi(
    *,
    arama_metni: str = "",
    okas_kodlari: Optional[List[str]] = None,
    durum_idler: Optional[List[int]] = None,
    skip: int = 0,
    take: int = 20,
) -> Dict:
    """GetListByParameters'ın beklediği en küçük geçerli istek gövdesi."""
    return {
        "searchText": arama_metni or "",
        "filterType": {
            "ikNdeAra": True,
            "ihaleAdindaAra": True,
            "ihaleIlanindaAra": False,
            "teknikSartnamedeAra": False,
            "idariSartnamedeAra": False,
            "benzerIsMaddesindeAra": False,
            "isinYapilacagiYerMaddesindeAra": False,
            "nitelikTurMiktarMaddesindeAra": False,
            "ihaleBilgilerindeAra": False,
            "sozlesmeTasarisindaAra": False,
            "teklifCetvelindeAra": False,
        },
        "searchType": "GirdigimGibi",
        "yasaKapsami4734List": [],
        "ihaleTuruIdList": [],
        "ihaleUsulIdList": [],
        "ihaleUsulAltIdList": [],
        "ihaleIlIdList": [],
        # Canlı EKAP: 2 = İhale İlanı Yayımlanmış, Katılıma Açık
        "ihaleDurumIdList": list(durum_idler or []),
        "idareIdList": [],
        "ihaleIlanTuruIdList": [],
        "teklifTuruIdList": [],
        "asiriDusukTeklifIdList": [],
        "istisnaMaddeIdList": [],
        "okasBransKodList": list(okas_kodlari or []),
        "okasBransAdiList": [],
        "titubbKodList": [],
        "gmdnKodList": [],
        "orderBy": "ihaleTarihi",
        "siralamaTipi": "desc",
        "paginationSkip": skip,
        "paginationTake": take,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ayristirici = argparse.ArgumentParser(description="EKAP API önkoşul aracı")
    ayristirici.add_argument(
        "--teshis", action="store_true", help="önkoşul matrisini ölç ve bas"
    )
    ayristirici.add_argument(
        "--anahtar", action="store_true", help="bundle içinden r8fact anahtarını ara"
    )
    ayristirici.add_argument(
        "--ara", metavar="METIN", help="önkoşullarla gerçek bir arama isteği at"
    )
    ayristirici.add_argument("--timeout", type=float, default=25.0)
    args = ayristirici.parse_args(argv)

    if not (args.teshis or args.anahtar or args.ara):
        ayristirici.print_help()
        return 0

    with EkapSession(timeout=args.timeout) as oturum:
        if args.anahtar:
            anahtar = oturum.r8fact_anahtari()
            print(f"🔑 Kullanılacak anahtar uzunluğu: {len(anahtar)} bayt")

        if args.teshis:
            print("🧪 EKAP API önkoşul matrisi:")
            for satir in oturum.onkosul_teshisi():
                print(f"   {satir.satir()}")
            print(
                "   ➜ 200 dönen ilk satır, hangi önkoşulun yeterli olduğunu gösterir. "
                "Hepsi 428 ise imza anahtarı/başlık adları değişmiştir."
            )

        if args.ara:
            try:
                veri = oturum.api_json(
                    IHALE_ARAMA_YOLU, arama_govdesi(arama_metni=args.ara, take=5)
                )
            except EkapOnkosulHatasi as e:
                print(f"⛔ {e}")
                return 1
            print(json.dumps(veri, ensure_ascii=False, indent=2)[:4000])

    return 0


if __name__ == "__main__":
    sys.exit(main())
