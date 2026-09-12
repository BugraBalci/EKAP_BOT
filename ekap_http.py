"""EKAP ve ilan.gov.tr'ye istek atan modüllerin paylaştığı requests oturumu.

Neden ayrı bir katman: iki devlet sunucusunun TLS yapılandırması Python'un
varsayılanlarıyla çalışmıyor ve bu, "sunucu kapalı" gibi görünen sahte
arızalara yol açıyor.

  1. ekapv2.kik.gov.tr, OpenSSL 3'ün varsayılan güvenlik seviyesiyle el
     sıkışmayı reddediyor: düz ``requests.get(...)`` çağrısı
     ``SSLV3_ALERT_HANDSHAKE_FAILURE`` ile düşerken aynı adres curl ile 200
     dönüyor. Bu yüzden şifre listesi ``DEFAULT@SECLEVEL=1``'e indirilir.
  2. www.ilan.gov.tr ara sertifikasını (GeoTrust TLS RSA CA G1) zincirde hiç
     göndermiyor; curl da aynı hatayı veriyor. Doğrulama varsayılan olarak
     açık kalır, yalnızca tam bu hata görüldüğünde ve yalnızca ilgili oturum
     için ``dogrulamayi_gevset`` ile kapatılır.
"""

from __future__ import annotations

import os
import ssl
from typing import Callable, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# EKAP'ın kabul ettiği eski şifre takımları için gerekli.
EKAP_CIPHERS = "DEFAULT@SECLEVEL=1"


class _EkapTLSAdapter(HTTPAdapter):
    """SECLEVEL=1 şifre listesiyle TLS bağlamı kuran adapter."""

    def __init__(
        self, ciphers: str = EKAP_CIPHERS, dogrula: bool = True, **kwargs
    ) -> None:
        self._ciphers = ciphers
        self._dogrula = dogrula
        super().__init__(**kwargs)

    def _baglam(self) -> ssl.SSLContext:
        baglam = create_urllib3_context(ciphers=self._ciphers)
        if not self._dogrula:
            # check_hostname, verify_mode'dan önce kapatılmalı; aksi hâlde
            # requests verify=False verdiğinde ValueError düşer.
            baglam.check_hostname = False
            baglam.verify_mode = ssl.CERT_NONE
        return baglam

    def init_poolmanager(self, *args, **kwargs):  # type: ignore[override]
        kwargs["ssl_context"] = self._baglam()
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):  # type: ignore[override]
        kwargs["ssl_context"] = self._baglam()
        return super().proxy_manager_for(*args, **kwargs)


def ssl_dogrula() -> bool:
    """EKAP_SSL_DOGRULA=0 ile sertifika doğrulaması kapatılabilir."""
    return (os.environ.get("EKAP_SSL_DOGRULA") or "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def ekap_oturumu(
    *,
    dogrula: Optional[bool] = None,
    user_agent: str = DEFAULT_USER_AGENT,
    yeniden_deneme: int = 0,
) -> requests.Session:
    """EKAP'a istek atmaya hazır, TLS'i uyumlanmış bir Session döndürür."""
    if dogrula is None:
        dogrula = ssl_dogrula()

    oturum = requests.Session()
    oturum.mount(
        "https://",
        _EkapTLSAdapter(dogrula=dogrula, max_retries=yeniden_deneme),
    )
    oturum.headers.update(
        {
            "User-Agent": user_agent,
            "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
        }
    )
    oturum.verify = bool(dogrula)
    return oturum


def tls_hatasi_mi(hata: "str | BaseException") -> bool:
    """Hata, sunucu kapalılığı değil TLS el sıkışma sorunu mu."""
    metin = (
        hata if isinstance(hata, str) else f"{type(hata).__name__}: {hata}"
    ).lower()
    return "sslerror" in metin or "handshake" in metin or "sslv3" in metin


def sertifika_zinciri_hatasi_mi(hata: "str | BaseException") -> bool:
    """Sunucu ara sertifikayı göndermediği için doğrulama düştü mü."""
    metin = (
        hata if isinstance(hata, str) else f"{type(hata).__name__}: {hata}"
    ).lower()
    return "certificate_verify_failed" in metin or "local issuer certificate" in metin


def dogrulamayi_gevset(
    oturum: requests.Session,
    sebep: str,
    log: Callable[[str], None] = print,
) -> None:
    """Tek bir oturum için sertifika doğrulamasını kapatır ve gerekçeyi loglar.

    Yalnızca ``sertifika_zinciri_hatasi_mi`` doğruladığı durumlarda çağrılmalı;
    sessizce genel kullanıma açılmamalı.
    """
    import urllib3

    oturum.verify = False
    oturum.mount("https://", _EkapTLSAdapter(dogrula=False))
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    log(f"🔓 Sertifika doğrulaması bu oturum için kapatıldı — {sebep}")


def govde_oku(yanit: requests.Response, bayt: int = 4096) -> str:
    """Yanıt gövdesinin yalnızca ilk bloğunu okur (sağlık kontrolü hafif kalsın)."""
    try:
        ham = yanit.raw.read(bayt, decode_content=True) or b""
    except Exception:
        return ""
    return ham.decode("utf-8", errors="replace")


def istek_at(
    oturum: requests.Session,
    url: str,
    *,
    metot: str = "GET",
    timeout: float = 8.0,
    govde_limiti: int = 4096,
    **kwargs,
) -> Tuple[Optional[int], str, str]:
    """(http_kodu, gövde_parçası, hata) döndürür; istisna yükseltmez."""
    try:
        yanit = oturum.request(
            url=url, method=metot, timeout=timeout, stream=True, **kwargs
        )
        try:
            govde = govde_oku(yanit, govde_limiti)
        finally:
            yanit.close()
        return yanit.status_code, govde, ""
    except (requests.RequestException, ValueError, OSError) as e:
        # ValueError/OSError: TLS bağlamı kurulurken çıkan hatalar da burada
        # yakalanmalı, yoksa sağlık kontrolü istisnayla çöker.
        return None, "", f"{type(e).__name__}: {e}"
