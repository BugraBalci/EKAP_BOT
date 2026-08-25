import time

from selenium.common.exceptions import (
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# Öğretici / intro overlay'leri (OKAS popup'ındaki dx-overlay hariç)
_OGRETICI_KAPAT_XPATHS = (
    "//button[@class='close-btn']",
    "//button[contains(@class,'close-btn')]",
    "//button[@aria-label='Close' or @aria-label='Kapat']",
    "//*[contains(@class,'tutorial')]//button[contains(@class,'close')]",
)
_OGRETICI_OVERLAY_XPATHS = (
    "//button[@class='close-btn']",
    "//button[contains(@class,'close-btn')]",
    "//div[contains(@class,'introjs-overlay')]",
    "//div[contains(@class,'introjs-helperLayer')]",
    "//div[contains(@class,'shepherd-modal-overlay')]",
    "//div[contains(@class,'tutorial') and contains(@class,'overlay')]",
    "//div[contains(@class,'modal-backdrop')]",
)


def _js_tikla(driver, element):
    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
        element,
    )
    time.sleep(0.2)
    try:
        driver.execute_script("arguments[0].click();", element)
        return
    except Exception:
        pass
    try:
        driver.execute_script(
            """
            var el = arguments[0];
            el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true, view: window}));
            el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true, view: window}));
            el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
            """,
            element,
        )
        return
    except Exception:
        pass
    element.click()


def _bekle_ve_tikla(driver, wait, locator, aciklama="öğe"):
    """Görünür/tıklanabilir olana kadar bekle, JS click; native click yedek."""
    kisa = WebDriverWait(driver, 8)
    try:
        element = kisa.until(EC.element_to_be_clickable(locator))
    except TimeoutException:
        try:
            element = wait.until(EC.visibility_of_element_located(locator))
        except TimeoutException:
            element = wait.until(EC.presence_of_element_located(locator))
    try:
        _js_tikla(driver, element)
    except Exception:
        print(f"⚠️ JS click yenileniyor ({aciklama})...")
        element = driver.find_element(*locator)
        _js_tikla(driver, element)
    return element


def _js_deger_yaz(driver, element, deger):
    """Angular/DevExpress input'una native setter + input/change event ile değer yaz."""
    driver.execute_script(
        """
        var el = arguments[0];
        var val = arguments[1];
        el.focus();
        el.removeAttribute('readonly');
        el.removeAttribute('disabled');
        var setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value'
        );
        if (setter && setter.set) { setter.set.call(el, val); }
        else { el.value = val; }
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: val.slice(-1) || 'a' }));
        """,
        element,
        deger,
    )


def _bekle_ve_yaz(driver, wait, locator, deger, aciklama="arama kutusu"):
    """Kutu görünene kadar bekle, JS ile değer ata; send_keys yalnızca yedek."""
    try:
        element = wait.until(EC.visibility_of_element_located(locator))
    except TimeoutException:
        element = wait.until(EC.presence_of_element_located(locator))
    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center'}); arguments[0].focus();",
        element,
    )
    try:
        _js_deger_yaz(driver, element, deger)
    except Exception as e:
        print(f"⚠️ JS değer ataması başarısız ({aciklama}): {e}; send_keys deneniyor...")
        try:
            element.clear()
        except Exception:
            driver.execute_script("arguments[0].value = '';", element)
        element.send_keys(deger)
    return element


def _gorunur_overlay_var(driver) -> bool:
    for xp in _OGRETICI_OVERLAY_XPATHS:
        try:
            for el in driver.find_elements(By.XPATH, xp):
                if el.is_displayed():
                    return True
        except StaleElementReferenceException:
            return True
    return False


def _ogretici_ve_backdrop_kaybolsun(driver, timeout=4):
    """Öğretici pencere ve arka plan backdrop'unun DOM'dan/ekrandan inmesini bekle."""
    try:
        WebDriverWait(driver, timeout).until(lambda d: not _gorunur_overlay_var(d))
    except TimeoutException:
        print("⚠️ Öğretici/backdrop zaman aşımı; JS ile zorla kaldırılıyor...")
        driver.execute_script(
            """
            var xpaths = [
                "//button[contains(@class,'close-btn')]",
                "//div[contains(@class,'introjs-overlay')]",
                "//div[contains(@class,'introjs-helperLayer')]",
                "//div[contains(@class,'shepherd-modal-overlay')]",
                "//div[contains(@class,'tutorial') and contains(@class,'overlay')]",
                "//div[contains(@class,'modal-backdrop')]"
            ];
            xpaths.forEach(function (xp) {
                var it = document.evaluate(xp, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
                for (var i = 0; i < it.snapshotLength; i++) {
                    var n = it.snapshotItem(i);
                    if (n && n.parentNode) { n.parentNode.removeChild(n); }
                }
            });
            document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
            """
        )
        time.sleep(0.5)


def ogretici_kapat(driver, wait):
    print("🔍 Öğretici (Tutorial) penceresi kontrol ediliyor...")
    kapatildi = False
    for xp in _OGRETICI_KAPAT_XPATHS:
        try:
            for btn in driver.find_elements(By.XPATH, xp):
                try:
                    if not btn.is_displayed():
                        continue
                except StaleElementReferenceException:
                    continue
                _js_tikla(driver, btn)
                kapatildi = True
                print("❌ Öğretici penceresi başarıyla kapatıldı!")
                break
        except Exception:
            continue
        if kapatildi:
            break
    if not kapatildi:
        try:
            driver.execute_script(
                "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));"
            )
        except Exception:
            pass
    _ogretici_ve_backdrop_kaybolsun(driver)
    time.sleep(0.5)


def okas_kodu_sec(driver, wait, okas_kodu):
    print(f"📂 'OKAS Kodu Seç' menüsü açılıyor ve '{okas_kodu}' aranıyor...")
    try:
        ogretici_kapat(driver, wait)
        time.sleep(0.5)

        okas_btn_xpath = (
            "//div[contains(@class, 'dx-button-content') and contains(., 'OKAS Kodu Seç')] "
            "| //button[contains(., 'OKAS Kodu Seç')] "
            "| //button[contains(., 'OKAS')]"
        )
        _bekle_ve_tikla(driver, wait, (By.XPATH, okas_btn_xpath), "OKAS Kodu Seç")
        time.sleep(1.5)

        arama_xpath = (
            "//div[contains(@class,'dx-overlay-content') or contains(@class,'dx-popup')]"
            "//input[@aria-label='Search in the tree list' or contains(@class,'dx-texteditor-input')]"
            " | //input[@aria-label='Search in the tree list']"
        )
        _bekle_ve_yaz(driver, wait, (By.XPATH, arama_xpath), okas_kodu, "OKAS arama kutusu")
        time.sleep(1.5)

        checkbox_xpath = (
            f"(//*[@role='row' or self::tr][.//text()[contains(., '{okas_kodu}')]]"
            f"//span[contains(@class, 'dx-checkbox-icon')])[1] "
            f"| (//*[@role='row' or self::tr][.//text()[contains(., '{okas_kodu}')]]"
            f"//*[@role='checkbox'])[1] "
            "| //span[contains(@class, 'dx-checkbox-icon')] "
            "| //div[@role='checkbox'] "
            "| //*[@aria-label='Satırı seç']"
        )
        _bekle_ve_tikla(driver, wait, (By.XPATH, checkbox_xpath), "OKAS checkbox")
        time.sleep(0.8)

        sec_xpath = (
            "//div[contains(@class, 'dx-overlay-content') or contains(@class,'dx-popup')]"
            "//div[contains(@class, 'dx-button-content') and (contains(., 'Seç') or contains(., 'Kaydet'))] "
            "| //div[contains(@class, 'dx-button-content') and contains(., 'Seç')] "
            "| //div[contains(@class, 'dx-button-content') and contains(., 'Kaydet')] "
            "| //dx-button[@aria-label='Kaydet' or @aria-label='Seç'] "
            "| //p[contains(@class, 'detay-button-text')]"
        )
        _bekle_ve_tikla(driver, wait, (By.XPATH, sec_xpath), "Seç/Kaydet")
        time.sleep(0.8)

        try:
            WebDriverWait(driver, 5).until(
                EC.invisibility_of_element_located(
                    (By.XPATH, "//input[@aria-label='Search in the tree list']")
                )
            )
        except TimeoutException:
            print("⚠️ OKAS popup kapanması zaman aşımı; Escape deneniyor...")
            driver.execute_script(
                "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));"
            )
            time.sleep(0.5)

        print(f"✅ OKAS kodu '{okas_kodu}' başarıyla seçildi.")
    except Exception as e:
        print(f"❌ OKAS kodu seçilirken HATA: {e}")
        raise


def arama_yap_ve_gosterimi_ayarla(driver, wait, gosterim_sayisi="50"):
    print("🔍 'Filtrele' butonuna basılıyor, ihaleler getiriliyor...")
    try:
        _ogretici_ve_backdrop_kaybolsun(driver, timeout=3)

        filtrele_xpath = (
            "//button[@id='search-ihale'] "
            "| //button[contains(., 'Filtrele')] "
            "| //div[contains(@class,'dx-button-content') and contains(., 'Filtrele')]"
        )
        _bekle_ve_tikla(driver, wait, (By.XPATH, filtrele_xpath), "Filtrele")
        time.sleep(2)

        gosterim_xpath = (
            "//*[@title='Gösterilecek Kayıt Sayısı'] "
            "| //div[contains(@class,'dx-selectbox')][.//input or contains(., 'Kayıt')]"
        )
        _bekle_ve_tikla(driver, wait, (By.XPATH, gosterim_xpath), "Gösterim kutusu")
        time.sleep(0.8)

        elli_xpath = (
            f"//div[contains(@class, 'dx-list-item-content') and normalize-space()='{gosterim_sayisi}']"
        )
        _bekle_ve_tikla(driver, wait, (By.XPATH, elli_xpath), f"Gösterim {gosterim_sayisi}")
        time.sleep(1.5)
    except Exception as e:
        print(f"⚠️ Arama veya gösterim ayarında hata: {e}")
