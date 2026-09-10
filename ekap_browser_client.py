"""EKAP API isteklerini gerçek Chrome sayfa bağlamından atar.

httpx TLS parmak izi Cloudflare/WAF 406 üretince, imzalı POST
`fetch()` ile `/ekap/search` origin'inden gider. DOM kazımaz;
imza anahtarını sayfadan okumaz — AES başlıkları mevcut EKAPClient
üreticisinden gelir, tarayıcı yalnızca HTTP taşıyıcısıdır.

Ortam:
  EKAP_HEADED=1/0   zorunlu headed / headless
  EKAP_HEADLESS=1   headless zorla (WAF 406 verir)
  EKAP_WARMUP_SLEEP ısınma beklemesi saniye (varsayılan 3)
  DISPLAY varsa ve EKAP_HEADLESS yoksa headed kullanılır.
"""

from __future__ import annotations

import json
import os
import sys
import time
from types import SimpleNamespace
from typing import Any, Callable, Dict, Optional

_SEARCH_URL = "https://ekapv2.kik.gov.tr/ekap/search"
_CHROME_CANDIDATES = (
    os.environ.get("CHROME_BIN"),
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
)

# callback son argümandır. Gövde string; None/nested dict Selenium dönüşümüne takılmaz.
_FETCH_JS = r"""
const endpoint = arguments[0];
const bodyJson = arguments[1];
const extraHeaders = arguments[2];
const done = arguments[arguments.length - 1];
const headers = Object.assign({
  "Accept": "application/json, text/plain, */*",
  "Content-Type": "application/json",
  "api-version": "v1"
}, extraHeaders || {});
fetch(endpoint, {
  method: "POST",
  credentials: "include",
  headers: headers,
  body: bodyJson
}).then(async (r) => {
  const text = await r.text();
  let parsed = null;
  try { parsed = JSON.parse(text); } catch (e) {}
  done({
    status: r.status,
    contentType: r.headers.get("content-type") || "",
    json: parsed,
    text: parsed ? "" : String(text || "").slice(0, 400)
  });
}).catch((e) => {
  done({status: 0, contentType: "", json: null, text: String(e)});
});
"""


class BrowserHTTPError(Exception):
    """httpx.HTTPStatusError benzeri; _http_status() response.status_code okur."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status
        self.response = SimpleNamespace(status_code=status)


def _headed() -> bool:
    raw = (os.environ.get("EKAP_HEADED") or "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    headless = (os.environ.get("EKAP_HEADLESS") or "").strip().lower()
    if headless in {"1", "true", "yes", "on"}:
        return False
    # Headless Chrome 406 alıyor; gerçek veya xvfb DISPLAY şart.
    return bool(os.environ.get("DISPLAY"))


class BrowserEKAPTransport:
    def __init__(
        self,
        *,
        base_url: str,
        header_factory: Callable[[], Dict[str, str]],
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._header_factory = header_factory
        self._driver = None

    def start(self) -> None:
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        headed = _headed()
        options = webdriver.ChromeOptions()
        options.page_load_strategy = "normal"
        if not headed:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        for candidate in _CHROME_CANDIDATES:
            if candidate and os.path.isfile(candidate):
                options.binary_location = candidate
                break

        print(
            f"🌐 EKAP için Chrome açılıyor (sayfa-içi fetch, headed={headed})...",
            file=sys.stderr,
            flush=True,
        )
        driver = None
        try:
            driver = webdriver.Chrome(options=options)
            driver.set_page_load_timeout(45)
            driver.set_script_timeout(60)
            try:
                driver.execute_cdp_cmd(
                    "Page.addScriptToEvaluateOnNewDocument",
                    {
                        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                    },
                )
            except Exception:
                pass
            self._driver = driver
            self._wait = WebDriverWait(driver, 20)
            self._by = By
            self._ec = EC
            self._warmup(reload=True)
        except Exception:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
            self._driver = None
            raise

    def _warmup(self, *, reload: bool) -> None:
        assert self._driver is not None
        try:
            self._driver.get(_SEARCH_URL)
        except Exception as e:
            print(f"⚠️ EKAP sayfa yükleme: {e}", file=sys.stderr, flush=True)

        try:
            sleep_s = float(os.environ.get("EKAP_WARMUP_SLEEP") or "3")
        except ValueError:
            sleep_s = 3.0
        if sleep_s > 0:
            time.sleep(sleep_s)

        title = ""
        url = ""
        try:
            title = self._driver.title or ""
            url = self._driver.current_url or ""
        except Exception:
            pass

        found = False
        try:
            self._wait.until(
                self._ec.presence_of_element_located(
                    (self._by.XPATH, "//button[contains(., 'Filtrele')]")
                )
            )
            found = True
        except Exception:
            found = False

        if not found:
            try:
                body_txt = self._driver.execute_script(
                    "return (document.body && document.body.innerText || '').slice(0, 280);"
                ) or ""
            except Exception:
                body_txt = ""
            print(
                f"⚠️ Filtrele görünmedi, fetch yine denenecek. title={title!r} url={url} "
                f"body={body_txt!r}",
                file=sys.stderr,
                flush=True,
            )
            return

        print(
            f"🍪 EKAP arama sayfası hazır ({title!r}).",
            file=sys.stderr,
            flush=True,
        )

    def post_json(
        self,
        endpoint: str,
        params: Dict[str, Any],
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> dict:
        if self._driver is None:
            raise RuntimeError("BrowserEKAPTransport.start() çağrılmadı.")

        url = endpoint if endpoint.startswith("http") else f"{self.base_url}{endpoint}"
        body_json = json.dumps(params, ensure_ascii=False)
        last_error: Optional[BrowserHTTPError] = None

        for attempt in range(1, 3):
            sec = self._header_factory() or {}
            headers = {**(extra_headers or {}), **sec}
            # Tarayıcının UA / Origin / Referer değerini ezme; WAF bunu yakalıyor.
            for drop in ("User-Agent", "Origin", "Referer", "Accept-Language", "Connection"):
                headers.pop(drop, None)
                headers.pop(drop.lower(), None)

            result = self._driver.execute_async_script(_FETCH_JS, url, body_json, headers)
            status = int((result or {}).get("status") or 0)
            parsed = (result or {}).get("json")
            snippet = ((result or {}).get("text") or "").replace("\n", " ")[:220]

            if status == 200 and isinstance(parsed, dict):
                return parsed

            msg = (
                f"EKAP tarayıcı fetch {status} {url} "
                f"ctype={(result or {}).get('contentType')!r} body={snippet!r}"
            )
            last_error = BrowserHTTPError(status or 599, msg)
            if status in {406, 403, 429, 0} and attempt < 3:
                bekle = 2 ** attempt
                print(
                    f"⚠️ tarayıcı fetch {status}, sayfa yenilenip {bekle}s sonra tekrar "
                    f"({attempt}/3)",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(bekle)
                try:
                    self._warmup(reload=True)
                except Exception as warm_e:
                    print(f"⚠️ sayfa yenileme: {warm_e}", file=sys.stderr, flush=True)
                continue
            raise last_error

        raise last_error or BrowserHTTPError(599, f"EKAP tarayıcı fetch başarısız: {url}")

    def close(self) -> None:
        driver = self._driver
        self._driver = None
        if driver is None:
            return
        try:
            driver.quit()
        except Exception:
            pass
        print("🧹 Chrome kapatıldı.", file=sys.stderr, flush=True)
