#!/usr/bin/env python3
"""Angular boş/siyah ekranda kaldığında nedenini bulan Selenium teşhis katmanı.

EKAP v2 bir module federation SPA'i olduğu için HTTP 200 dönmesi arayüzün
render edildiği anlamına gelmiyor: paketlerden biri iptal edilirse ya da
çalışma anında JS hatası oluşursa ``<web-root>`` boş kalır ve Selenium
"arama formu yüklenmedi" hatasıyla düşer.

Bu modül o boşluğu doldurur:
  * ``spa_hidrasyon_bekle`` — formu beklemeden önce SPA'in hiç render edilip
    edilmediğini kısa sürede anlar (155 sn'lik üç deneme yerine ~15 sn).
  * ``konsol_loglarini_bas`` — ``driver.get_log('browser')`` çıktısını basar.
  * ``ag_hatalarini_bas`` — iptal edilen / 4xx-5xx dönen paketleri ve
    JavaScript yerine HTML servis edilen dosyaları listeler.
  * ``bot_korumasi_ipucu`` — Cloudflare/F5 sayfası mı, headless tespiti mi
    olduğuna dair kanıt toplar.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Dict, List

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.support.ui import WebDriverWait

ROOT = Path(__file__).resolve().parent

# Bot koruma / erişim engeli sayfalarının imzaları.
BOT_KORUMA_KALIPLARI = (
    "just a moment",
    "checking your browser",
    "cf-browser-verification",
    "attention required",
    "access denied",
    "request blocked",
    "the requested url was rejected",
    "bu olay için referans numarasi",
    "incident id",
    # EKAP'ın otomasyona gösterdiği Türkçe doğrulama sayfası. Paketlerin
    # tamamı 200 dönerken <web-root> boş kalıyorsa sebep budur; bu kalıplar
    # olmadan teşhis yanlışlıkla "sorun uygulama tarafında" diyor.
    "kısa bir güvenlik kontrolü",
    "kisa bir guvenlik kontrolu",
    "güvenlik kontrolünü tamamlayın",
    "guvenlik kontrolunu tamamlayin",
    "güvenlik kontrolü",
)

SPA_KOK_SECICILER = ("web-root", "app-root")

# Konsol mesajından nedene giden bilinen eşlemeler. "invalid sfntVersion"
# değeri HTML'in ilk baytlarıdır (0x3C21444F = "<!DO"): istenen varlık yerine
# nginx'in SPA fallback'i index.html döndürüyor demektir.
BILINEN_BELIRTILER = (
    (
        "invalid sfntversion",
        "varlık yerine HTML servis edildi (nginx SPA fallback) — dağıtımdaki "
        "dosya yolu hatalı",
    ),
    (
        "failed to load module script",
        "JS modülü yerine HTML döndü — Angular/federation paketi dağıtımda yok",
    ),
    ("chunkloaderror", "webpack parçası yüklenemedi — dağıtım sürümleri uyuşmuyor"),
    ("loading chunk", "webpack parçası yüklenemedi — dağıtım sürümleri uyuşmuyor"),
    ("428", "API önkoşulu karşılanmadı (ekap_session.py --teshis)"),
    ("err_blocked_by_client", "istek istemci/eklenti tarafından engellendi"),
)


def bilinen_belirtiler(kayitlar: List[Dict]) -> List[str]:
    """Konsol kayıtlarını bilinen neden açıklamalarıyla eşler."""
    metin = " ".join((k.get("message") or "") for k in kayitlar).lower()
    bulunan: List[str] = []
    for kalip, aciklama in BILINEN_BELIRTILER:
        if kalip in metin and aciklama not in bulunan:
            bulunan.append(aciklama)
    return bulunan


def _guvenli_js(driver, betik: str, varsayilan=None):
    try:
        return driver.execute_script(betik)
    except WebDriverException:
        return varsayilan


def spa_render_edildi_mi(driver) -> bool:
    """SPA kök elemanının içine gerçekten DOM basılmış mı?"""
    betik = """
        const secici = arguments[0];
        for (const ad of secici) {
          const kok = document.querySelector(ad);
          if (kok && kok.children.length > 0) return true;
        }
        return false;
    """
    try:
        return bool(driver.execute_script(betik, list(SPA_KOK_SECICILER)))
    except WebDriverException:
        return False


def spa_hidrasyon_bekle(driver, timeout: float = 20.0, log: Callable[[str], None] = print) -> bool:
    """SPA render edilene kadar bekler. Render edilmezse hızlı ``False`` döner."""
    try:
        WebDriverWait(driver, timeout).until(lambda d: spa_render_edildi_mi(d))
        log("✅ Angular SPA render edildi.")
        return True
    except TimeoutException:
        log(
            f"❌ Angular SPA {timeout:.0f} sn içinde render edilmedi "
            "(<web-root> boş) — arayüz boş/siyah ekran."
        )
        return False


def konsol_loglari(driver) -> List[Dict]:
    """Tarayıcı konsol kayıtlarını döndürür.

    Chrome dışındaki sürücülerde ya da ``goog:loggingPrefs`` verilmediğinde
    sürücü bu uç noktayı desteklemez; o durumda boş liste döner.
    """
    try:
        return driver.get_log("browser")
    except WebDriverException as e:
        print(f"⚠️ Konsol logları alınamadı ({type(e).__name__}); "
              "browser_utils goog:loggingPrefs yeteneğini veriyor mu?")
        return []


def konsol_loglarini_bas(
    driver, *, sadece_hata: bool = False, azami: int = 40, log: Callable[[str], None] = print
) -> List[Dict]:
    """Konsol loglarını okunur biçimde basar ve ham kayıtları döndürür."""
    kayitlar = konsol_loglari(driver)
    if not kayitlar:
        log("🖥️ Tarayıcı konsolu boş (ya da log toplama kapalı).")
        return kayitlar

    seviye_sirasi = {"SEVERE": 0, "WARNING": 1, "INFO": 2, "DEBUG": 3}
    suzulmus = [
        k
        for k in kayitlar
        if not sadece_hata or (k.get("level") or "").upper() in ("SEVERE", "WARNING")
    ]
    suzulmus.sort(key=lambda k: seviye_sirasi.get((k.get("level") or "").upper(), 9))

    log(f"🖥️ Tarayıcı konsolu ({len(suzulmus)}/{len(kayitlar)} kayıt):")
    for kayit in suzulmus[:azami]:
        seviye = (kayit.get("level") or "?").upper()
        mesaj = (kayit.get("message") or "").replace("\n", " ")
        isaret = "❌" if seviye == "SEVERE" else ("⚠️" if seviye == "WARNING" else "•")
        log(f"   {isaret} [{seviye}] {mesaj[:300]}")
    if len(suzulmus) > azami:
        log(f"   … {len(suzulmus) - azami} kayıt daha var.")
    return kayitlar


def ag_kayitlari(driver) -> List[Dict]:
    """Performance API üzerinden yüklenen kaynakların durumunu toplar."""
    betik = """
        const koken = location.origin;
        return performance.getEntriesByType('resource').map(function (e) {
          return {
            ad: e.name,
            tur: e.initiatorType,
            durum: e.responseStatus === undefined ? null : e.responseStatus,
            sure: Math.round(e.duration),
            aktarilan: e.transferSize,
            govde: e.decodedBodySize,
            capraz: e.name.indexOf(koken) !== 0
          };
        });
    """
    return _guvenli_js(driver, betik, []) or []


def ag_hatalari(driver) -> List[Dict]:
    """Angular'ı kıran ağ olaylarını süzer.

    Yalnızca aynı kökenli istekler değerlendirilir: Chrome,
    ``Timing-Allow-Origin`` göndermeyen çapraz köken isteklerinde durum
    kodunu ve gövde boyutunu her zaman 0 raporlar, bu yüzden Google Fonts
    gibi üçüncü taraf kaynaklar aksi hâlde hatalı biçimde "engellendi"
    görünür.
    """
    hatalar: List[Dict] = []
    for kayit in ag_kayitlari(driver):
        if kayit.get("capraz"):
            continue

        durum = kayit.get("durum")
        ad = kayit.get("ad") or ""
        js_mi = ad.endswith(".js") or kayit.get("tur") == "script"

        if durum is not None and durum >= 400:
            kayit["sebep"] = f"HTTP {durum}"
            hatalar.append(kayit)
        elif durum == 0:
            kayit["sebep"] = "istek iptal edildi/engellendi (status 0)"
            hatalar.append(kayit)
        elif js_mi and not kayit.get("govde"):
            kayit["sebep"] = "JS paketi boş gövdeyle döndü"
            hatalar.append(kayit)
    return hatalar


def capraz_koken_kayitlari(driver) -> List[Dict]:
    """Durumu ölçülemeyen üçüncü taraf istekleri (bilgi amaçlı)."""
    return [k for k in ag_kayitlari(driver) if k.get("capraz")]


def ag_hatalarini_bas(driver, *, azami: int = 25, log: Callable[[str], None] = print) -> List[Dict]:
    hatalar = ag_hatalari(driver)
    capraz = capraz_koken_kayitlari(driver)

    if not hatalar:
        toplam = len(ag_kayitlari(driver))
        log(
            f"🌐 EKAP kökenli isteklerde hata yok ({toplam - len(capraz)} istek); "
            f"{len(capraz)} üçüncü taraf isteğinin durumu ölçülemez (CORS)."
        )
        return hatalar

    log(f"🌐 Başarısız/şüpheli {len(hatalar)} EKAP isteği:")
    for kayit in hatalar[:azami]:
        ad = (kayit.get("ad") or "").split("/")[-1] or kayit.get("ad")
        log(f"   ❌ {kayit.get('sebep'):42s} {ad} ({kayit.get('sure')}ms)")
    if len(hatalar) > azami:
        log(f"   … {len(hatalar) - azami} istek daha var.")
    return hatalar


def bot_korumasi_ipucu(driver, *, log: Callable[[str], None] = print) -> Dict:
    """Boş ekranın bot koruması mı yoksa uygulama hatası mı olduğunu değerlendirir."""
    bilgi = _guvenli_js(
        driver,
        """
        return {
          url: location.href,
          baslik: document.title,
          hazir: document.readyState,
          govde_metni: (document.body && document.body.innerText || '').slice(0, 3000),
          html_boyu: document.documentElement.outerHTML.length,
          webdriver: navigator.webdriver,
          eklenti_sayisi: navigator.plugins ? navigator.plugins.length : -1,
          diller: navigator.languages,
          kullanici_ajani: navigator.userAgent
        };
        """,
        {},
    ) or {}

    metin = (bilgi.get("govde_metni") or "").lower()
    baslik = (bilgi.get("baslik") or "").lower()
    eslesen = [k for k in BOT_KORUMA_KALIPLARI if k in metin or k in baslik]

    sonuc = {
        "bot_korumasi": bool(eslesen),
        "eslesen_kaliplar": eslesen,
        "webdriver_bayragi": bilgi.get("webdriver"),
        "headless_ipucu": "headless" in (bilgi.get("kullanici_ajani") or "").lower(),
        "url": bilgi.get("url"),
        "baslik": bilgi.get("baslik"),
    }

    if eslesen:
        log(f"🛡️ Bot koruması/erişim engeli sayfası tespit edildi: {eslesen}")
        log("   ➜ EKAP_UNDETECTED=1 ile undetected-chromedriver deneyin.")
    else:
        log("🛡️ Bot koruma sayfası imzası yok; sorun uygulama/paket tarafında görünüyor.")

    if sonuc["webdriver_bayragi"]:
        log("⚠️ navigator.webdriver hâlâ true — otomasyon gizlemesi çalışmamış.")
    if sonuc["headless_ipucu"]:
        log("⚠️ User-Agent 'Headless' içeriyor — sunucu headless'ı ayırt edebilir.")

    return sonuc


def teshis_raporu(
    driver,
    *,
    onek: str = "ekap_hata_ekrani",
    log: Callable[[str], None] = print,
    dosyaya_yaz: bool = True,
) -> Dict:
    """Tüm teşhis adımlarını çalıştırır, ekran görüntüsü ve HTML'i kaydeder."""
    log("🔍 EKAP teşhisi başlıyor...")
    rapor: Dict = {
        "url": _guvenli_js(driver, "return location.href", ""),
        "baslik": _guvenli_js(driver, "return document.title", ""),
        "hazir_durumu": _guvenli_js(driver, "return document.readyState", ""),
        "spa_render": spa_render_edildi_mi(driver),
    }
    log(
        f"   url={rapor['url']} | başlık={rapor['baslik']!r} | "
        f"readyState={rapor['hazir_durumu']} | spa_render={rapor['spa_render']}"
    )

    rapor["konsol"] = konsol_loglarini_bas(driver, sadece_hata=True, log=log)
    rapor["belirtiler"] = bilinen_belirtiler(rapor["konsol"])
    for aciklama in rapor["belirtiler"]:
        log(f"   ➜ {aciklama}")
    rapor["ag_hatalari"] = ag_hatalarini_bas(driver, log=log)
    rapor["bot_korumasi"] = bot_korumasi_ipucu(driver, log=log)

    if dosyaya_yaz:
        try:
            png = ROOT / f"{onek}.png"
            driver.save_screenshot(str(png))
            rapor["ekran_goruntusu"] = str(png)
            log(f"💾 Ekran görüntüsü: {png.name}")
        except WebDriverException as e:
            log(f"⚠️ Ekran görüntüsü alınamadı: {e}")

        try:
            html = ROOT / f"{onek}.html"
            html.write_text((driver.page_source or "")[:200000], encoding="utf-8")
            rapor["html"] = str(html)
            log(f"💾 Sayfa kaynağı: {html.name}")
        except Exception as e:
            log(f"⚠️ Sayfa kaynağı yazılamadı: {e}")

        try:
            jsn = ROOT / f"{onek}_teshis.json"
            jsn.write_text(
                json.dumps(rapor, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            log(f"💾 Teşhis raporu: {jsn.name}")
        except Exception as e:
            log(f"⚠️ Teşhis raporu yazılamadı: {e}")

    return rapor


def teshis_ozeti(rapor: Dict) -> str:
    """Rapordan e-postaya/loga konacak tek paragraf üretir."""
    parcalar: List[str] = []
    if not rapor.get("spa_render"):
        parcalar.append("Angular SPA render edilmedi (<web-root> boş)")

    for aciklama in rapor.get("belirtiler") or []:
        parcalar.append(aciklama)

    ag = rapor.get("ag_hatalari") or []
    if ag:
        ilk = ", ".join(
            f"{(k.get('ad') or '').split('/')[-1]} [{k.get('sebep')}]" for k in ag[:3]
        )
        parcalar.append(f"{len(ag)} başarısız istek: {ilk}")

    severe = [
        k for k in (rapor.get("konsol") or []) if (k.get("level") or "").upper() == "SEVERE"
    ]
    if severe:
        parcalar.append(
            f"{len(severe)} konsol hatası, ilki: "
            f"{(severe[0].get('message') or '')[:200]}"
        )

    koruma = rapor.get("bot_korumasi") or {}
    if koruma.get("bot_korumasi"):
        parcalar.append(f"bot koruma sayfası: {koruma.get('eslesen_kaliplar')}")

    return "; ".join(parcalar) or "Teşhiste belirgin bir hata bulunamadı."
