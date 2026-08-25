import re
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


def _kisa_tikla(driver, locator, aciklama="öğe", timeout=5):
    """Kısa bekleyip JS tıkla; yoksa TimeoutException."""
    w = WebDriverWait(driver, timeout)
    try:
        element = w.until(EC.element_to_be_clickable(locator))
    except TimeoutException:
        element = w.until(EC.presence_of_element_located(locator))
    _js_tikla(driver, element)
    return element


def _sirayla_tikla(driver, locators, aciklama, timeout=5):
    last = None
    for locator in locators:
        try:
            return _kisa_tikla(driver, locator, aciklama, timeout=timeout)
        except Exception as e:
            last = e
            continue
    raise last if last else TimeoutException(aciklama)


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


_OKAS_BTN_XPATH = (
    "//div[contains(@class, 'dx-button-content') and contains(., 'OKAS Kodu Seç')] "
    "| //button[contains(., 'OKAS Kodu Seç')] "
    "| //button[contains(., 'OKAS')]"
)
_OKAS_POPUP_TITLE_XPATH = (
    "//div[contains(@class,'dx-popup-title') and contains(., 'OKAS')]"
)
_OKAS_POPUP_XPATH = (
    "//div[contains(@class,'dx-overlay-content') or contains(@class,'dx-popup')]"
    "[.//*[contains(., 'OKAS Kodu Seç')] or .//input[contains(@placeholder,'Arama')]]"
)


def _okas_buton_metni(driver) -> str:
    els = driver.find_elements(By.XPATH, _OKAS_BTN_XPATH)
    for el in els:
        try:
            if el.is_displayed():
                return (el.text or "").replace("\n", " ").strip()
        except StaleElementReferenceException:
            continue
    return (els[0].text or "").strip() if els else ""


def _okas_adet(driver) -> int:
    m = re.search(r"(\d+)\s*adet", _okas_buton_metni(driver), re.I)
    return int(m.group(1)) if m else 0


def _okas_popup_metin_tikla(driver, tam_metin: str) -> str:
    return str(
        driver.execute_script(
            """
            var hedef = (arguments[0] || '').replace(/\\s+/g, ' ').trim();
            var nodes = Array.from(document.querySelectorAll('div, button, span, a, p'));
            var t = nodes.find(function (el) {
              var x = (el.textContent || '').replace(/\\s+/g, ' ').trim();
              return x === hedef && x.length < 80;
            });
            if (!t) return 'not-found';
            t.scrollIntoView({block:'center'});
            t.click();
            return 'clicked';
            """,
            tam_metin,
        )
    )


def _sonuc_sayisi(driver) -> int:
    kaynak = ""
    try:
        kaynak = driver.find_element(By.TAG_NAME, "body").text or ""
    except Exception:
        kaynak = driver.page_source or ""
    m = re.search(r"([\d.\s]+)\s*ihale listelenmektedir", kaynak, re.I)
    if not m:
        return -1
    ham = re.sub(r"[.\s]", "", m.group(1))
    try:
        return int(ham)
    except ValueError:
        return -1


def okas_kodu_sec(driver, wait, okas_kodu):
    print(f"📂 'OKAS Kodu Seç' menüsü açılıyor ve '{okas_kodu}' aranıyor...")
    try:
        ogretici_kapat(driver, wait)
        time.sleep(0.5)

        onceki = _okas_adet(driver)
        print(f"   OKAS buton (önce): {_okas_buton_metni(driver) or '-'} ({onceki} adet)")

        _bekle_ve_tikla(driver, wait, (By.XPATH, _OKAS_BTN_XPATH), "OKAS Kodu Seç")
        WebDriverWait(driver, 12).until(
            EC.visibility_of_element_located((By.XPATH, _OKAS_POPUP_TITLE_XPATH))
        )
        time.sleep(0.8)

        temizle = _okas_popup_metin_tikla(driver, "Seçilen Branşları Temizle")
        print(f"   Seçilenleri temizle: {temizle}")
        time.sleep(1.0)

        tab_sonuc = _okas_popup_metin_tikla(driver, "KALEM AĞACI LİSTESİ")
        print(f"   OKAS sekme: {tab_sonuc}")
        time.sleep(1.0)

        arama_adaylari = [
            (
                By.XPATH,
                _OKAS_POPUP_XPATH
                + "//input[contains(@placeholder,'Arama') or contains(@placeholder,'arama')]",
            ),
            (
                By.XPATH,
                "//input[@aria-label='Search in the tree list']",
            ),
            (
                By.XPATH,
                _OKAS_POPUP_XPATH + "//input[contains(@class,'dx-texteditor-input')]",
            ),
        ]
        arama_kutusu = None
        last = None
        for loc in arama_adaylari:
            try:
                arama_kutusu = WebDriverWait(driver, 6).until(
                    EC.visibility_of_element_located(loc)
                )
                break
            except Exception as e:
                last = e
        if arama_kutusu is None:
            raise last or TimeoutException("OKAS arama kutusu bulunamadı")
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'}); arguments[0].focus();",
            arama_kutusu,
        )
        _js_deger_yaz(driver, arama_kutusu, okas_kodu)
        WebDriverWait(driver, 8).until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    f"//*[contains(text(), '{okas_kodu}')]",
                )
            )
        )
        time.sleep(0.6)

        checkbox_xpath = (
            f"(//*[@role='row' or self::tr or contains(@class,'dx-treelist-row')"
            f" or contains(@class,'dx-data-row')]"
            f"[.//text()[contains(., '{okas_kodu}')]]"
            f"//span[contains(@class,'dx-checkbox-icon')])[1] "
            f"| (//*[@role='row' or self::tr][.//text()[contains(., '{okas_kodu}')]]"
            f"//*[@role='checkbox'])[1] "
            f"| (//*[@role='row' or self::tr][.//text()[contains(., '{okas_kodu}')]]"
            f"//*[@aria-label='Satırı seç'])[1]"
        )
        _bekle_ve_tikla(driver, wait, (By.XPATH, checkbox_xpath), "OKAS checkbox")
        time.sleep(0.6)

        sec = _okas_popup_metin_tikla(driver, "Seç")
        if sec != "clicked":
            sec_xpath = (
                _OKAS_POPUP_XPATH
                + "//div[contains(@class,'dx-button-content') and normalize-space()='Seç']"
                " | "
                + _OKAS_POPUP_XPATH
                + "//button[normalize-space()='Seç']"
            )
            _bekle_ve_tikla(driver, wait, (By.XPATH, sec_xpath), "Seç")
        try:
            WebDriverWait(driver, 10).until(
                EC.invisibility_of_element_located(
                    (By.XPATH, _OKAS_POPUP_TITLE_XPATH)
                )
            )
        except TimeoutException:
            raise RuntimeError("OKAS popup Seç sonrası kapanmadı; seçim uygulanmamış olabilir.")

        time.sleep(0.4)
        adet = _okas_adet(driver)
        metin = _okas_buton_metni(driver)
        print(f"   OKAS buton (sonra): {metin or '-'} ({adet} adet)")
        if adet <= 0:
            raise RuntimeError(
                f"OKAS {okas_kodu} uygulanmadı; buton hâlâ '{metin or 'OKAS Kodu Seç'}'."
            )
        print(f"✅ OKAS kodu '{okas_kodu}' seçildi ({adet} kalem).")
    except Exception as e:
        print(f"❌ OKAS kodu seçilirken HATA: {e}")
        raise


def _arama_formu_hazir(driver, timeout=40):
    WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located(
            (
                By.XPATH,
                "//*[contains(., 'OKAS Kodu Seç') or contains(., 'Filtrele') "
                "or contains(., 'İhale Durumu')]",
            )
        )
    )


def _js_durum_tikla(driver) -> str:
    """Angular formunda İhale Durumu kutusunu açar. Dönüş: opened / hata kodu."""
    return str(
        driver.execute_script(
            """
            function clickEl(el) {
              if (!el) return false;
              el.scrollIntoView({block:'center'});
              el.click();
              return true;
            }
            var labels = Array.from(document.querySelectorAll('*'));
            var durumLabel = labels.find(function (el) {
              var t = (el.textContent || '').replace(/\\s+/g, ' ').trim();
              return t === 'İhale Durumu' || t === 'Ihale Durumu';
            });
            if (durumLabel) {
              var host = durumLabel.closest('.dx-field-item')
                || durumLabel.closest('.dx-field')
                || durumLabel.closest('dx-tag-box')
                || durumLabel.parentElement;
              var drop = host && host.querySelector(
                '.dx-dropdowneditor-icon, .dx-dropdowneditor-button, .dx-texteditor-input'
              );
              if (!clickEl(drop || host)) return 'label-click-failed';
              return 'opened';
            }
            var input = document.querySelector(
              'input[aria-label*="İhale Durumu"], input[placeholder*="İhale Durumu"]'
            );
            if (!clickEl(input)) return 'no-durum-box';
            return 'opened';
            """
        )
    )


def durum_sec(driver, wait, durum="Teklif Vermeye Açık"):
    """İhale Durumu kutusundan yalnızca teklif vermeye açık kaydı işaretler."""
    print(f"📌 İhale durumu seçiliyor: {durum}")
    ogretici_kapat(driver, wait)
    _ogretici_ve_backdrop_kaybolsun(driver, timeout=3)
    _arama_formu_hazir(driver)

    js_sonuc = _js_durum_tikla(driver)
    print(f"   JS durum kutusu: {js_sonuc}")
    if js_sonuc != "opened":
        kutu_adaylari = [
            (
                By.XPATH,
                "//div[contains(@class,'dx-field') or contains(@class,'form-group')]"
                "[.//*[contains(normalize-space(.), 'İhale Durumu')]]"
                "//div[contains(@class,'dx-dropdowneditor-icon') or contains(@class,'dx-dropdowneditor-button')]",
            ),
            (
                By.XPATH,
                "//*[contains(normalize-space(.), 'İhale Durumu')]/following::div[contains(@class,'dx-dropdowneditor-icon')][1]",
            ),
            (
                By.XPATH,
                "//input[contains(@aria-label,'İhale Durumu') or contains(@placeholder,'İhale Durumu')]",
            ),
        ]
        _sirayla_tikla(driver, kutu_adaylari, "İhale Durumu kutusu", timeout=6)
    time.sleep(0.8)

    secenek_adaylari = [
        (
            By.XPATH,
            "//div[contains(@class,'dx-list-item-content') and contains(., 'Teklif Vermeye Açık')]",
        ),
        (
            By.XPATH,
            "//div[contains(@class,'dx-item-content') and contains(., 'Teklif Vermeye Açık')]",
        ),
        (
            By.XPATH,
            "//div[contains(@class,'dx-list-item-content') and contains(., 'Katılıma Açık')]",
        ),
        (
            By.XPATH,
            "//*[contains(@class,'dx-item') and contains(., 'İhale İlanı Yayımlanmış')]",
        ),
    ]
    _sirayla_tikla(driver, secenek_adaylari, durum, timeout=8)
    time.sleep(0.6)

    try:
        driver.execute_script(
            "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));"
        )
    except Exception:
        pass
    time.sleep(0.4)

    chip = driver.find_elements(
        By.XPATH,
        "//*[contains(@class,'dx-tag') or contains(@class,'dx-tagbox')]"
        "[contains(., 'Açık') or contains(., 'Açik') or contains(., 'Teklif') "
        "or contains(., 'Katılıma')]",
    )
    if not chip:
        sayfa = driver.page_source or ""
        if "Teklif Vermeye Açık" not in sayfa and "Katılıma Açık" not in sayfa:
            raise RuntimeError(
                "İhale durumu seçilemedi: sayfada 'Teklif Vermeye Açık' görünmüyor."
            )
    print("✅ İhale durumu: Teklif Vermeye Açık")


def arama_yap_ve_gosterimi_ayarla(driver, wait, gosterim_sayisi="50"):
    print("🔍 'Filtrele' butonuna basılıyor, ihaleler getiriliyor...")
    _ogretici_ve_backdrop_kaybolsun(driver, timeout=3)

    filtrele_xpath = (
        "//button[@id='search-ihale'] "
        "| //button[contains(., 'Filtrele')] "
        "| //div[contains(@class,'dx-button-content') and contains(., 'Filtrele')]"
    )
    _bekle_ve_tikla(driver, wait, (By.XPATH, filtrele_xpath), "Filtrele")
    time.sleep(2)

    try:
        WebDriverWait(driver, 12).until(
            lambda d: d.find_elements(
                By.XPATH,
                "//ihale-liste-item | //div[contains(@class,'pc-card')] "
                "| //*[contains(., 'kayıt bulunamadı') or contains(., 'Kayıt bulunamadı') "
                "or contains(., 'sonuç bulunamadı')]",
            )
        )
    except TimeoutException:
        print("⚠️ Filtrele sonrası kart/sonuç beklenirken zaman aşımı; mevcut DOM okunacak.")

    n = _sonuc_sayisi(driver)
    if n >= 0:
        print(f"   Listelenen ihale: {n}")
    if n > 100000:
        raise RuntimeError(
            f"OKAS filtresi uygulanmamış görünüyor: {n} ihale listeleniyor "
            "(beklenen: yazılıma özgü yüzler/binler, milyonlar değil)."
        )

    try:
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
        print(f"⚠️ Gösterim sayısı ayarlanamadı ({e}); mevcut liste ile devam.")
    print("✅ Filtrele uygulandı.")
