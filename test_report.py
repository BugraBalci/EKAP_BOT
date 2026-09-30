#!/usr/bin/env python3
"""first_seen raporlama testleri — tarayıcı yok, canlı istek yok, gerçek mail yok."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from ekap.models import Tender
from ekap.pipeline import CollectionResult, persist_if_needed
from ekap.report import (
    FIRST_SEEN_DISCLAIMER,
    THIS_WEEK_DISCLAIMER,
    classify_first_seen,
    format_mail_line,
    istanbul_now,
    monday_start,
    parse_seen_at,
    should_send_mail,
    this_week_window,
    today_window,
    yesterday_window,
)
from ekap.store import TenderStore
from ekap.config import EkapConfig

ISTANBUL = ZoneInfo("Europe/Istanbul")
NOW = datetime(2026, 9, 17, 15, 30, tzinfo=ISTANBUL)  # Perşembe


def make_tender(
    n: int,
    *,
    ikn: str | None = None,
    ekap_id: str | None = None,
    title: str | None = None,
) -> Tender:
    return Tender(
        ekap_id=ekap_id if ekap_id is not None else f"id-{n}",
        title=title or f"İhale {n}",
        institution=f"İdare {n}",
        tender_datetime="20.09.2026 11:00",
        province="ANKARA",
        tender_type="Mal",
        url=f"https://ekapv2.kik.gov.tr/ekap/search/2026_T{n}",
        ikn=ikn if ikn is not None else f"2026/T{n}",
    )


class TimeWindowTests(unittest.TestCase):
    def test_monday_start_is_this_weeks_monday_midnight_istanbul(self):
        start = monday_start(NOW)
        self.assertEqual(start.tzinfo, ISTANBUL)
        self.assertEqual(start, datetime(2026, 9, 14, 0, 0, tzinfo=ISTANBUL))
        self.assertEqual(start.weekday(), 0)

    def test_monday_start_from_sunday_uses_that_weeks_monday(self):
        sunday = datetime(2026, 9, 13, 23, 15, tzinfo=ISTANBUL)
        self.assertEqual(monday_start(sunday), datetime(2026, 9, 7, 0, 0, tzinfo=ISTANBUL))

    def test_today_yesterday_this_week_windows(self):
        today_start, today_end = today_window(NOW)
        yesterday_start, yesterday_end = yesterday_window(NOW)
        week_start, week_end = this_week_window(NOW)
        self.assertEqual(today_start, datetime(2026, 9, 17, 0, 0, tzinfo=ISTANBUL))
        self.assertEqual(today_end, NOW)
        self.assertEqual(yesterday_start, datetime(2026, 9, 16, 0, 0, tzinfo=ISTANBUL))
        self.assertEqual(yesterday_end, datetime(2026, 9, 17, 0, 0, tzinfo=ISTANBUL))
        self.assertEqual(week_start, datetime(2026, 9, 14, 0, 0, tzinfo=ISTANBUL))
        self.assertEqual(week_end, NOW)

    def test_timezone_utc_instant_classified_in_istanbul(self):
        # 16.09.2026 21:00 UTC = 17.09.2026 00:00 Istanbul → bugün
        utc_today = datetime(2026, 9, 16, 21, 0, tzinfo=timezone.utc)
        flags = classify_first_seen(utc_today, NOW)
        self.assertTrue(flags["today"])
        self.assertFalse(flags["yesterday"])
        self.assertTrue(flags["this_week"])

        # 16.09.2026 20:59 UTC = 16.09.2026 23:59 Istanbul → dün
        utc_yesterday = datetime(2026, 9, 16, 20, 59, tzinfo=timezone.utc)
        flags = classify_first_seen(utc_yesterday, NOW)
        self.assertFalse(flags["today"])
        self.assertTrue(flags["yesterday"])
        self.assertTrue(flags["this_week"])

        # Pazartesi 00:00 Istanbul sınırının bir dakika öncesi (Pazar) haftaya girmez
        before_monday = datetime(2026, 9, 13, 20, 59, tzinfo=timezone.utc)
        flags = classify_first_seen(before_monday, NOW)
        self.assertFalse(flags["this_week"])

        after_monday = datetime(2026, 9, 13, 21, 0, tzinfo=timezone.utc)
        flags = classify_first_seen(after_monday, NOW)
        self.assertTrue(flags["this_week"])

    def test_naive_datetime_treated_as_istanbul(self):
        naive = datetime(2026, 9, 17, 10, 0)
        self.assertEqual(istanbul_now(naive).tzinfo, ISTANBUL)
        flags = classify_first_seen(naive, NOW)
        self.assertTrue(flags["today"])


class FirstSeenStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TenderStore(Path(self.tmp.name) / "ekap.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_tender_gets_first_seen(self):
        tender = make_tender(1)
        result = self.store.record_scan([tender], now=NOW)
        self.assertEqual(len(result.inserted), 1)
        seen = parse_seen_at(self.store.first_seen_at(tender))
        self.assertEqual(seen, NOW)

    def test_existing_tender_first_seen_does_not_change(self):
        tender = make_tender(1, title="İlk ad")
        first_now = datetime(2026, 9, 14, 9, 0, tzinfo=ISTANBUL)
        later = datetime(2026, 9, 17, 15, 30, tzinfo=ISTANBUL)
        self.store.record_scan([tender], now=first_now)
        original = self.store.first_seen_at(tender)
        renamed = make_tender(1, title="Değişen ad")
        second = self.store.record_scan([renamed], now=later)
        self.assertEqual(second.inserted, [])
        self.assertEqual(len(second.updated), 1)
        row = self.store.get(tender)
        self.assertEqual(row["first_seen_at"], original)
        self.assertEqual(parse_seen_at(row["last_seen_at"]), later)
        self.assertEqual(row["title"], "İlk ad")

    def test_unique_key_prefers_ikn_over_ekap_id(self):
        with_ikn = make_tender(1, ikn="2026/AAA", ekap_id="uuid-1")
        self.assertEqual(with_ikn.unique_key(), "2026/AAA")
        no_ikn = make_tender(2, ikn="-", ekap_id="uuid-2")
        self.assertEqual(no_ikn.unique_key(), "uuid-2")
        self.store.record_scan([with_ikn, no_ikn], now=NOW)
        self.assertIsNotNone(self.store.get(with_ikn))
        self.assertEqual(self.store.get(no_ikn)["tender_key"], "uuid-2")

    def test_today_yesterday_this_week_from_sqlite(self):
        seed = make_tender(1)
        tue = make_tender(2)
        wed = make_tender(3)
        thu = make_tender(4)
        self.store.record_scan([seed], now=datetime(2026, 9, 14, 10, 0, tzinfo=ISTANBUL))
        self.store.record_scan(
            [seed, tue], now=datetime(2026, 9, 15, 11, 0, tzinfo=ISTANBUL)
        )
        self.store.record_scan(
            [seed, tue, wed], now=datetime(2026, 9, 16, 8, 0, tzinfo=ISTANBUL)
        )
        self.store.record_scan(
            [seed, tue, wed, thu], now=NOW
        )
        meta = self.store.report_windows(now=NOW, baseline_just_completed=False)
        today_ikn = {r["İKN"] for r in meta["yeni_bugun"]}
        yesterday_ikn = {r["İKN"] for r in meta["yeni_dun"]}
        week_ikn = {r["İKN"] for r in meta["yeni_bu_hafta"]}
        self.assertEqual(today_ikn, {"2026/T4"})
        self.assertEqual(yesterday_ikn, {"2026/T3"})
        self.assertEqual(week_ikn, {"2026/T2", "2026/T3", "2026/T4"})
        self.assertNotIn("2026/T1", week_ikn)

    def test_baseline_run_does_not_send_mail(self):
        tenders = [make_tender(1), make_tender(2)]
        persist = self.store.record_scan(tenders, now=NOW)
        self.assertTrue(persist.baseline_just_completed)
        meta = self.store.report_windows(now=NOW, baseline_just_completed=True)
        self.assertTrue(meta["baseline"])
        self.assertFalse(meta["send_mail"])
        self.assertEqual(meta["yeni_bugun"], [])
        self.assertFalse(should_send_mail(meta))
        self.assertFalse(should_send_mail(baseline_just_completed=True))

    def test_second_run_reports_only_new_records(self):
        old = [make_tender(1), make_tender(2)]
        self.store.record_scan(old, now=datetime(2026, 9, 16, 10, 0, tzinfo=ISTANBUL))
        fresh = make_tender(3)
        persist = self.store.record_scan(old + [fresh], now=NOW)
        self.assertFalse(persist.baseline_just_completed)
        self.assertEqual([t.ikn for t in persist.inserted], ["2026/T3"])
        meta = self.store.report_windows(now=NOW, baseline_just_completed=False)
        self.assertTrue(should_send_mail(meta))
        self.assertEqual([r["İKN"] for r in meta["yeni_bugun"]], ["2026/T3"])
        self.assertEqual([r["İKN"] for r in meta["yeni_bu_hafta"]], ["2026/T3"])
        self.assertEqual(meta["yeni_dun"], [])


class PipelineBaselineTests(unittest.TestCase):
    def test_persist_if_needed_seeds_then_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "ekap.db"
            config = EkapConfig(db_path=db, dry_run=False, mail_enabled=True)
            first = CollectionResult(tenders=[make_tender(1), make_tender(2)])
            persist_if_needed(first, config, now=NOW)
            self.assertEqual(first.new_tenders, [])
            self.assertTrue(first.extra["meta"]["baseline"])
            self.assertFalse(should_send_mail(first.extra["meta"]))

            second = CollectionResult(
                tenders=[make_tender(1), make_tender(2), make_tender(3)]
            )
            persist_if_needed(second, config, now=NOW)
            self.assertEqual([t.ikn for t in second.new_tenders], ["2026/T3"])
            self.assertFalse(second.extra["meta"]["baseline"])
            self.assertTrue(should_send_mail(second.extra["meta"]))
            self.assertEqual(
                [r["İKN"] for r in second.extra["meta"]["yeni_bugun"]], ["2026/T3"]
            )

    def test_dry_run_does_not_write_or_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "ekap.db"
            config = EkapConfig(db_path=db, dry_run=True)
            result = CollectionResult(tenders=[make_tender(1)])
            persist_if_needed(result, config, now=NOW)
            self.assertFalse(should_send_mail(result.extra["meta"]))
            store = TenderStore(db)
            self.assertFalse(store.is_baseline_complete())
            self.assertIsNone(store.first_seen_at(make_tender(1)))


class MailRenderTests(unittest.TestCase):
    def test_mail_line_and_sections_without_sending(self):
        row = make_tender(1).to_mail_row()
        line = format_mail_line(row)
        self.assertEqual(
            line,
            "2026/T1 | İhale 1 | İdare 1 | ANKARA | 20.09.2026 11:00 | Mal | "
            "https://ekapv2.kik.gov.tr/ekap/search/2026_T1",
        )
        from email_provider import ihale_sonuclarini_maile_cevir

        today = [make_tender(4).to_mail_row()]
        yesterday = [make_tender(3).to_mail_row()]
        week = [make_tender(2).to_mail_row(), make_tender(3).to_mail_row(), make_tender(4).to_mail_row()]
        with mock.patch("email_provider.send_email") as mocked_send:
            subject, body, html = ihale_sonuclarini_maile_cevir(
                week,
                "72200000",
                yeni_meta={
                    "kaynak": "EKAP",
                    "tracking": "first_seen",
                    "yeni_bugun": today,
                    "yeni_dun": yesterday,
                    "yeni_bu_hafta": week,
                },
            )
            mocked_send.assert_not_called()
        self.assertEqual(subject, "Bugün 1 ihale, dün 1 ihale ilk kez tespit edildi")
        self.assertIn("BUGÜN İLK KEZ TESPİT EDİLEN İHALELER", body)
        self.assertIn("DÜN İLK KEZ TESPİT EDİLEN İHALELER", body)
        self.assertIn("BU HAFTA İLK KEZ TESPİT EDİLEN İHALELER", body)
        self.assertIn("Haftalık özet", body)
        self.assertIn(FIRST_SEEN_DISCLAIMER, body)
        self.assertIn(THIS_WEEK_DISCLAIMER, html)
        self.assertIn("İlk tespit tarihi", FIRST_SEEN_DISCLAIMER)
        self.assertIn("İhale 4", body)
        self.assertIn("İhale 3", body)
        self.assertIn("İhale 2", body)
        self.assertNotIn("yayimlanan", body.lower())
        self.assertNotIn("yayımlanan", html.lower())

    def test_sonuclari_email_gonder_is_mocked_and_not_live(self):
        from email_provider import sonuclari_email_gonder

        with mock.patch("email_provider.send_email") as mocked_send:
            mocked_send.return_value = {"ok": True, "status": "mocked"}
            result = sonuclari_email_gonder(
                "test@example.com",
                [],
                "72200000",
                yeni_meta={
                    "kaynak": "EKAP",
                    "tracking": "first_seen",
                    "yeni_bugun": [make_tender(1).to_mail_row()],
                    "yeni_dun": [],
                    "yeni_bu_hafta": [make_tender(1).to_mail_row()],
                },
            )
        mocked_send.assert_called_once()
        self.assertEqual(result["status"], "mocked")
        args, kwargs = mocked_send.call_args
        self.assertIn("ilk kez tespit edildi", kwargs["subject"])
        self.assertIn("BUGÜN İLK KEZ TESPİT EDİLEN İHALELER", kwargs["body"])


if __name__ == "__main__":
    unittest.main()
