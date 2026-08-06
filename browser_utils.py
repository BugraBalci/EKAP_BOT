from requests import options
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait

def tarayiciyi_baslat():
    print("🌐 Tarayıcı başlatılıyor ve ekstra gizlenme ayarları yapılıyor...")
    
    options = webdriver.ChromeOptions()
    
    # 1. Tam ekran (maximize) bayrağı yerine doğrudan Full HD çözünürlük veriyoruz (Algılamayı atlatır)
    options.add_argument("--window-size=1920,1080") 
    
    # 2. Gerçek bir insan gibi görünmek için sahte kimlik (User-Agent) ekliyoruz
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    # 3. Selenium'un "Ben Botum" diyen default ayarlarını siliyoruz
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"]) # İŞTE BU SATIR!
    options.add_experimental_option('useAutomationExtension', False)
    # Tarayıcıyı kur ve seçeneklerle birlikte başlat
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    
    # Ekstra Gizlenme: Tarayıcıya Javascript ile 'Ben bot değilim' diyoruz
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    print("🖥️ Tarayıcı 1920x1080 (Full HD) boyutunda açıldı ve hazır.")
    wait = WebDriverWait(driver, 25)
    
    return driver, wait