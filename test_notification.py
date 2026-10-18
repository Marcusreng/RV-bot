import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import monitor


fake_deal = monitor.Deal(
    source="Imoova",
    origin="Los Angeles",
    destination="New York",
    start_date="2026-12-10",
    end_date="2026-12-20",
    vehicle="TEST RV - 4 personer",
    capacity=4,
    url="https://example.com/test-rv",
    details="FIKTIVT TILBUD - KUN TEST"
)


def fake_imoova(page, settings):
    return [fake_deal]


def no_deals(page, settings):
    return []


with tempfile.TemporaryDirectory() as folder:
    os.environ["STATE_FILE"] = str(Path(folder) / "seen.json")

    with (
        patch.object(monitor, "fetch", return_value="test"),
        patch.object(monitor, "parse_imoova", fake_imoova),
        patch.object(monitor, "parse_el_monte", no_deals),
        patch.object(monitor, "parse_cruise_america", no_deals),
    ):
        print("TEST 1: Nyt tilbud")
        assert monitor.run() == 0

        state, _ = monitor.load_state(
            Path(os.environ["STATE_FILE"])
        )
        assert fake_deal.id in state["seen"]

        print("TEST 2: Samme tilbud igen")

        with patch.object(monitor, "send_email") as mock_email:
            assert monitor.run() == 0
            mock_email.assert_not_called()

        print("SUCCESS: Ingen dubletmail sendt!")
