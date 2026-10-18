#!/usr/bin/env python3
"""Hourly LA -> New York RV relocation monitor.

Uses only Python's standard library. Public pages are fetched at a deliberately
low frequency; no login, booking endpoint, or private API is used.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import smtplib
import ssl
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


IMOOVA_URL = "https://www.imoova.com/deals/usa/los-angeles-to-new-york"
EL_MONTE_URL = "https://www.elmonterv.com/rental-deals"
CRUISE_AMERICA_URL = "https://www.cruiseamerica.com/rv-rentals/rental-deals/one-way-specials"
USER_AGENT = "RVRelocationMonitor/1.0 (+personal hourly deal monitor)"


@dataclass(frozen=True)
class Deal:
    source: str
    origin: str
    destination: str
    start_date: str | None
    end_date: str | None
    vehicle: str
    capacity: int | None
    url: str
    details: str = ""

    @property
    def id(self) -> str:
        raw = "|".join(
            [
                self.source,
                self.origin,
                self.destination,
                self.start_date or "",
                self.end_date or "",
                self.vehicle,
                self.url,
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class Settings:
    target_start: date
    target_end: date
    minimum_capacity: int
    origin_terms: tuple[str, ...]
    destination_terms: tuple[str, ...]
    state_file: Path
    request_timeout: int
    notify_existing_on_first_run: bool


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_csv_env(name: str, default: Iterable[str]) -> tuple[str, ...]:
    value = os.getenv(name)
    items = value.split(",") if value else list(default)
    return tuple(item.strip().lower() for item in items if item.strip())


def load_settings() -> Settings:
    return Settings(
        target_start=date.fromisoformat(os.getenv("TARGET_START", "2026-12-01")),
        target_end=date.fromisoformat(os.getenv("TARGET_END", "2027-01-31")),
        minimum_capacity=int(os.getenv("MIN_CAPACITY", "3")),
        origin_terms=parse_csv_env(
            "ORIGIN_TERMS",
            ("los angeles", "carson", "santa fe springs", "agoura hills"),
        ),
        destination_terms=parse_csv_env(
            "DESTINATION_TERMS",
            ("new york", "linden", "newark", "kenvil"),
        ),
        state_file=Path(os.getenv("STATE_FILE", ".state/seen.json")),
        request_timeout=int(os.getenv("REQUEST_TIMEOUT", "30")),
        notify_existing_on_first_run=env_bool("NOTIFY_EXISTING_ON_FIRST_RUN", True),
    )


def fetch(url: str, timeout: int) -> str:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            req = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            with urlopen(req, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2**attempt)
    raise RuntimeError(f"Kunne ikke hente {url}: {last_error}")


def clean_markup(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    return " ".join(value.split())


def parse_us_date(value: str | None) -> date | None:
    if not value:
        return None
    value = value.strip().replace("Sept ", "Sep ")
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def date_matches(start: str | None, end: str | None, settings: Settings) -> bool:
    start_date = parse_us_date(start)
    end_date = parse_us_date(end)
    if start_date and end_date:
        return start_date <= settings.target_end and end_date >= settings.target_start
    if start_date:
        return settings.target_start <= start_date <= settings.target_end
    return True  # Unknown dates are kept to avoid a false negative.


def location_matches(value: str, terms: tuple[str, ...]) -> bool:
    normalized = clean_markup(value).lower()
    return any(term in normalized for term in terms)


def capacity_matches(capacity: int | None, settings: Settings) -> bool:
    return capacity is None or capacity >= settings.minimum_capacity


def parse_imoova(
    page: str,
    settings: Settings,
    fetcher: Callable[[str, int], str] = fetch,
) -> list[Deal]:
    if "Los Angeles" not in page or "New York" not in page:
        raise ValueError("Imoova-siden havde et uventet format")

    deals: list[Deal] = []
    blocks = re.findall(
        r'<li class="relative flex w-full flex-col">(.*?)</li>', page, re.DOTALL
    )
    for block in blocks:
        href_match = re.search(r'href="([^"]*/relocations/[^"?]+)"', block)
        if not href_match:
            continue
        href = html.unescape(href_match.group(1))
        times = re.findall(r'<time[^>]*dateTime="([^"]+)"', block)
        start = times[0] if times else None
        end = times[1] if len(times) > 1 else None
        if not date_matches(start, end, settings):
            continue

        alt_match = re.search(r'alt="([^"]+)"', block)
        alt = html.unescape(alt_match.group(1)) if alt_match else ""
        capacity_match = re.search(r"(\d+)\s*(?:Berth|Sleeper|Seats?)", alt, re.I)
        capacity = int(capacity_match.group(1)) if capacity_match else None
        vehicle = re.sub(r"\s+relocation\s+from.*", "", alt, flags=re.I).strip()
        url = urljoin(IMOOVA_URL, href)

        if capacity is None:
            detail = fetcher(url, settings.request_timeout)
            sleep_match = re.search(r"seatbelts:(\d+),sleeps:(\d+|null)", detail)
            if sleep_match:
                seats = int(sleep_match.group(1))
                sleeps = None if sleep_match.group(2) == "null" else int(sleep_match.group(2))
                capacity = min(seats, sleeps) if sleeps is not None else seats
            visible_vehicle = re.search(
                r'aria-label="Seats"[^>]*>.*?</span>.*?(\d+).*?Seats',
                detail,
                re.DOTALL | re.I,
            )
            if capacity is None and visible_vehicle:
                capacity = int(visible_vehicle.group(1))

        if not capacity_matches(capacity, settings):
            continue
        text = clean_markup(block)
        duration = ""
        duration_matches = re.findall(
            r"\b\d+(?:\s*\+\s*\d+)?\s+(?:nights|x\s*24hrs)\b", text, re.I
        )
        if duration_matches:
            duration = duration_matches[-1]
        deals.append(
            Deal(
                source="Imoova",
                origin="Los Angeles",
                destination="New York",
                start_date=start,
                end_date=end,
                vehicle=vehicle or "Ukendt køretøj",
                capacity=capacity,
                url=url,
                details=duration,
            )
        )
    return deals


def decode_next_flight(page: str) -> dict[str, Any]:
    pattern = re.compile(r'self\.__next_f\.push\(\[1,("(?:\\.|[^"\\])*")\]\)')
    pieces: list[str] = []
    for match in pattern.finditer(page):
        try:
            pieces.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            continue
    records: dict[str, Any] = {}
    for line in "".join(pieces).splitlines():
        key, separator, raw = line.partition(":")
        if not separator or not re.fullmatch(r"[0-9a-f]+", key):
            continue
        try:
            records[key] = json.loads(raw)
        except json.JSONDecodeError:
            continue
    return records


def resolve_flight(value: Any, records: dict[str, Any], trail: frozenset[str] = frozenset()) -> Any:
    if isinstance(value, str):
        match = re.fullmatch(r"\$([0-9a-f]+)", value)
        if not match:
            return value
        key = match.group(1)
        if key in trail or key not in records:
            return value
        return resolve_flight(records[key], records, trail | {key})
    if isinstance(value, list):
        return [resolve_flight(item, records, trail) for item in value]
    if isinstance(value, dict):
        return {key: resolve_flight(item, records, trail) for key, item in value.items()}
    return value


EL_MONTE_CAPACITIES = {
    "class c small": 4,
    "class c medium": 5,
    "class c large": 6,
    "class c family sleeper": 6,
    "mighty class a": 6,
    "mighty class b camper van": 2,
    "mighty class c medium": 5,
    "mighty class c large": 6,
}


def list_items(value: Any) -> list[Any]:
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        return value["items"]
    return []


def parse_el_monte(page: str, settings: Settings) -> list[Deal]:
    if "RV Rental Specials" not in page:
        raise ValueError("El Monte RV-siden havde et uventet format")
    records = decode_next_flight(page)
    candidates: list[dict[str, Any]] = []
    for value in records.values():
        if isinstance(value, dict) and value.get("fullSlug") and value.get("cardHeading"):
            resolved = resolve_flight(value, records)
            if isinstance(resolved, dict):
                candidates.append(resolved)

    output: list[Deal] = []
    seen_slugs: set[str] = set()
    for item in candidates:
        slug = str(item.get("fullSlug", ""))
        if not slug or slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        categories = [
            str(category.get("name", ""))
            for category in list_items(item.get("dealCategory"))
            if isinstance(category, dict)
        ]
        if not any("one-way" in category.lower() for category in categories):
            continue
        origins = [
            str(location.get("name", ""))
            for location in list_items(item.get("pickUpLocation"))
            if isinstance(location, dict)
        ]
        destinations = [
            str(location.get("name", ""))
            for location in list_items(item.get("dropOffLocation"))
            if isinstance(location, dict)
        ]
        matching_origins = [o for o in origins if location_matches(o, settings.origin_terms)]
        matching_destinations = [d for d in destinations if location_matches(d, settings.destination_terms)]
        if not matching_origins or not matching_destinations:
            continue

        start = str(item.get("pickUpDate") or "") or None
        end = str(item.get("dropOffDate") or "") or None
        months = {
            int(month["value"])
            for month in list_items(item.get("pickUpMonths"))
            if isinstance(month, dict) and str(month.get("value", "")).isdigit()
        }
        if not date_matches(start, end, settings) and not months.intersection({1, 12}):
            continue

        vehicles = [
            str(vehicle.get("vehicleClassName", "")).strip()
            for vehicle in list_items(item.get("vehicles"))
            if isinstance(vehicle, dict)
        ]
        vehicles = [vehicle for vehicle in vehicles if vehicle]
        capacities = [
            EL_MONTE_CAPACITIES.get(vehicle.lower()) for vehicle in vehicles
        ]
        capacities = [capacity for capacity in capacities if capacity is not None]
        capacity = max(capacities) if capacities else None
        if not capacity_matches(capacity, settings):
            continue
        details = clean_markup(str(item.get("cardSubHeading") or item.get("rate") or ""))
        output.append(
            Deal(
                source="El Monte RV",
                origin=", ".join(matching_origins),
                destination=", ".join(matching_destinations),
                start_date=start,
                end_date=end,
                vehicle=", ".join(vehicles) or "Ukendt RV-type",
                capacity=capacity,
                url=urljoin(EL_MONTE_URL, slug),
                details=details,
            )
        )
    return output


def attr(block: str, name: str) -> str:
    match = re.search(rf'{re.escape(name)}="([^"]*)"', block, re.DOTALL)
    return clean_markup(match.group(1)) if match else ""


def parse_cruise_america(page: str, settings: Settings) -> list[Deal]:
    if "One Way Specials" not in page:
        raise ValueError("Cruise America-siden havde et uventet format")
    deals: list[Deal] = []
    for block in re.findall(
        r'<div class="deals--list--row grid".*?</button>\s*</div>\s*</div>',
        page,
        re.DOTALL,
    ):
        origin = attr(block, "data-deal-field-depart-from")
        destination = attr(block, "data-deal-field-return-to")
        if not location_matches(origin, settings.origin_terms):
            continue
        if not location_matches(destination, settings.destination_terms):
            continue
        start = attr(block, "data-deal-field-depart-beginning") or None
        end = attr(block, "data-deal-field-return-by") or None
        if not date_matches(start, end, settings):
            continue
        capacity_raw = attr(block, "data-rv-thumb-capacity")
        capacity = int(capacity_raw) if capacity_raw.isdigit() else None
        if not capacity_matches(capacity, settings):
            continue
        vehicle = attr(block, "data-rv-thumb-title") or attr(block, "data-deal-field-vehicle-size")
        discount = attr(block, "data-deal-field-discount")
        deals.append(
            Deal(
                source="Cruise America",
                origin=origin,
                destination=destination,
                start_date=start,
                end_date=end,
                vehicle=vehicle or "Ukendt RV-type",
                capacity=capacity,
                url=CRUISE_AMERICA_URL,
                details=(discount + " rabat") if discount else "",
            )
        )
    return deals


def load_state(path: Path) -> tuple[dict[str, Any], bool]:
    if not path.exists():
        return {"seen": {}, "errors": {}}, True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("seen", {})
        data.setdefault("errors", {})
        return data, False
    except (json.JSONDecodeError, OSError):
        return {"seen": {}, "errors": {}}, True


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def email_configured() -> bool:
    required = ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO")
    return all(os.getenv(name) for name in required)


def send_email(subject: str, text_body: str, html_body: str) -> None:
    host = os.environ["SMTP_HOST"]
    port = int(os.getenv("SMTP_PORT", "465"))
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASSWORD"]
    sender = os.getenv("EMAIL_FROM", user)
    recipients = [address.strip() for address in os.environ["EMAIL_TO"].split(",") if address.strip()]

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    context = ssl.create_default_context()
    if env_bool("SMTP_USE_SSL", port == 465):
        with smtplib.SMTP_SSL(host, port, timeout=30, context=context) as server:
            server.login(user, password)
            server.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.starttls(context=context)
            server.login(user, password)
            server.send_message(message)


def deal_email(deals: list[Deal], settings: Settings) -> tuple[str, str, str]:
    subject = f"{len(deals)} nyt LA→NY relocation-tilbud"
    if len(deals) != 1:
        subject = f"{len(deals)} nye LA→NY relocation-tilbud"
    text_lines = [
        f"Fundet {len(deals)} nyt/nye match for {settings.target_start}–{settings.target_end}.",
        "",
    ]
    html_items: list[str] = []
    for deal in deals:
        capacity = f"{deal.capacity} personer" if deal.capacity else "kapacitet skal bekræftes"
        period = " – ".join(part for part in (deal.start_date, deal.end_date) if part) or "dato skal bekræftes"
        text_lines.extend(
            [
                f"{deal.source}: {deal.origin} → {deal.destination}",
                f"Dato: {period}",
                f"Køretøj: {deal.vehicle} ({capacity})",
                f"Detaljer: {deal.details}" if deal.details else "",
                deal.url,
                "",
            ]
        )
        html_items.append(
            "<li style='margin-bottom:18px'>"
            f"<strong>{html.escape(deal.source)}: {html.escape(deal.origin)} → {html.escape(deal.destination)}</strong><br>"
            f"Dato: {html.escape(period)}<br>"
            f"Køretøj: {html.escape(deal.vehicle)} ({html.escape(capacity)})<br>"
            + (f"{html.escape(deal.details)}<br>" if deal.details else "")
            + f"<a href='{html.escape(deal.url, quote=True)}'>Åbn tilbuddet</a></li>"
        )
    text_body = "\n".join(line for line in text_lines if line is not None)
    html_body = (
        f"<p>Fundet {len(deals)} nyt/nye match for "
        f"{settings.target_start}–{settings.target_end}.</p><ul>{''.join(html_items)}</ul>"
        "<p>Tilbud kan forsvinde hurtigt; kontrollér pris, kilometer, forsikring og førerkrav før booking.</p>"
    )
    return subject, text_body, html_body


def error_email(errors: dict[str, str]) -> tuple[str, str, str]:
    lines = [f"{source}: {message}" for source, message in sorted(errors.items())]
    subject = "RV-monitoren kunne ikke kontrollere alle kilder"
    text_body = "\n".join(lines)
    html_body = "<p>Følgende kilder fejlede:</p><ul>" + "".join(
        f"<li><strong>{html.escape(source)}</strong>: {html.escape(message)}</li>"
        for source, message in sorted(errors.items())
    ) + "</ul>"
    return subject, text_body, html_body


def run(dry_run: bool = False) -> int:
    settings = load_settings()
    sources: list[tuple[str, str, Callable[[str, Settings], list[Deal]]]] = [
        ("Imoova", IMOOVA_URL, lambda page, cfg: parse_imoova(page, cfg)),
        ("El Monte RV", EL_MONTE_URL, parse_el_monte),
        ("Cruise America", CRUISE_AMERICA_URL, parse_cruise_america),
    ]
    matches: list[Deal] = []
    errors: dict[str, str] = {}
    for name, url, parser in sources:
        try:
            page = fetch(url, settings.request_timeout)
            source_matches = parser(page, settings)
            matches.extend(source_matches)
            print(f"{name}: {len(source_matches)} relevante tilbud")
        except Exception as exc:  # A source failure must not hide deals from the others.
            errors[name] = str(exc)
            print(f"{name}: FEJL: {exc}", file=sys.stderr)

    matches = list({deal.id: deal for deal in matches}.values())
    if dry_run:
        print(json.dumps([asdict(deal) | {"id": deal.id} for deal in matches], ensure_ascii=False, indent=2))
        return 1 if errors else 0

    state, first_run = load_state(settings.state_file)
    new_deals = [deal for deal in matches if deal.id not in state["seen"]]
    if first_run and not settings.notify_existing_on_first_run:
        new_deals = []

    if new_deals:
        if not email_configured():
            print("E-mail er ikke konfigureret; nye tilbud gemmes ikke som set.", file=sys.stderr)
            return 2
        send_email(*deal_email(new_deals, settings))
        print(f"Sendte e-mail med {len(new_deals)} nyt/nye tilbud")

    now = datetime.now(timezone.utc).isoformat()
    for deal in matches:
        state["seen"][deal.id] = {"first_seen": state["seen"].get(deal.id, {}).get("first_seen", now), "deal": asdict(deal)}

    previous_errors = state.get("errors", {})
    error_fingerprint = hashlib.sha256(json.dumps(errors, sort_keys=True).encode()).hexdigest() if errors else ""
    if errors and previous_errors.get("fingerprint") != error_fingerprint and email_configured():
        send_email(*error_email(errors))
        print("Sendte driftsfejl via e-mail")
    state["errors"] = {"fingerprint": error_fingerprint, "details": errors, "updated_at": now}
    state["last_run"] = now
    save_state(settings.state_file, state)
    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Overvåg LA→NY RV relocation-tilbud")
    parser.add_argument("--dry-run", action="store_true", help="Hent og filtrér uden e-mail eller state-ændringer")
    args = parser.parse_args()
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
