import os

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# GitHub Actions / Linux CI için gerçekçi masaüstü Chrome kimliği
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_CHROME_CANDIDATES = (
    os.environ.get("CHROME_BIN"),
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
)

# driver.get_log("browser") ancak bu yetenek verilirse çalışır; Angular'ın
# neden render edilmediğini konsol hatalarından okumak için şart.
_LOG_TERCIHLERI = {"browser": "ALL", "performance": "ALL"}


def _bayrak(ad: str, varsayilan: bool = False) -> bool:
    ham = (os.environ.get(ad) or "").strip().lower()
    if ham in {"1", "true", "yes", "on"}:
        return True
    if ham in {"0", "false", "no", "off"}:
        return False
    return varsayilan


def _headless_mi(headless=None) -> bool:
    if headless is not None:
        return bool(headless)
    # EKAP_HEADED=1 verilirse (veya gerçek DISPLAY varsa) görünür pencere.
    if _bayrak("EKAP_HEADED"):
        return False
    return True


def _undetected_mi(undetected=None) -> bool:
    if undetected is not None:
        return bool(undetected)
    return _bayrak("EKAP_UNDETECTED")


def _chrome_yolu():
    for candidate in _CHROME_CANDIDATES:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def _ortak_argumanlar(options, headless: bool):
    options.page_load_strategy = "eager"
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--blink-settings=imagesEnabled=false")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(f"--user-agent={DEFAULT_USER_AGENT}")
    yol = _chrome_yolu()
    if yol:
        options.binary_location = yol
    return options


def _standart_driver(headless: bool):
    options = _ortak_argumanlar(webdriver.ChromeOptions(), headless)
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.set_capability("goog:loggingPrefs", _LOG_TERCIHLERI)

    # Actions'ta Chrome zaten kurulu; Selenium Manager 120 sn'lik
    # webdriver-manager indirme timeout'una takılmaz.
    try:
        return webdriver.Chrome(options=options)
    except Exception as e:
        print(f"⚠️ Selenium Manager başarısız ({e}); webdriver-manager deneniyor...")
        return webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options,
        )


def _undetected_driver(headless: bool):
    """undetected-chromedriver ile aç.

    Amaç teşhis: EKAP'ta Angular boş ekranda kalıyor. Bunun sebebi bot
    koruması / headless tespiti mi, yoksa sunucu tarafı arıza mı? Aynı sayfa
    yamalı sürücüyle de boş kalıyorsa sorun bot korumasında değildir.
    """
    try:
        import undetected_chromedriver as uc
    except ImportError as e:
        raise RuntimeError(
            "EKAP_UNDETECTED=1 verildi ama undetected-chromedriver kurulu değil. "
            "Kurulum: pip install undetected-chromedriver"
        ) from e

    options = _ortak_argumanlar(uc.ChromeOptions(), headless=False)
    # uc, headless'ı kendi bayrağıyla yönetir; --headless argümanını eklemeyiz.
    options.set_capability("goog:loggingPrefs", _LOG_TERCIHLERI)
    return uc.Chrome(options=options, headless=headless, use_subprocess=True)


def tarayiciyi_baslat(*, headless=None, undetected=None):
    """Chrome'u açar ve (driver, wait) döndürür.

    headless=None   → EKAP_HEADED ortam değişkenine bakar (varsayılan headless)
    undetected=None → EKAP_UNDETECTED=1 ise undetected-chromedriver kullanır
    """
    headless = _headless_mi(headless)
    undetected = _undetected_mi(undetected)

    print(
        f"🌐 Tarayıcı başlatılıyor (headless={headless}, "
        f"undetected={undetected})..."
    )
    driver = _undetected_driver(headless) if undetected else _standart_driver(headless)

    driver.set_page_load_timeout(40)
    try:
        driver.set_window_size(1920, 1080)
    except Exception:
        pass
    try:
        # CDP ile enjekte etmek sayfa script'lerinden önce çalışır; execute_script
        # ise sayfa yüklendikten sonra kaldığı için tespiti kaçırabiliyor.
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": "Object.defineProperty(navigator, 'webdriver', "
                "{get: () => undefined});"
            },
        )
    except Exception:
        try:
            driver.execute_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
        except Exception:
            pass

    etiket = "undetected Chrome" if undetected else "Headless Chrome" if headless else "Chrome"
    print(f"🖥️ {etiket} 1920x1080 (eager, görselsiz) başarıyla açıldı.")
    wait = WebDriverWait(driver, 25)
    return driver, wait
