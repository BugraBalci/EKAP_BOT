#!/usr/bin/env python3
"""EKAP parser, OKAS, tarih ve duplicate birim testleri (tarayıcı yok, canlı istek yok)."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from ekap.config import search_okas_codes
from ekap.mcp_source import (
    EkapMcpError,
    announcement_window,
    formatted_to_item,
    paginate_mcp,
    tenders_from_mcp_result,
)
from ekap.filters import filter_by_lookback, filter_by_okas, okas_matches
from ekap.models import (
    Tender,
    extract_item_list,
    extract_total_count,
    looks_like_search_payload,
    parse_date_value,
    tender_from_item,
)
from ekap.parser import parse_search_json
from ekap.store import TenderStore
from okas_defaults import DEFAULT_OKAS_KODLARI, SEARCH_OKAS_KOKLERI, okas_kok_kodlari


def make_summary_item(index: int) -> dict:
    return {
        "dokumanButonuGosterilsinMi": False,
        "dokumanIndirenlerGosterilsinMi": False,
        "dokumanListe": [
            {"id": f"doc-{index}", "ihaleId": f"test-id-{index}", "tarih": "16.09.2026"}
        ],
        "dokumanSayisi": 1,
        "id": f"test-id-{index}",
        "idareAdi": "TEST KURUMU",
        "idareIdHash": "hash",
        "ihaleAdi": f"Test İhalesi {index}",
        "ihaleDurum": "2",
        "ihaleDurumAciklama": "İhale İlanı Yayımlanmış, Katılıma Açık",
        "ihaleIlAdi": "ANKARA",
        "ihaleTarihSaat": "16.09.2026 11:00",
        "ihaleTip": "1",
        "ihaleTipAciklama": "Mal",
        "ihaleUsul": None,
        "ihaleUsulAciklama": "İhale Usulü: Açık",
        "ihaleUsuluAltMaddeAciklama": None,
        "ikn": f"2026/TEST{index}",
        "ilanVarMi": True,
        "istisnaUsulAciklama": None,
        "paylasButonuGosterilsinMi": False,
        "takipEdiliyorMu": False,
        "teknikSartnamesizDokumanIndirButonuGosterilsinMi": False,
    }


SAMPLE_PAYLOAD = {
    "list": [make_summary_item(1)],
    "totalCount": 1,
}


class OkasMatchTests(unittest.TestCase):
    def test_exact(self):
        self.assertTrue(okas_matches("72200000", ["72200000"]))

    def test_root_covers_child(self):
        self.assertTrue(okas_matches("32230000", ["32000000"]))
        self.assertTrue(okas_matches("32000000", ["32230000"]))

    def test_other_branch_rejected(self):
        self.assertFalse(okas_matches("31711000", ["32000000"]))
        self.assertFalse(okas_matches("45233120", ["72200000", "32420000"]))

    def test_roots_from_project_targets(self):
        roots = okas_kok_kodlari(["32230000", "72200000", "64215000", "31711000"])
        self.assertEqual(roots, ["32000000", "72000000", "64000000", "31000000"])
        self.assertNotIn("48000000", roots)
        self.assertNotIn("48000000", SEARCH_OKAS_KOKLERI)

    def test_search_mode_roots_and_target(self):
        targets = ["32230000", "72200000"]
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"OKAS_SEARCH_CODES", "SEARCH_OKAS_ROOTS", "OKAS_SEARCH_MODE"}
        }
        env["OKAS_SEARCH_MODE"] = "roots"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(search_okas_codes(targets), ["32000000", "72000000"])
        env["OKAS_SEARCH_MODE"] = "target"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(search_okas_codes(targets), targets)
        env.pop("OKAS_SEARCH_MODE", None)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(search_okas_codes(targets), targets)

    def test_default_list_has_twenty_five_codes(self):
        self.assertEqual(len(DEFAULT_OKAS_KODLARI), 25)
        self.assertEqual(len(set(DEFAULT_OKAS_KODLARI)), 25)


class DateFilterTests(unittest.TestCase):
    def test_publish_date_window(self):
        today = date.today()
        tenders = [
            Tender(ekap_id="1", title="yeni", institution="A", publish_date=today),
            Tender(
                ekap_id="2",
                title="eski",
                institution="B",
                publish_date=today - timedelta(days=10),
            ),
            Tender(
                ekap_id="3",
                title="tarihsiz",
                institution="C",
                publish_date=None,
                tender_date=today + timedelta(days=20),
            ),
        ]
        kept, dropped = filter_by_lookback(tenders, 2, today=today)
        titles = {t.title for t in kept}
        self.assertIn("yeni", titles)
        self.assertNotIn("eski", titles)
        self.assertIn("tarihsiz", titles)
        self.assertEqual(dropped, 1)

    def test_lookback_does_not_use_ihale_tarih_saat(self):
        today = date.today()
        tenders, _ = parse_search_json(SAMPLE_PAYLOAD)
        self.assertIsNone(tenders[0].publish_date)
        self.assertEqual(tenders[0].tender_date, date(2026, 9, 16))
        kept, dropped = filter_by_lookback(tenders, 2, today=today)
        self.assertEqual(dropped, 0)
        self.assertEqual(len(kept), 1)

    def test_parse_tr_and_iso(self):
        self.assertEqual(parse_date_value("14.09.2026"), date(2026, 9, 14))
        self.assertEqual(parse_date_value("16.09.2026 11:00"), date(2026, 9, 16))
        self.assertEqual(parse_date_value("2026-09-14T09:00:00"), date(2026, 9, 14))


class DuplicateTests(unittest.TestCase):
    def test_sqlite_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TenderStore(Path(tmp) / "ekap.db")
            first = Tender(ekap_id="9001", title="A", institution="X", ikn="2026/111")
            second = Tender(ekap_id="9002", title="B", institution="Y", ikn="2026/222")
            new, dupes = store.split_new([first, second])
            self.assertEqual(len(new), 2)
            self.assertEqual(dupes, [])
            store.mark_seen(new)
            new2, dupes2 = store.split_new(
                [
                    first,
                    second,
                    Tender(ekap_id="9003", title="C", institution="Z", ikn="2026/333"),
                ]
            )
            self.assertEqual([t.ekap_id for t in new2], ["9003"])
            self.assertEqual({t.ekap_id for t in dupes2}, {"9001", "9002"})


def _mcp_page(item: dict, total: int) -> dict:
    raw = item
    return {
        "tenders": [
            {
                "id": raw["id"],
                "name": raw["ihaleAdi"],
                "ikn": raw["ikn"],
                "type": {"code": raw["ihaleTip"], "description": raw["ihaleTipAciklama"]},
                "method": raw["ihaleUsulAciklama"],
                "status": {
                    "code": raw["ihaleDurum"],
                    "description": raw["ihaleDurumAciklama"],
                },
                "authority": raw["idareAdi"],
                "province": raw["ihaleIlAdi"],
                "tender_datetime": raw["ihaleTarihSaat"],
                "document_count": raw["dokumanSayisi"],
                "has_announcement": raw["ilanVarMi"],
            }
        ],
        "total_count": total,
        "returned_count": 1,
    }


class McpMappingTests(unittest.TestCase):
    def test_announcement_window(self):
        start, end = announcement_window(2, today=date(2026, 9, 30))
        self.assertEqual(start, "2026-09-28")
        self.assertEqual(end, "2026-09-30")
        self.assertEqual(announcement_window(0, today=date(2026, 9, 30)), (None, None))

    def test_formatted_item_roundtrip(self):
        page = _mcp_page(make_summary_item(1), 1)
        item = formatted_to_item(page["tenders"][0])
        self.assertEqual(item["ikn"], "2026/TEST1")
        self.assertEqual(item["idareAdi"], "TEST KURUMU")
        tenders, total = tenders_from_mcp_result(page)
        self.assertEqual(total, 1)
        self.assertEqual(tenders[0].title, "Test İhalesi 1")
        self.assertEqual(tenders[0].tender_datetime, "16.09.2026 11:00")

    def test_api_error_is_not_retryable_for_428(self):
        err = EkapMcpError("blocked", retryable=False)
        self.assertFalse(err.retryable)
        with self.assertRaises(EkapMcpError) as caught:
            tenders_from_mcp_result(
                {"error": "API request failed with status 428", "message": "428"}
            )
        self.assertFalse(caught.exception.retryable)


class ParserTests(unittest.TestCase):
    def test_extracts_list_and_total_count(self):
        items = extract_item_list(SAMPLE_PAYLOAD)
        self.assertEqual(len(items), 1)
        self.assertEqual(extract_total_count(SAMPLE_PAYLOAD, items), 1)
        self.assertEqual(extract_item_list({"items": [make_summary_item(1)], "totalCount": 1}), [])
        self.assertTrue(looks_like_search_payload(SAMPLE_PAYLOAD))

    def test_json_to_tender_maps_verified_fields(self):
        tenders, total = parse_search_json(SAMPLE_PAYLOAD)
        self.assertEqual(total, 1)
        self.assertEqual(len(tenders), 1)
        first = tenders[0]
        self.assertEqual(first.ekap_id, "test-id-1")
        self.assertEqual(first.ikn, "2026/TEST1")
        self.assertEqual(first.title, "Test İhalesi 1")
        self.assertEqual(first.institution, "TEST KURUMU")
        self.assertEqual(first.province, "ANKARA")
        self.assertEqual(first.tender_datetime, "16.09.2026 11:00")
        self.assertEqual(first.tender_date, date(2026, 9, 16))
        self.assertEqual(first.status_code, "2")
        self.assertEqual(first.status, "İhale İlanı Yayımlanmış, Katılıma Açık")
        self.assertEqual(first.tender_type_code, "1")
        self.assertEqual(first.tender_type, "Mal")
        self.assertEqual(first.procedure, "İhale Usulü: Açık")
        self.assertTrue(first.has_announcement)
        self.assertIsNone(first.publish_date)
        self.assertEqual(first.okas_codes, [])
        self.assertEqual(first.okas_code, "")
        row = first.to_mail_row()
        self.assertEqual(row["İhale Tarihi"], "16.09.2026 11:00")
        self.assertEqual(row["Yayım Tarihi"], "")
        self.assertTrue(first.url.endswith("2026_TEST1"))

    def test_does_not_invent_publish_date_or_okas(self):
        item = make_summary_item(1)
        item["ilanTarihSaat"] = "14.09.2026 09:00"
        item["ilanTarihi"] = "14.09.2026"
        item["okasKod"] = "72200000"
        item["okasBransAdiList"] = ["Yazılım"]
        tender = tender_from_item(item)
        self.assertIsNotNone(tender)
        self.assertIsNone(tender.publish_date)
        self.assertEqual(tender.okas_codes, [])
        self.assertEqual(tender.tender_datetime, "16.09.2026 11:00")

    def test_unknown_shape_does_not_crash(self):
        tenders, total = parse_search_json({"unexpected": True})
        self.assertEqual(tenders, [])
        self.assertEqual(total, 0)
        self.assertIsNone(tender_from_item({"foo": "bar"}))

    def test_okas_filter_keeps_summary_without_okas(self):
        tenders, _ = parse_search_json(SAMPLE_PAYLOAD)
        kept, dropped = filter_by_okas(tenders, ["72200000", "32420000"])
        self.assertEqual(dropped, 0)
        self.assertEqual([t.ikn for t in kept], ["2026/TEST1"])


class PaginationTests(unittest.TestCase):
    def test_skip_advances_by_take_and_stops_at_total_count(self):
        items = [make_summary_item(i) for i in range(1, 24)]
        calls = []

        def fetch_page(skip, take):
            calls.append((skip, take))
            chunk = items[skip : skip + take]
            return {
                "tenders": [_mcp_page(item, 23)["tenders"][0] for item in chunk],
                "total_count": 23,
                "returned_count": len(chunk),
            }

        collected, total, skips = paginate_mcp(fetch_page, take=10, max_pages=20)
        self.assertEqual(total, 23)
        self.assertEqual(len(collected), 23)
        self.assertEqual(skips, [0, 10, 20])
        self.assertEqual(calls, [(0, 10), (10, 10), (20, 10)])
        self.assertEqual(collected[0].ekap_id, "test-id-1")
        self.assertEqual(collected[-1].ekap_id, "test-id-23")


if __name__ == "__main__":
    unittest.main()
