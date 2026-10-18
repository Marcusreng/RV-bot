import unittest
from datetime import date
from pathlib import Path

from monitor import (
    Settings,
    parse_cruise_america,
    parse_imoova,
)


SETTINGS = Settings(
    target_start=date(2026, 12, 1),
    target_end=date(2027, 1, 31),
    minimum_capacity=3,
    origin_terms=("los angeles", "carson"),
    destination_terms=("new york", "linden"),
    state_file=Path("unused.json"),
    request_timeout=1,
    notify_existing_on_first_run=True,
)


class ParserTests(unittest.TestCase):
    def test_imoova_keeps_matching_capacity_and_dates(self):
        page = '''
        Los Angeles New York
        <li class="relative flex w-full flex-col">
          <a href="/relocations/usa/los-angeles-to-new-york/RLC42">
            <img alt="4 Berth RV relocation from Los Angeles to New York">
            <time dateTime="2026-12-10">10 Dec</time>
            <time dateTime="2027-01-12">12 Jan</time>
          </a>
        </li>
        '''
        deals = parse_imoova(page, SETTINGS, fetcher=lambda *_: "")
        self.assertEqual(1, len(deals))
        self.assertEqual(4, deals[0].capacity)

    def test_imoova_rejects_two_berth(self):
        page = '''
        Los Angeles New York
        <li class="relative flex w-full flex-col">
          <a href="/relocations/usa/los-angeles-to-new-york/RLC2">
            <img alt="2 Berth Campervan relocation from Los Angeles to New York">
            <time dateTime="2026-12-10"></time><time dateTime="2027-01-12"></time>
          </a>
        </li>
        '''
        self.assertEqual([], parse_imoova(page, SETTINGS, fetcher=lambda *_: ""))

    def test_cruise_america_parses_data_attributes(self):
        page = '''
        <h1>One Way Specials</h1>
        <div class="deals--list--row grid">
          <button
            data-rv-thumb-title="25&#x2019; Standard"
            data-rv-thumb-capacity="5"
            data-deal-field-discount="75%"
            data-deal-field-depart-from="Los Angeles (Carson), CA"
            data-deal-field-depart-beginning="December 10, 2026"
            data-deal-field-return-to="New York (Linden), NJ"
            data-deal-field-return-by="January 15, 2027">View Details</button>
        </div></div>
        '''
        deals = parse_cruise_america(page, SETTINGS)
        self.assertEqual(1, len(deals))
        self.assertEqual(5, deals[0].capacity)


if __name__ == "__main__":
    unittest.main()
