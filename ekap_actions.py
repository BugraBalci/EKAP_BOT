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


def _okas_arama_kutusu(driver):
    arama_adaylari = [
        (
            By.XPATH,
            _OKAS_POPUP_XPATH
            + "//input[contains(@placeholder,'Arama') or contains(@placeholder,'arama')]",
        ),
        (By.XPATH, "//input[@aria-label='Search in the tree list']"),
        (
            By.XPATH,
            _OKAS_POPUP_XPATH + "//input[contains(@class,'dx-texteditor-input')]",
        ),
    ]
    last = None
    for loc in arama_adaylari:
        try:
            return WebDriverWait(driver, 6).until(EC.visibility_of_element_located(loc))
        except Exception as e:
            last = e
    raise last or TimeoutException("OKAS arama kutusu bulunamadı")


def _okas_kodu_isaretle(driver, wait, okas_kodu) -> str:
    arama_kutusu = _okas_arama_kutusu(driver)
    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center'}); arguments[0].focus();",
        arama_kutusu,
    )
    _js_deger_yaz(driver, arama_kutusu, "")
    time.sleep(0.2)
    _js_deger_yaz(driver, arama_kutusu, okas_kodu)
    WebDriverWait(driver, 8).until(
        EC.presence_of_element_located(
            (By.XPATH, f"//*[contains(text(), '{okas_kodu}')]")
        )
    )
    time.sleep(0.35)
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
    isaretli = driver.execute_script(
        """
        var kod = arguments[0];
        var xpath = "//*[@role='row' or self::tr or contains(@class,'dx-treelist-row')]"
          + "[.//text()[contains(., '" + kod + "')]]";
        var rows = document.evaluate(xpath, document, null,
          XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
        if (!rows.snapshotLength) return 'no-row';
        var row = rows.snapshotItem(0);
        var box = row.querySelector('.dx-checkbox, [role=checkbox]');
        if (box && (box.classList.contains('dx-checkbox-checked')
            || box.getAttribute('aria-checked') === 'true')) {
          return 'already';
        }
        return 'need-click';
        """,
        okas_kodu,
    )
    if isaretli == "already":
        return "already"
    _bekle_ve_tikla(driver, wait, (By.XPATH, checkbox_xpath), f"OKAS {okas_kodu}")
    return "clicked"


def okas_kodlari_sec(driver, wait, okas_kodlari):
    kodlar = [str(k).strip() for k in (okas_kodlari or []) if str(k).strip()]
    if not kodlar:
        raise RuntimeError("OKAS kodu boş.")
    print(f"📂 'OKAS Kodu Seç' menüsü açılıyor ({len(kodlar)} kod)...")
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
        time.sleep(0.8)

        secilen = 0
        hatalar = []
        for kod in kodlar:
            try:
                durum = _okas_kodu_isaretle(driver, wait, kod)
                secilen += 1
                print(f"   ✓ {kod} ({durum})")
            except Exception as e:
                hatalar.append(f"{kod}: {e}")
                print(f"   ⚠️ {kod} işaretlenemedi: {e}")

        if secilen <= 0:
            raise RuntimeError(
                "Hiçbir OKAS kodu işaretlenemedi: " + "; ".join(hatalar[:4])
            )

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
                f"OKAS uygulanmadı; buton hâlâ '{metin or 'OKAS Kodu Seç'}'."
            )
        if hatalar:
            print("⚠️ Bazı OKAS kodları atlandı: " + "; ".join(hatalar))
        print(f"✅ {secilen}/{len(kodlar)} OKAS kodu seçildi ({adet} kalem).")
        return hatalar
    except Exception as e:
        print(f"❌ OKAS kodu seçilirken HATA: {e}")
        raise


def okas_kodu_sec(driver, wait, okas_kodu):
    okas_kodlari_sec(driver, wait, [okas_kodu])


def _ilan_tarihi_bolumu(driver):
    return driver.execute_script(
        """
        function ownText(el) {
          return Array.from(el.childNodes)
            .filter(function (n) { return n.nodeType === 3; })
            .map(function (n) { return n.textContent; })
            .join('')
            .replace(/\\s+/g, ' ')
            .trim();
        }
        var lab = Array.from(document.querySelectorAll('div, span, label, p, legend, strong, h2, h3'))
          .find(function (el) { return ownText(el) === 'İlan Tarihi'; });
        return lab ? lab.parentElement : null;
        """
    )


def _ilan_preset_tikla(driver, metin: str) -> str:
    return str(
        driver.execute_script(
            """
            function ownText(el) {
              return Array.from(el.childNodes)
                .filter(function (n) { return n.nodeType === 3; })
                .map(function (n) { return n.textContent; })
                .join('')
                .replace(/\\s+/g, ' ')
                .trim();
            }
            var lab = Array.from(document.querySelectorAll('div, span, label, p, legend, strong'))
              .find(function (el) { return ownText(el) === 'İlan Tarihi'; });
            if (!lab || !lab.parentElement) return 'no-section';
            var hedef = (arguments[0] || '').trim();
            var btn = Array.from(lab.parentElement.querySelectorAll('div, span, button, a, label, p'))
              .find(function (el) { return ownText(el) === hedef; });
            if (!btn) return 'not-found';
            btn.scrollIntoView({block:'center'});
            btn.click();
            return 'clicked:' + hedef;
            """,
            metin,
        )
    )


def _ilan_tarihi_kutulari(driver):
    return driver.execute_script(
        """
        function ownText(el) {
          return Array.from(el.childNodes)
            .filter(function (n) { return n.nodeType === 3; })
            .map(function (n) { return n.textContent; })
            .join('')
            .replace(/\\s+/g, ' ')
            .trim();
        }
        function inputNear(root, label) {
          var lab = Array.from(root.querySelectorAll('div, span, label, p'))
            .find(function (el) { return ownText(el) === label; });
          if (!lab) return null;
          var p = lab.parentElement;
          for (var i = 0; i < 8 && p; i++) {
            var inp = p.querySelector('.dx-datebox input.dx-texteditor-input, .dx-datebox input');
            if (inp) return inp;
            p = p.parentElement;
          }
          return null;
        }
        var lab = Array.from(document.querySelectorAll('div, span, label, p, legend, strong'))
          .find(function (el) { return ownText(el) === 'İlan Tarihi'; });
        if (!lab || !lab.parentElement) return [];
        var root = lab.parentElement;
        var start = inputNear(root, 'Başlangıç Tarihi');
        var end = inputNear(root, 'Bitiş Tarihi');
        if (start && end && start !== end) return [start, end];
        var inputs = Array.from(root.querySelectorAll(
          '.dx-datebox input.dx-texteditor-input, .dx-datebox input'
        ));
        return inputs.slice(0, 2);
        """
    )


def _tarih_deger_uyuyor(val: str, gun) -> bool:
    val = (val or "").strip()
    return gun.strftime("%d.%m.%Y") in val or gun.strftime("%Y-%m-%d") in val


def _takvim_gun_tikla(driver, gun) -> str:
    val = gun.strftime("%Y/%m/%d")
    return str(
        driver.execute_script(
            """
            var val = arguments[0];
            var cells = Array.from(document.querySelectorAll('.dx-calendar-cell[data-value="' + val + '"]'))
              .filter(function (c) {
                return !c.classList.contains('dx-calendar-other-month')
                  && !c.classList.contains('dx-calendar-other-view');
              });
            if (!cells.length) {
              cells = Array.from(document.querySelectorAll('.dx-calendar-cell[data-value="' + val + '"]'));
            }
            if (!cells.length) return 'no-cell:' + val;
            var c = cells[0];
            c.scrollIntoView({block:'center'});
            c.click();
            return 'clicked:' + val;
            """,
            val,
        )
    )


def ilan_tarihi_ayarla(driver, wait, baslangic, bitis):
    """İlan Tarihi > Tarih Aralığı takviminden gün seçer (DX DateRangeBox)."""
    _ = wait
    b = baslangic.strftime("%d.%m.%Y")
    e = bitis.strftime("%d.%m.%Y")
    print(f"📅 İlan tarihi: {b} — {e}")
    if _ilan_tarihi_bolumu(driver) is None:
        raise RuntimeError("İlan Tarihi bölümü bulunamadı.")

    sonuc = _ilan_preset_tikla(driver, "Tarih Aralığı")
    print(f"   İlan preset: {sonuc}")
    if not str(sonuc).startswith("clicked"):
        raise RuntimeError("İlan Tarihi 'Tarih Aralığı' seçilemedi.")
    time.sleep(0.5)

    kutular = _ilan_tarihi_kutulari(driver)
    if len(kutular) < 1:
        raise RuntimeError("İlan Tarihi tarih kutusu bulunamadı.")
    driver.execute_script(
        "arguments[0].scrollIntoView({block:'center'}); arguments[0].click();",
        kutular[0],
    )
    WebDriverWait(driver, 6).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, ".dx-calendar-cell"))
    )
    time.sleep(0.3)
    s1 = _takvim_gun_tikla(driver, baslangic)
    time.sleep(0.25)
    s2 = _takvim_gun_tikla(driver, bitis)
    print(f"   Takvim: {s1} / {s2}")
    if not str(s1).startswith("clicked") or not str(s2).startswith("clicked"):
        raise RuntimeError(f"İlan tarihi takvimden seçilemedi ({s1}, {s2}).")
    try:
        driver.execute_script(
            "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));"
        )
    except Exception:
        pass
    time.sleep(0.4)
    kutular = _ilan_tarihi_kutulari(driver)
    okunan = []
    for el in (kutular or [])[:2]:
        try:
            okunan.append((el.get_attribute("value") or "").strip())
        except StaleElementReferenceException:
            okunan.append("")
    print(f"   İlan tarihi kutuları: {okunan}")
    if len(okunan) < 2 or not (
        _tarih_deger_uyuyor(okunan[0], baslangic)
        and _tarih_deger_uyuyor(okunan[1], bitis)
    ):
        raise RuntimeError(
            f"İlan tarihi yazılamadı (beklenen {b}–{e}, okunan {okunan})."
        )


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


def arama_yap_ve_gosterimi_ayarla(
    driver, wait, gosterim_sayisi="50", okas_ust_sinir=True, gosterim_ayarla=True
):
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
    if okas_ust_sinir and n > 100000:
        raise RuntimeError(
            f"OKAS filtresi uygulanmamış görünüyor: {n} ihale listeleniyor "
            "(beklenen: yazılıma özgü yüzler/binler, milyonlar değil)."
        )

    if gosterim_ayarla:
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
    return n
