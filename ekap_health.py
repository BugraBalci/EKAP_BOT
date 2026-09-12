#!/usr/bin/env python3
"""EKAP erişilebilirlik ön kontrolü (health-check).

Selenium ya da imzalı API çağrıları gibi pahalı adımlar başlamadan önce
EKAP'ın gerçekten ayakta olup olmadığını hafif HTTP istekleriyle doğrular.

Neden "200 geldi mi" kontrolü yetmez: EKAP kapalıyken bile
``ekapv2.kik.gov.tr/ekap/search`` 200 ve geçerli HTML döndürüyor. Angular
kabuğu boş bir ``<web-root>`` etiketinden ibaret olduğu için sayfa "açıldı"
görünürken ekran bomboş kalıyor. Asıl belirleyici sinyal, kabuğun yüklediği
module-federation paketinin servis edilebiliyor olmasıdır: kapalı dönemde o
istek JavaScript yerine EKAP hata sayfasını (HTTP 406 + HTML) döndürüyor,
Angular bootstrap'i patlıyor ve DOM hiç dolmuyor.

Kontrol edilen katmanlar:
  1. Eski EKAP (ekap.kik.gov.tr) — "Sayfada hata oluştu" şablonu dönüyor mu
  2. v2 kabuğu — HTML'i ve ana paketi (main.<hash>.js) indirilebiliyor mu
  3. federation manifesti ve uzak modüller — arama modülü gerçekten servis
     ediliyor mu (boş ekranın kök nedeni burada görünür)
  4. API önkoşulu — 428/401 ayrımı (imza üretilmez, önkoşul atlatılmaz)

Kullanım:
    from ekap_health import saglik_kontrol, ekap_hazir_mi

    rapor = saglik_kontrol()
    rapor.yazdir()
    if rapor.saglikli:
        ...  # Selenium taramasını başlat

CLI:
    python ekap_health.py                 # insan okunur rapor
    python ekap_health.py --api --json    # API önkoşulunu da ölç, JSON bas
    python ekap_health.py --bekle         # ayağa kalkmasını backoff ile bekle
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import requests

from ekap_http import (
    DEFAULT_USER_AGENT,
    ekap_oturumu,
    govde_oku,
    tls_hatasi_mi,
)

V2_TABAN = "https://ekapv2.kik.gov.tr"
V2_KABUK_YOLU = "/ekap/search"
MANIFEST_YOLU = "/assets/manifest/module-federation.manifest.prod.json"
ESKI_EKAP_URL = "https://ekap.kik.gov.tr/EKAP/Default.aspx"
API_YOLU = "/b_ihalearama/api/Ihale/GetListByParameters"

# Arama ekranını çizen uzak modül; boş ekran için belirleyici olan bu.
ARAMA_REMOTE_ADI = "ekap"

# Uzak modüllerin module-federation giriş dosyası. Dosya adı önemli: EKAP
# bilinmeyen yollara (ör. remoteEntry.json) 406 + kendi hata şablonunu
# döndürüyor, bu da yanlışlıkla "sunucu kapalı" gibi okunuyor. Gerçek giriş
# dosyası .mjs uzantılı.
REMOTE_GIRIS_DOSYASI = "remoteEntry.mjs"

# Bot koruması sorgu sayfasının imzaları. Bu bir arıza değil: sunucu ayakta
# ama otomasyona doğrulama sayfası gösteriyor. Ayrı raporlanmalı, yoksa
# "EKAP kapalı" gibi görünüp yedek hatta gereksiz düşülür.
BOT_KORUMASI_NOTU = "bot koruması sorgu sayfası"
GUVENLIK_KONTROLU_KALIPLARI = (
    "kısa bir güvenlik kontrolü",
    "kisa bir guvenlik kontrolu",
    "güvenlik kontrolünü tamamlayın",
    "guvenlik kontrolunu tamamlayin",
    "just a moment",
    "checking your browser",
    "cf-browser-verification",
)

# EKAP'ın arıza/bakım şablonunun imzaları. Gövde 200 ile de dönebildiği için
# durum kodunun yanında gövde kalıbına da bakılır.
HATA_KALIPLARI = (
    "sayfada hata oluştu",
    "bu olay için referans numarasi",
    "bu olay icin referans numarasi",
    "çağrı merkezini arayabilirsiniz",
    "cagri merkezini arayabilirsiniz",
    "bakım çalışması",
    "bakim calismasi",
    "geçici olarak hizmet",
    "gecici olarak hizmet",
    "service unavailable",
    "gateway timeout",
    "precondition required",
)

# Angular index.html'inden ana paketi çıkarmak için.
_ANA_PAKET_DESENI = re.compile(r'<script[^>]+src="([^"]*main\.[0-9a-f]+\.js)"')
_HERHANGI_PAKET_DESENI = re.compile(r'<script[^>]+src="([^"]+\.js)"')


@dataclass
class Prob:
    """Tek bir HTTP kontrolünün sonucu."""

    ad: str
    url: str
    saglikli: bool
    kritik: bool = False
    durum_kodu: Optional[int] = None
    sure_ms: int = 0
    icerik_turu: str = ""
    notlar: List[str] = field(default_factory=list)

    def satir(self) -> str:
        isaret = "✅" if self.saglikli else ("❌" if self.kritik else "⚠️")
        kod = f" HTTP {self.durum_kodu}" if self.durum_kodu else ""
        notlar = f"  | {'; '.join(self.notlar)}" if self.notlar else ""
        return f"{isaret} {self.ad}{kod} ({self.sure_ms}ms){notlar}"


@dataclass
class SaglikRaporu:
    problar: List[Prob] = field(default_factory=list)
    saglikli: bool = False
    ozet: str = ""

    def prob(self, ad: str) -> Optional[Prob]:
        for p in self.problar:
            if p.ad == ad:
                return p
        return None

    def bozuk_kritikler(self) -> List[Prob]:
        return [p for p in self.problar if p.kritik and not p.saglikli]

    def uyarilar(self) -> List[Prob]:
        return [p for p in self.problar if not p.kritik and not p.saglikli]

    @property
    def bot_korumasi(self) -> bool:
        """Engel sunucu arızası değil, otomasyona gösterilen doğrulama sayfası mı.

        Ayrım eylem değiştiriyor: arıza ise beklemek gerekir, bot koruması ise
        EKAP_UNDETECTED=1 ile denemek anlamlı olur.
        """
        return any(
            BOT_KORUMASI_NOTU in not_ for p in self.problar for not_ in p.notlar
        )

    def yazdir(self, log: Callable[[str], None] = print) -> None:
        log("🩺 EKAP sağlık kontrolü:")
        for p in self.problar:
            log(f"   {p.satir()}")
        log(f"   ➜ {self.ozet}")

    def sozluk(self) -> Dict:
        return {
            "saglikli": self.saglikli,
            "ozet": self.ozet,
            "problar": [asdict(p) for p in self.problar],
        }


def _hata_kalibi_var(govde: str) -> Optional[str]:
    alt = (govde or "").lower()
    for kalip in HATA_KALIPLARI:
        if kalip in alt:
            return kalip
    return None


def _guvenlik_kontrolu_var(govde: str) -> Optional[str]:
    alt = (govde or "").lower()
    for kalip in GUVENLIK_KONTROLU_KALIPLARI:
        if kalip in alt:
            return kalip
    return None


def _istek(
    oturum: requests.Session,
    url: str,
    timeout: float = 8.0,
    bayt: int = 4096,
) -> Tuple[Optional[requests.Response], str, int, Optional[str]]:
    """GET at, gövdenin yalnızca ilk bloğunu oku. Dönüş: (yanıt, govde, ms, hata)."""
    basla = time.monotonic()
    try:
        yanit = oturum.get(
            url,
            timeout=timeout,
            stream=True,
            allow_redirects=False,
            headers={"Cache-Control": "no-cache"},
        )
        try:
            govde = govde_oku(yanit, bayt)
        finally:
            yanit.close()
        return yanit, govde, int((time.monotonic() - basla) * 1000), None
    except requests.exceptions.SSLError as e:
        sure = int((time.monotonic() - basla) * 1000)
        return None, "", sure, f"SSL hatası: {e}"
    except requests.exceptions.Timeout:
        sure = int((time.monotonic() - basla) * 1000)
        return None, "", sure, f"zaman aşımı (>{timeout:.0f} sn)"
    except (requests.RequestException, ValueError, OSError) as e:
        sure = int((time.monotonic() - basla) * 1000)
        return None, "", sure, f"{type(e).__name__}: {e}"


def _prob_calistir(
    oturum: requests.Session,
    ad: str,
    url: str,
    *,
    kritik: bool = False,
    timeout: float = 8.0,
    bayt: int = 4096,
    dogrulayici: Optional[Callable[[requests.Response, str], List[str]]] = None,
) -> Prob:
    yanit, govde, sure, hata = _istek(oturum, url, timeout=timeout, bayt=bayt)

    if yanit is None:
        notlar = [hata or "bağlantı kurulamadı"]
        if hata and tls_hatasi_mi(hata):
            notlar.append(
                "TLS el sıkışması — sunucu arızası olmayabilir, "
                "ekap_http.EKAP_CIPHERS listesine bakın"
            )
        return Prob(ad, url, False, kritik, None, sure, "", notlar)

    icerik = (yanit.headers.get("Content-Type") or "").split(";")[0].strip()
    notlar: List[str] = []
    saglikli = True

    sorgu = _guvenlik_kontrolu_var(govde)
    if sorgu:
        saglikli = False
        notlar.append(f"{BOT_KORUMASI_NOTU}: {sorgu!r}")

    kalip = _hata_kalibi_var(govde)
    if yanit.status_code == 428:
        saglikli = False
        notlar.append("428 Precondition Required — imzalı başlık/oturum önkoşulu")
    elif yanit.status_code >= 500:
        saglikli = False
        notlar.append("sunucu tarafı hata (5xx)")
    elif yanit.status_code >= 400:
        saglikli = False
        notlar.append("istemci tarafı hata (4xx)")
    if kalip:
        saglikli = False
        notlar.append(f"gövdede arıza kalıbı: {kalip!r}")

    if saglikli and dogrulayici is not None:
        ek = dogrulayici(yanit, govde) or []
        if ek:
            saglikli = False
            notlar.extend(ek)

    return Prob(ad, url, saglikli, kritik, yanit.status_code, sure, icerik, notlar)


def _javascript_dogrula(yanit: requests.Response, govde: str) -> List[str]:
    """Paketin gerçekten JavaScript olduğunu doğrular.

    EKAP kapalıyken paket yollarına HTML hata sayfası servis ediliyor. Tarayıcı
    bunu "Failed to load module script" ile reddediyor ve Angular hiç
    başlamıyor — boş ekranın doğrudan sebebi bu.
    """
    alt = (govde or "").lstrip().lower()
    if alt.startswith("<!doctype") or alt.startswith("<html"):
        return [
            "JavaScript yerine HTML döndü — paket dağıtımda yok, "
            "Angular modülü yüklenemez ve arayüz boş kalır"
        ]
    icerik = (yanit.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if icerik and "javascript" not in icerik and icerik != "text/plain":
        return [f"beklenmeyen içerik türü: {icerik or 'bilinmiyor'}"]
    return []


def _ana_paket_url(govde: str, taban: str, alt_yol: str = "") -> Optional[str]:
    """Bir Angular index.html'inden ana paketin (main.<hash>.js) tam URL'sini çıkarır."""
    eslesme = _ANA_PAKET_DESENI.search(govde or "")
    if eslesme is None:
        eslesme = _HERHANGI_PAKET_DESENI.search(govde or "")
    if eslesme is None:
        return None

    yol = eslesme.group(1)
    if yol.startswith("http"):
        return yol
    if yol.startswith("/"):
        return f"{taban}{yol}"
    onek = f"{taban}{alt_yol}".rstrip("/")
    return f"{onek}/{yol}"


def _kabuk_problari(
    oturum: requests.Session, timeout: float
) -> Tuple[List[Prob], Optional[str]]:
    """v2 kabuğunun HTML'ini ve ana paketini doğrular."""

    def dogrula(_yanit: requests.Response, govde: str) -> List[str]:
        notlar: List[str] = []
        if "<web-root" not in govde and "<app-root" not in govde:
            notlar.append(
                "SPA kök elemanı (<web-root>) yok — kabuk HTML'i beklenenden farklı"
            )
        if _ana_paket_url(govde, V2_TABAN) is None:
            notlar.append("kabuk HTML'inde ana paket (main.*.js) referansı yok")
        return notlar

    kabuk_url = f"{V2_TABAN}{V2_KABUK_YOLU}"
    # Angular kök etiketi ~13 KB'lık gövdenin sonunda, satır içi CSS'ten sonra
    # geliyor; kısa okuma penceresi onu kaçırır ve sahte uyarı üretir.
    kabuk = _prob_calistir(
        oturum,
        "v2 kabuğu",
        kabuk_url,
        kritik=True,
        timeout=timeout,
        bayt=65536,
        dogrulayici=dogrula,
    )
    problar = [kabuk]

    _yanit, govde, _sure, _hata = _istek(oturum, kabuk_url, timeout=timeout, bayt=65536)
    paket_url = _ana_paket_url(govde, V2_TABAN)
    if paket_url:
        problar.append(
            _prob_calistir(
                oturum,
                "kabuk paketi",
                paket_url,
                kritik=True,
                timeout=timeout,
                dogrulayici=_javascript_dogrula,
            )
        )
    return problar, paket_url


def _remote_yollari(
    oturum: requests.Session, timeout: float
) -> Tuple[Prob, Dict[str, str]]:
    """Manifesti çek, uzak modül adlarını ve yollarını döndür."""
    yollar: Dict[str, str] = {}

    def dogrula(_yanit: requests.Response, govde: str) -> List[str]:
        nonlocal yollar
        try:
            veri = json.loads(govde)
        except ValueError as e:
            return [f"manifest JSON ayrıştırılamadı: {e}"]
        if not isinstance(veri, dict) or not veri:
            return ["manifest boş ya da beklenen sözlük biçiminde değil"]
        yollar = {
            str(ad): str(yol)
            for ad, yol in veri.items()
            if isinstance(yol, str) and yol.startswith("/") and yol.rstrip("/")
        }
        return []

    prob = _prob_calistir(
        oturum,
        "federation manifest",
        f"{V2_TABAN}{MANIFEST_YOLU}",
        kritik=True,
        timeout=timeout,
        bayt=8192,
        dogrulayici=dogrula,
    )
    return prob, yollar


def _remote_problari(
    oturum: requests.Session, ad: str, yol: str, timeout: float
) -> List[Prob]:
    """Uzak modülün module-federation giriş dosyasını doğrular.

    Modülün kök dizinini (``/f_ekap/``) yoklamak yanıltıcı: o yol dağıtımda
    yok ve WAF 406 + EKAP hata şablonu döndürüyor. Angular'ın gerçekten
    indirdiği dosya ``remoteEntry.mjs``; render edilip edilmeyeceğini
    belirleyen de bu.
    """
    taban_yol = yol.rstrip("/")
    if not taban_yol:
        # "shell" remote'u kabuğun kendisi; "kabuk paketi" probu onu kapsıyor.
        return []

    url = f"{V2_TABAN}{taban_yol}/{REMOTE_GIRIS_DOSYASI}"
    return [
        _prob_calistir(
            oturum,
            f"remote:{ad} girişi",
            url,
            kritik=(ad == ARAMA_REMOTE_ADI),
            timeout=timeout,
            dogrulayici=_javascript_dogrula,
        )
    ]


def _api_onkosul_probu(oturum: requests.Session, timeout: float) -> Prob:
    """API uç noktasının durumunu raporlar; imza üretmez, önkoşul atlatmaz."""
    url = f"{V2_TABAN}{API_YOLU}"
    yanit, govde, sure, hata = _istek(oturum, url, timeout=timeout, bayt=2048)

    if yanit is None:
        return Prob("api önkoşulu", url, False, False, None, sure, "", [hata or "-"])

    kod = yanit.status_code
    icerik = (yanit.headers.get("Content-Type") or "").split(";")[0].strip()
    notlar: List[str] = []
    saglikli = False

    if kod == 428:
        notlar.append(
            "428 — imzalı X-Ekap-Sec başlıkları veya oturum önkoşulu "
            "karşılanmıyor (ekap_session.py --teshis ile ayrıntı al)"
        )
    elif kod in (404, 405):
        saglikli = True
        notlar.append("uç nokta ayakta (GET'e izin verilmiyor, beklenen)")
    elif kod in (401, 403):
        saglikli = True
        notlar.append("yetkilendirme/imza gerekiyor")
    elif kod >= 500:
        notlar.append("API 5xx veriyor")
    elif kod >= 400:
        notlar.append(f"beklenmeyen istemci hatası: {kod}")
    else:
        saglikli = True

    kalip = _hata_kalibi_var(govde)
    if kalip:
        saglikli = False
        notlar.append(f"gövdede arıza kalıbı: {kalip!r}")

    return Prob("api önkoşulu", url, saglikli, False, kod, sure, icerik, notlar)


def saglik_kontrol(
    *,
    timeout: float = 8.0,
    dogrula_ssl: Optional[bool] = None,
    api_probu: bool = False,
    tum_remotelar: bool = False,
    oturum: Optional[requests.Session] = None,
) -> SaglikRaporu:
    """EKAP katmanlarını sırayla yoklar ve tek bir sağlık raporu döndürür."""
    kendi_oturumu = oturum is None
    oturum = oturum or ekap_oturumu(dogrula=dogrula_ssl, user_agent=DEFAULT_USER_AGENT)
    rapor = SaglikRaporu()

    try:
        rapor.problar.append(
            _prob_calistir(oturum, "eski EKAP", ESKI_EKAP_URL, timeout=timeout)
        )

        kabuk_problari, _paket = _kabuk_problari(oturum, timeout)
        rapor.problar.extend(kabuk_problari)

        manifest_prob, yollar = _remote_yollari(oturum, timeout)
        rapor.problar.append(manifest_prob)

        if yollar:
            secili = (
                yollar
                if tum_remotelar
                else {
                    ad: yol
                    for ad, yol in yollar.items()
                    if ad in (ARAMA_REMOTE_ADI, "shell")
                }
            )
            for ad, yol in secili.items():
                rapor.problar.extend(_remote_problari(oturum, ad, yol, timeout))

        if api_probu:
            rapor.problar.append(_api_onkosul_probu(oturum, timeout))
    finally:
        if kendi_oturumu:
            oturum.close()

    bozuklar = rapor.bozuk_kritikler()
    uyarilar = rapor.uyarilar()
    rapor.saglikli = not bozuklar

    if rapor.saglikli:
        rapor.ozet = "EKAP erişilebilir; tarama başlatılabilir."
        if uyarilar:
            rapor.ozet += f" ({len(uyarilar)} kritik olmayan uyarı)"
    else:
        gerekce = "; ".join(
            f"{p.ad}: {'; '.join(p.notlar) or 'sağlıksız'}" for p in bozuklar
        )
        rapor.ozet = f"EKAP şu an kullanılamıyor: {gerekce}"
        if rapor.bot_korumasi:
            rapor.ozet += " — EKAP_UNDETECTED=1 ile denemeye değer."

    return rapor


def ekap_hazir_mi(timeout: float = 8.0, sessiz: bool = False) -> bool:
    """Tek satırlık kapı kontrolü: Selenium/API çağrısı başlatılsın mı?"""
    rapor = saglik_kontrol(timeout=timeout)
    if not sessiz:
        rapor.yazdir()
    return rapor.saglikli


def saglik_bekle(
    *,
    deneme: int = 3,
    bekleme: float = 60.0,
    katsayi: float = 2.0,
    azami_bekleme: float = 600.0,
    timeout: float = 8.0,
    log: Callable[[str], None] = print,
) -> SaglikRaporu:
    """Sağlıklı yanıt alana kadar artan aralıklarla yeniden dener."""
    deneme = max(1, int(deneme))
    rapor = saglik_kontrol(timeout=timeout)

    for sira in range(1, deneme + 1):
        if sira > 1:
            rapor = saglik_kontrol(timeout=timeout)
        rapor.yazdir(log)
        if rapor.saglikli:
            return rapor
        if sira >= deneme:
            break
        log(f"⏳ EKAP kapalı; {bekleme:.0f} sn sonra {sira + 1}. deneme yapılacak.")
        time.sleep(bekleme)
        bekleme = min(bekleme * katsayi, azami_bekleme)

    return rapor


def main(argv: Optional[List[str]] = None) -> int:
    ayristirici = argparse.ArgumentParser(description="EKAP sağlık kontrolü")
    ayristirici.add_argument("--json", action="store_true", help="raporu JSON bas")
    ayristirici.add_argument(
        "--bekle", action="store_true", help="sağlıklı olana kadar backoff ile bekle"
    )
    ayristirici.add_argument(
        "--deneme", type=int, default=3, help="--bekle için deneme sayısı"
    )
    ayristirici.add_argument(
        "--bekleme", type=float, default=60.0, help="--bekle için ilk bekleme (sn)"
    )
    ayristirici.add_argument(
        "--api", action="store_true", help="API önkoşul (428) durumunu da raporla"
    )
    ayristirici.add_argument(
        "--tum-remotelar",
        action="store_true",
        help="yalnızca arama modülü değil, tüm uzak modülleri yokla",
    )
    ayristirici.add_argument("--timeout", type=float, default=8.0)
    args = ayristirici.parse_args(argv)

    if args.bekle:
        rapor = saglik_bekle(
            deneme=args.deneme, bekleme=args.bekleme, timeout=args.timeout
        )
    else:
        rapor = saglik_kontrol(
            timeout=args.timeout,
            api_probu=args.api,
            tum_remotelar=args.tum_remotelar,
        )
        rapor.yazdir()

    if args.json:
        print(json.dumps(rapor.sozluk(), ensure_ascii=False, indent=2))
    return 0 if rapor.saglikli else 1


if __name__ == "__main__":
    sys.exit(main())
