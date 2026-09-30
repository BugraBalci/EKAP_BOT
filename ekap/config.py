"""Ortam değişkenleri ve merkezi EKAP tarama ayarları."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

from okas_defaults import DEFAULT_OKAS_KODLARI, SEARCH_OKAS_KOKLERI, okas_kok_kodlari

ROOT = Path(__file__).resolve().parent.parent
EKAP_SEARCH_URL = "https://ekapv2.kik.gov.tr/ekap/search"
DEFAULT_LOOKBACK_DAYS = 2
DEFAULT_PAGE_SIZE = 50
DEFAULT_MAX_PAGES = 20
DEFAULT_HARIC = "lisans, araba"


def load_dotenv(path: Optional[Path] = None) -> None:
    env_path = path or (ROOT / ".env")
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def env_flag(name: str, default: bool = False) -> bool:
    ham = (os.environ.get(name) or "").strip().lower()
    if not ham:
        return default
    if ham in {"1", "true", "yes", "evet", "on"}:
        return True
    if ham in {"0", "false", "no", "hayir", "off"}:
        return False
    return default


def env_int(name: str, default: int) -> int:
    ham = (os.environ.get(name) or "").strip()
    if not ham:
        return default
    try:
        return int(ham)
    except ValueError:
        return default


def _split_codes(raw: str) -> List[str]:
    out: List[str] = []
    seen = set()
    for part in (raw or "").replace(";", ",").split(","):
        code = part.strip()
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def target_okas_codes(override: Optional[str] = None) -> List[str]:
    raw = override if override is not None else (os.environ.get("OKAS_KODU") or os.environ.get("TARGET_OKAS_CODES") or "")
    codes = _split_codes(raw)
    return codes or list(DEFAULT_OKAS_KODLARI)


def search_okas_codes(target: Optional[Sequence[str]] = None) -> List[str]:
    """EKAP isteğine gidecek kodlar.

    OKAS_SEARCH_CODES / SEARCH_OKAS_ROOTS doluysa onlar kullanılır.
    OKAS_SEARCH_MODE=roots ise 2 haneli CPV kökleri gider.
    Varsayılan (target): 8 haneli hedef liste `okasBransKodList` olarak gider.
    Özet yanıtta OKAS dönmediği için istemci tarafı OKAS süzgeci çalışmaz;
    kodlar istek gövdesinde gönderilmelidir.
    """
    raw = (
        os.environ.get("OKAS_SEARCH_CODES")
        or os.environ.get("SEARCH_OKAS_ROOTS")
        or ""
    ).strip()
    if raw:
        return _split_codes(raw)
    mode = (os.environ.get("OKAS_SEARCH_MODE") or "target").strip().lower()
    chosen = list(target) if target else target_okas_codes()
    if mode in {"roots", "kok", "kök"}:
        return okas_kok_kodlari(chosen) or list(SEARCH_OKAS_KOKLERI)
    return chosen


@dataclass
class EkapConfig:
    search_url: str = EKAP_SEARCH_URL
    lookback_days: int = DEFAULT_LOOKBACK_DAYS
    page_size: int = DEFAULT_PAGE_SIZE
    max_pages: int = DEFAULT_MAX_PAGES
    target_okas: List[str] = field(default_factory=target_okas_codes)
    search_okas_roots: List[str] = field(default_factory=search_okas_codes)
    exclude_words: str = DEFAULT_HARIC
    db_path: Path = ROOT / "data" / "ekap.db"
    retry_attempts: int = 3
    fallback_enabled: bool = True
    mail_enabled: bool = True
    dry_run: bool = False
    network_debug: bool = False

    @classmethod
    def from_env(
        cls,
        *,
        dry_run: bool = False,
        network_debug: bool = False,
        okas: Optional[str] = None,
        haric: Optional[str] = None,
    ) -> "EkapConfig":
        load_dotenv()
        target = target_okas_codes(okas)
        db = (os.environ.get("EKAP_DB_PATH") or "").strip()
        return EkapConfig(
            lookback_days=max(0, env_int("LOOKBACK_DAYS", DEFAULT_LOOKBACK_DAYS)),
            page_size=max(1, env_int("EKAP_PAGE_SIZE", DEFAULT_PAGE_SIZE)),
            max_pages=max(1, env_int("EKAP_MAX_PAGES", DEFAULT_MAX_PAGES)),
            target_okas=target,
            search_okas_roots=search_okas_codes(target),
            exclude_words=haric if haric is not None else (os.environ.get("HARIC_KELIME") or DEFAULT_HARIC),
            db_path=Path(db) if db else (ROOT / "data" / "ekap.db"),
            retry_attempts=max(1, env_int("EKAP_RETRY", 3)),
            fallback_enabled=env_flag("EKAP_FALLBACK", True),
            mail_enabled=env_flag("EKAP_MAIL_GONDER", True) and not dry_run,
            dry_run=dry_run,
            network_debug=network_debug,
        )
