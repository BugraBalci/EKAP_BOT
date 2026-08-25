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


def tarayiciyi_baslat():
    print("🌐 Tarayıcı başlatılıyor ve güvenlik duvarı aşılıyor...")
    options = webdriver.ChromeOptions()
    options.page_load_strategy = "eager"
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
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    for candidate in _CHROME_CANDIDATES:
        if candidate and os.path.isfile(candidate):
            options.binary_location = candidate
            break

    # Actions'ta Chrome zaten kurulu; Selenium Manager 120 sn'lik
    # webdriver-manager indirme timeout'una takılmaz.
    try:
        driver = webdriver.Chrome(options=options)
    except Exception as e:
        print(f"⚠️ Selenium Manager başarısız ({e}); webdriver-manager deneniyor...")
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options,
        )

    driver.set_page_load_timeout(40)
    try:
        driver.set_window_size(1920, 1080)
    except Exception:
        pass
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )

    print("🖥️ Headless Chrome 1920x1080 (eager, maximized, görselsiz) başarıyla açıldı.")
    wait = WebDriverWait(driver, 25)
    return driver, wait
