# LA→NY RV relocation-monitor

Denne bot kontrollerer én gang i timen:

- Imoova – den konkrete Los Angeles → New York-rute
- El Monte RV – tilbud mærket “One-Way”
- Cruise America – “One Way Specials”

Den filtrerer som standard på afgang/tilgængelighed mellem **1. december 2026 og 31. januar 2027** og køretøjer til mindst **3 personer**. Los Angeles-omegn og New York/New Jersey-omegn er inkluderet. Den sender kun e-mail, når et match ikke tidligere er set. Hvis et site ikke oplyser kapaciteten, sendes tilbuddet med teksten “kapacitet skal bekræftes”, så et muligt match ikke overses.

## Anbefalet drift: GitHub Actions

Det er gratis for et lille offentligt repository og kører, selv når din computer er slukket.

1. Opret et nyt repository på GitHub.
2. Upload hele denne mappe, inklusive `.github`.
3. Slå totrinsbekræftelse til på Google-kontoen, og opret en **app-adgangskode** under Google-konto → Sikkerhed → App passwords. Brug ikke din normale adgangskode.
4. Åbn repository → **Settings → Secrets and variables → Actions → New repository secret**.
5. Opret disse tre secrets:

   - `SMTP_USER`: Gmail-adressen, der skal sende mailen
   - `SMTP_PASSWORD`: Googles 16-tegns app-adgangskode
   - `EMAIL_TO`: adressen, der skal modtage fund

6. Åbn **Actions → Hourly RV relocation monitor → Run workflow** for en manuel første test.

Workflowen kører derefter omkring minut 17 hver time. GitHub kan forsinke planlagte kørsler nogle minutter. Historikken gemmes i GitHubs cache, så det samme tilbud ikke sendes igen.

Bemærk: GitHub deaktiverer planlagte workflows i offentlige repositories efter 60 dage uden repository-aktivitet. Hvis det sker, kan workflowen aktiveres igen under **Actions**.

## Lokal test

Botten bruger kun Pythons standardbibliotek (Python 3.11+):

```bash
python3 monitor.py --dry-run
python3 -m unittest -v
```

`--dry-run` sender ingen mail og ændrer ikke historikken.

Hvis du vil køre lokalt, kopierer du `.env.example` til `.env`, udfylder værdierne og eksporterer dem i dit shell-miljø før `python3 monitor.py`. Læg derefter kommandoen i cron eller macOS launchd én gang i timen.

## Filtre

Miljøvariablerne kan ændres uden kodeændringer:

- `TARGET_START` og `TARGET_END`: inklusive datovindue
- `MIN_CAPACITY`: mindste kendte kapacitet; standard er 3
- `ORIGIN_TERMS`: kommaseparerede navne omkring LA
- `DESTINATION_TERMS`: kommaseparerede navne omkring NY/NJ
- `NOTIFY_EXISTING_ON_FIRST_RUN`: `true` sender også relevante tilbud, der allerede findes ved første kørsel

## Imoovas egen venteliste

Ventelisten findes og er separat fra botten:

1. Gå til <https://www.imoova.com/waitlists>.
2. Vælg **Create account**. Indtast fuldt navn, e-mail og en adgangskode på mindst 8 tegn, eller vælg Google-login.
3. Log ind, og opret en waitlist med **Los Angeles** som afgang og **New York** som destination.
4. Vælg 3–4 rejsende og det ønskede datovindue, hvis Imoova viser disse filtre i din konto, og gem ventelisten.

Imoova beskriver funktionen som øjeblikkelige notifikationer, når tilbud bliver tilgængelige i valgte steder. På en ruteside uden aktuelle tilbud vises også knappen **Join the waitlist**. Den almindelige newsletter nederst på siden er kun ugentlige regionale opdateringer og er ikke det samme som ruteventelisten.

## Vigtige begrænsninger

- Websites kan ændre struktur. Botten sender en driftsfejl én gang pr. ny fejltype og prøver igen næste time.
- Et tilbud kan være udsolgt, når du åbner det. Cruise America oplyser, at deres one-way-specials bookes via en medarbejder, ikke som almindelig online self-service.
- Kontrollér altid samlet pris, kilometer, brændstof, forsikring, alderskrav, antal sæder og sovepladser før booking.
