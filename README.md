# EKAP İhale Listeleme

`vendor/ihale-mcp` içindeki EKAP istemcisiyle (`search_tenders` ile aynı kod) ihale listesini çeker, hedef OKAS kodlarına ve yayım tarihi penceresine göre süzer, yeni ihaleleri mail + takvim şablonuna aktarır.

Tarayıcı (Selenium / Playwright) kullanılmaz.

## Installation

Python 3.10+ önerilir. `vendor/ihale-mcp` klonu yerinde olmalıdır (Cursor MCP sunucusu da bunu çalıştırır).

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

1. ihale-mcp istemcisi EKAP aramasını imzalı istek ile yapar
2. OKAS + tarih + hariç kelime süzgeci uygulanır
3. Duplicate kontrolü `data/ekap.db` üzerinden okunur
4. Yeni ihaleler terminale yazılır

İstemci yoksa veya API hata verirse ve `EKAP_FALLBACK=1` ise kaynak `ilan.gov.tr` olur. Mail gitmez, Google Takvim’e yazılmaz, `seen_tenders` güncellenmez.

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

`vendor/ihale-mcp` aynı makinede durmalıdır. GitHub-hosted runner bu klonu görmez (`vendor/` gitignore); orada `EKAP_FALLBACK=1` ile liste ilan.gov.tr yedeğinden gelir.

## Troubleshooting

**`ihale-mcp bulunamadı`**  
`vendor/ihale-mcp/ihale_client.py` yerinde mi bakın. Cursor MCP kaydı da bu dizini kullanır.

**MCP HTTP 428 / `HUMAN_VERIFICATION_REQUIRED`**  
Canlı `search_tenders` çağrısı şu an bu yanıtı veriyor: imza başlığı ve kabuk çerezi tek başına aramayı açmıyor. `EKAP_FALLBACK=1` iken liste ilan.gov.tr yedeğinden gelir. İmza anahtarı `vendor/ihale-mcp/ihale_client.py` içindeki `_r8fact_key` değerindedir.

**Aynı ihale her gün geliyor**  
`data/ekap.db` yazılabilir mi kontrol edin. Dry-run bu tabloyu güncellemez.

**Mail gitmiyor**  
`.env` içinde `ENTROPY_EMAIL_API_KEY` ve `EKAP_EMAIL_RECIPIENTS`. Cron denemesi: `EKAP_MAIL_GONDER=0 python main.py`

**Birim testler (canlı istek yok)**

```bash
python -m unittest test_ekap.py test_ilan_alaka.py test_report.py
```

## Mimari

```
EKAP v2 API
 → vendor/ihale-mcp EKAPClient (X-Ekap-Sec)
 → OKAS / tarih / hariç kelime
 → SQLite duplicate
 → mail + ICS / Google Takvim şablon linki
```

MCP hata verirse ve `EKAP_FALLBACK=1` ise kaynak ilan.gov.tr olur.
