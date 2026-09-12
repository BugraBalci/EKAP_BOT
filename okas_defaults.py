"""Bot, cron, GUI ve Actions'ın ortak OKAS kök listesi."""

DEFAULT_OKAS_KODLARI = [
    "32230000",
    "32220000",
    "32510000",
    "32524000",
    "32552420",
    "32552110",
    "32552430",
    "32573000",
    "32420000",
    "32410000",
    "32430000",
    "64215000",
    "64212000",
    "64216210",
    "64216100",
    "64216200",
    "64216120",
    "64216110",
    "72700000",
    "72200000",
    "72400000",
    "72300000",
    "72800000",
    "72910000",
    "31711000",
]

DEFAULT_OKAS_VIRGUL = ",".join(DEFAULT_OKAS_KODLARI)

# ilan.gov.tr yedek hattı için OKAS → serbest metin karşılıkları.
# ilan.gov.tr'de OKAS filtresi yok; arama ilan başlığında yapıldığı için
# karşılıklar tek/iki kelimeye indirilmiştir. Uzun ifadeler ("elektronik
# posta hizmeti") başlıklarda tam olarak geçmediği için hiç sonuç vermiyor.
# Karşılıklar yaklaşıktır, EKAP'ın OKAS kapsamıyla bire bir örtüşmez.
OKAS_ANAHTAR_KELIME = {
    "32230000": "telsiz",
    "32220000": "verici",
    "32510000": "kablosuz",
    "32524000": "telekomünikasyon",
    "32552420": "frekans",
    "32552110": "telsiz",
    "32552430": "modem",
    "32573000": "haberleşme",
    "32420000": "ağ donanımı",
    "32410000": "yerel ağ",
    "32430000": "network",
    "64215000": "ip telefon",
    "64212000": "mobil telefon",
    "64216210": "video konferans",
    "64216100": "mesajlaşma",
    "64216200": "bilişim",
    "64216120": "elektronik posta",
    "64216110": "veri değişimi",
    "72700000": "ağ hizmeti",
    "72200000": "yazılım",
    "72400000": "internet",
    "72300000": "veri",
    "72800000": "bilgisayar",
    "72910000": "yedekleme",
    "31711000": "elektronik malzeme",
}

# Kodu eşlenmemiş OKAS'lar için genel tarama kelimeleri.
GENEL_ANAHTAR_KELIMELER = ("bilgisayar", "yazılım", "donanım", "sunucu")
