"""EKAP toplama hattı: ihale-mcp → filtre → duplicate → (opsiyonel) yedek hat."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from ekap.config import EkapConfig
from ekap.filters import (
    excluded_by_keywords,
    filter_by_lookback,
    filter_by_okas,
    parse_exclude_words,
)
from ekap.mcp_source import EkapMcpError, collect_tenders
from ekap.models import Tender, parse_date_value
from ekap.report import empty_meta, istanbul_now
from ekap.store import TenderStore

log = logging.getLogger("ekap.pipeline")


@dataclass
class CollectionResult:
    tenders: List[Tender] = field(default_factory=list)
    new_tenders: List[Tender] = field(default_factory=list)
    duplicates: List[Tender] = field(default_factory=list)
    raw_count: int = 0
    okas_passed: int = 0
    date_passed: int = 0
    source: str = "EKAP"
    errors: List[str] = field(default_factory=list)
    extra: Dict = field(default_factory=dict)

    def mail_rows(self) -> List[Dict[str, str]]:
        return [t.to_mail_row() for t in self.tenders]

    def new_mail_rows(self) -> List[Dict[str, str]]:
        return [t.to_mail_row() for t in self.new_tenders]


def _apply_filters(tenders: List[Tender], config: EkapConfig) -> CollectionResult:
    raw_count = len(tenders)
    words = parse_exclude_words(config.exclude_words)
    if words:
        tenders = [t for t in tenders if not excluded_by_keywords(t, words)]

    after_okas, dropped_okas = filter_by_okas(tenders, config.target_okas)
    after_date, dropped_date = filter_by_lookback(after_okas, config.lookback_days)

    store = TenderStore(config.db_path)
    new_tenders, duplicates = store.split_new(after_date)

    result = CollectionResult(
        tenders=list(after_date),
        new_tenders=new_tenders,
        duplicates=duplicates,
        raw_count=raw_count,
        okas_passed=len(after_okas),
        date_passed=len(after_date),
        source="EKAP",
        extra={
            "dropped_okas": dropped_okas,
            "dropped_date": dropped_date,
            "meta": empty_meta(source="EKAP", send_mail=False),
        },
    )
    result.extra["meta"]["kaynak"] = result.source
    log.info(
        "Sonuç: ham=%s OKAS geçen=%s tarih geçen=%s yeni=%s duplicate=%s",
        raw_count,
        result.okas_passed,
        result.date_passed,
        len(new_tenders),
        len(duplicates),
    )
    if dropped_okas:
        log.info("OKAS süzgeci %s kayıt elendi", dropped_okas)
    if dropped_date:
        log.info("Tarih süzgeci %s kayıt elendi", dropped_date)
    return result


def _from_legacy_row(row: Dict[str, str]) -> Tender:
    ikn = (row.get("İKN") or "").strip()
    title = (row.get("İşin Adı") or "").strip()
    institution = (row.get("Kurum") or row.get("İhaleyi Veren Kurum") or "").strip()
    ekap_id = ikn if ikn and ikn != "-" else (row.get("İlan Id") or row.get("Link") or f"{institution}|{title}")
    return Tender(
        ekap_id=ekap_id,
        title=title or "-",
        institution=institution or "-",
        publish_date=parse_date_value(row.get("Yayım Tarihi")),
        tender_date=parse_date_value(row.get("İhale Tarihi")),
        tender_datetime=(row.get("İhale Tarihi") or "").strip(),
        url=(row.get("Link") or "").strip(),
        description=(row.get("İhale Detayları") or title),
        ikn=ikn or "-",
        province=(row.get("İl") or "").strip(),
        status=(row.get("Durum") or "").strip(),
        tender_type=(row.get("Tür") or "").strip(),
        source=(row.get("Kaynak") or "ilan.gov.tr"),
        raw=dict(row),
    )


def _fallback(config: EkapConfig, reason: str) -> CollectionResult:
    from ilan_fallback import IlanYedekHatasi, okas_ile_tara
    from ilan_alaka import kayitlari_suz

    log.warning("EKAP kullanılamıyor (%s); ilan.gov.tr yedek hattı", reason)
    try:
        rows = okas_ile_tara(
            config.target_okas,
            limit=20,
            gun=max(config.lookback_days, 2),
            dislanacak_kelime=config.exclude_words or "",
            log=log.info,
        )
        temiz, _ozet = kayitlari_suz(rows)
    except IlanYedekHatasi as exc:
        result = CollectionResult(source="ilan.gov.tr", errors=[f"{reason}; yedek hat: {exc}"])
        result.extra["meta"] = empty_meta(
            source="ilan.gov.tr",
            send_mail=False,
            saglik_ozeti=reason,
        )
        return result

    tenders = [_from_legacy_row(r) for r in temiz]
    result = _apply_filters(tenders, config)
    result.source = "ilan.gov.tr"
    result.extra.setdefault("meta", {})["kaynak"] = "ilan.gov.tr"
    result.extra["meta"]["saglik_ozeti"] = reason
    return result


def persist_if_needed(
    result: CollectionResult,
    config: EkapConfig,
    *,
    now: Optional[datetime] = None,
) -> None:
    """Başarılı taramadaki tüm ihaleleri first-seen store'a yazar.

    İlk başarılı çalışma baseline/seed olur; mail gitmez.
    EKAP arama/parser koduna dokunulmaz.
    """
    meta = dict(result.extra.get("meta") or empty_meta(source=result.source))
    meta["kaynak"] = result.source
    if config.dry_run:
        meta["send_mail"] = False
        result.extra["meta"] = meta
        return
    if result.errors and not result.tenders:
        meta["send_mail"] = False
        result.extra["meta"] = meta
        return

    store = TenderStore(config.db_path)
    persist = store.record_scan(result.tenders, now=istanbul_now(now))
    meta = store.report_windows(
        now=istanbul_now(now),
        source=result.source,
        baseline_just_completed=persist.baseline_just_completed,
    )
    if result.extra.get("meta", {}).get("saglik_ozeti"):
        meta["saglik_ozeti"] = result.extra["meta"]["saglik_ozeti"]
    result.extra["persist"] = {
        "inserted": len(persist.inserted),
        "updated": len(persist.updated),
        "baseline": persist.baseline_just_completed,
    }
    result.extra["meta"] = meta
    if persist.baseline_just_completed:
        result.new_tenders = []
        log.info(
            "İlk başarılı tarama baseline olarak kaydedildi (%s ihale); mail gönderilmeyecek",
            len(persist.inserted),
        )
    else:
        result.new_tenders = list(persist.inserted)


def print_result(result: CollectionResult) -> None:
    meta = result.extra.get("meta") or {}
    persist = result.extra.get("persist") or {}
    rows = result.new_tenders or result.tenders
    print("")
    print(
        f"Kaynak={result.source} ham={result.raw_count} "
        f"OKAS={result.okas_passed} tarih={result.date_passed} "
        f"yeni={len(result.new_tenders)} duplicate={len(result.duplicates)}"
    )
    if persist.get("baseline") or meta.get("baseline"):
        print(
            "İlk başarılı tarama baseline/seed olarak kaydedildi; "
            "mail gönderilmedi. Sonraki taramalarda yalnızca yeni ilk tespitler raporlanır."
        )
    if result.errors:
        for err in result.errors:
            print(f"HATA: {err}")
    if meta.get("send_mail") is False and not (persist.get("baseline") or meta.get("baseline")):
        print("Mail gönderilmeyecek.")
    if not rows:
        print("Gösterilecek yeni ihale yok.")
        return
    print(f"{'İKN':<16} {'Kurum':<28} {'İşin Adı'}")
    for tender in rows:
        print(
            f"{(tender.ikn or '-')[:16]:<16} "
            f"{(tender.institution or '-')[:28]:<28} "
            f"{(tender.title or '-')[:70]}"
        )


async def run_collection(config: Optional[EkapConfig] = None) -> CollectionResult:
    config = config or EkapConfig.from_env()
    try:
        tenders, extra = await collect_tenders(config)
        result = _apply_filters(tenders, config)
        result.extra.update(extra)
        result.extra.setdefault("meta", empty_meta(source="EKAP"))
        result.extra["meta"]["kaynak"] = "EKAP"
        persist_if_needed(result, config)
        return result
    except EkapMcpError as exc:
        log.error("MCP araması başarısız: %s", exc)
        if not config.fallback_enabled:
            return CollectionResult(errors=[str(exc)])
        result = _fallback(config, str(exc))
        persist_if_needed(result, config)
        return result
    except Exception as exc:
        log.error("MCP istemci hatası: %s", exc)
        if not config.fallback_enabled:
            return CollectionResult(errors=[f"{type(exc).__name__}: {exc}"])
        result = _fallback(config, f"{type(exc).__name__}: {exc}")
        persist_if_needed(result, config)
        return result
