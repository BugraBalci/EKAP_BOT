# EKAP İhale Listeleme

Güncel [ihale-mcp](https://github.com/saidsurucu/ihale-mcp) sunucusunun `search_tenders` aracıyla ihale listesini çeker, hedef OKAS kodlarına ve yayım tarihi penceresine göre süzer, yeni ihaleleri mail + takvim şablonuna aktarır.

EKAP v2 artık AES imza başlığı (`X-Ekap-Sec`, `X-Custom-Request-*`) kabul etmiyor; istek `ekap.human-verification` çerezi olmadan 406/428 dönüyor. Bu çerezi Turnstile ile üreten taraf MCP sunucusudur. Bot EKAP’a doğrudan gitmez.

Varsayılan adres: `https://ihale.surucu.dev/mcp` (`IHALE_MCP_URL`). Eski `ihalemcp.fastmcp.app` kullanılmaz.

## Installation

Python 3.10+ önerilir. Yerel `vendor/ihale-mcp` klonu gerekmez.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

`.env` içine gerçek `ENTROPY_EMAIL_API_KEY` ve alıcı adreslerini yazın.

## Configuration

| Değişken | Anlamı | Varsayılan |
| --- | --- | --- |
| `IHALE_MCP_URL` | ihale-mcp streamable HTTP adresi | `https://ihale.surucu.dev/mcp` |
| `IHALE_MCP_TIMEOUT` | Tek MCP çağrısı zaman aşımı (sn). İlk çağrı Turnstile yüzünden uzun sürebilir | `180` |
| `LOOKBACK_DAYS` | Yayım tarihi penceresi (gün) | `2` |
| `OKAS_KODU` | Hedef OKAS kodları (virgül) | `okas_defaults.py` listesi |
| `OKAS_SEARCH_MODE` | `roots` (32/64/72/31) veya `target` (25 kod) | `target` |
| `OKAS_SEARCH_CODES` | MCP isteğine gidecek kodlar (boşsa mode'a göre) | — |
| `SEARCH_OKAS_ROOTS` | `OKAS_SEARCH_CODES` ile aynı (eski ad) | — |
| `HARIC_KELIME` | Başlıkta elenecek kelimeler | `lisans, araba` |
| `EKAP_FALLBACK` | MCP olmazsa ilan.gov.tr | `1` |
| `EKAP_PAGE_SIZE` | Sayfa başına kayıt (en fazla 100) | `50` |
| `EKAP_MAX_PAGES` | En fazla sayfa | `20` |
| `EKAP_DB_PATH` | Duplicate SQLite | `data/ekap.db` |
| `EKAP_MAIL_GONDER` | `0` = bülteni dosyaya yaz, gönderme | `1` |
| `EKAP_EMAIL_RECIPIENTS` | Virgülle alıcılar | — |
| `ENTROPY_EMAIL_API_KEY` | MSA mail API | — |

Hedef OKAS kodları projedeki gerçek listedir (`32230000`, `72200000`, `31711000`, …).

## Dry Run

```bash
python main.py --dry-run
```

1. Bot `search_tenders` aracını güncel MCP sunucusuna çağırır
2. OKAS + tarih + hariç kelime süzgeci uygulanır
3. Duplicate kontrolü `data/ekap.db` üzerinden okunur
4. Yeni ihaleler terminale yazılır

MCP hata verirse ve `EKAP_FALLBACK=1` ise kaynak `ilan.gov.tr` olur. Mail gitmez, Google Takvim’e yazılmaz, `seen_tenders` güncellenmez.

İstek özeti için: `python main.py --network-debug --dry-run`

## Production Run

```bash
python main.py
```

Aynı tarama + süzgeç + duplicate işaretleme + sabah bülteni (HTML mail + `.ics` eki). Takvim yalnızca maildeki “Takvime Ekle” şablon linkleridir; Calendar API çağrılmaz.

Eski Tkinter arayüzü: `python main.py --gui`

## Cron Setup

```cron
30 8 * * * cd /path/to/project && /path/to/venv/bin/python main.py >> logs/ekap.log 2>&1
```

GitHub Actions da aynı uzak MCP adresini kullanır. Sunucu Turnstile’ı geçemezse ve `EKAP_FALLBACK=1` ise liste ilan.gov.tr yedeğinden gelir.

Kendi makinenizde ihale-mcp çalıştırıyorsanız adresi değiştirin:

```bash
IHALE_MCP_URL=http://127.0.0.1:8000/mcp python main.py --dry-run
```

## Troubleshooting

**Turnstile / `HUMAN_VERIFICATION_REQUIRED` / HTTP 406**  
Bot bu hataları yeniden denemez; `EKAP_FALLBACK=1` iken kaynak ilan.gov.tr olur. Uzak sunucu EKAP sayfasını açamazsa ihale-mcp’yi yerelde çalıştırıp `IHALE_MCP_URL` ile bağlanın (`uvx --from git+https://github.com/saidsurucu/ihale-mcp ihale-mcp`). AES başlıklarını güncellemeye çalışmayın; EKAP ön yüzü onları artık göndermiyor.

**Aynı ihale her gün geliyor**  
`data/ekap.db` yazılabilir mi kontrol edin. Dry-run bu tabloyu güncellemez.

**Mail gitmiyor**  
`.env` içinde `ENTROPY_EMAIL_API_KEY` ve `EKAP_EMAIL_RECIPIENTS`. Cron denemesi: `EKAP_MAIL_GONDER=0 python main.py`

GitHub Actions her sabah temiz makinede açılır. `data/ekap.db` bir sonraki güne taşınmazsa her çalışma ilk tarama sayılır ve bülten gönderilmez; ilan.gov.tr yedek hattı kayıt bulsa da mail kesilir. Workflow bu dosyayı Actions cache ile saklar. Cache boşsa ilk çalışma yine tohumdur, ertesi sabah yalnızca yeni ilanlar gider.

**Birim testler (canlı istek yok)**

```bash
python -m unittest test_ekap.py test_ilan_alaka.py test_report.py
```

## Mimari

```
https://ihale.surucu.dev/mcp  (search_tenders)
 → OKAS / tarih / hariç kelime
 → SQLite duplicate
 → mail + ICS / Google Takvim şablon linki
```

MCP hata verirse ve `EKAP_FALLBACK=1` ise kaynak ilan.gov.tr olur.
