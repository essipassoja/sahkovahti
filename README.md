# Sähkövahti — home electricity & spot price dashboard

A small local web app that shows your household's electricity use next to the Finnish
15‑minute spot prices, and what it actually costs you.

- **Front page for phone / tablet**: a green / yellow / red traffic light telling whether now is a good
  time for big loads, plus price now and power now. Thresholds are set in `[traffic_light]` in `config.toml`.
  The detailed dashboard opens from the menu (or go to `/details`)
- **Live**: power now, price now (spot + your margin, transfer and tax), €/h right now, per‑phase current
- **Day view**: spot price and your consumption/cost for every 15‑minute slot, side by side
- **Cheapest upcoming 1/2/3/4‑hour windows** — for laundry, sauna, dishwasher, EV charging
- **30‑day history**, month‑to‑date cost, and how much your timing saved vs. flat consumption
- Works in light & dark mode, on phone and desktop, from any device on your home network

It runs with **only Python 3.11+ — no packages to install** (pyserial is needed only if the HAN
cable plugs straight into the computer running the app; on **Windows** also run
`pip install tzdata`, since Windows has no built-in time zone database). Until your HAN hardware is installed,
it runs on a realistic **simulated house** with **real prices**.

Done using Claude.

## Quick start

```bash
cd sahkovahti
python3 -m app
```

Open <http://localhost:8080> (or `http://<computer-ip>:8080` from your phone).
On first start the simulator generates 14 days of fake history so the charts have something to show.

## Configure your tariff

Open **menu → Settings** (`/settings`) and enter the numbers from your contract and invoices
**without VAT** ("alv 0 %"). VAT (25.5 % by default) is added on top of everything. A live breakdown shows what you pay right now, so you can check it against your bill.

| Setting | Where to find it |
|---|---|
| Margin | your spot contract's margin ("marginaali") |
| Spot price discount | e.g. "−30 % pörssisähkön hinnasta"; optional end date for intro offers |
| Negative prices billed as 0 | your contract terms; the discount then applies only to positive prices |
| Transfer product | flat (yleissiirto), day/night (aikasiirto) or seasonal (kausisiirto: Nov–Mar, Mon–Sat daytime) |
| Transfer prices | grid company invoice, energy part of transfer ("siirtomaksu") |
| Electricity tax | grid invoice ("sähkövero") — the rate changes with state budgets, check it |
| VAT | 25.5 % in Finland; applied to spot, margin, transfer, electricity tax and basic fees |
| Basic fees | "perusmaksu" on both invoices |

Settings are stored in the app's database and override `[tariff]` / `[traffic_light]` in `config.toml`,
which act as defaults ("Reset to config.toml" on the settings page goes back to them). The settings
page has no password, so anyone on your home network can change it; it holds no personal data.

Prices come from [porssisahko.net](https://porssisahko.net) (Nord Pool day‑ahead, 15‑min slots since
1 Oct 2025, VAT 25.5 % included). Tomorrow's prices are published around 14:00; the app polls
more often in the afternoon until they appear. Price history accumulates from the day you start the
app, so costs for days before that show as "no prices stored".

## Use it on your phone or tablet

With the app running, open `http://<this computer's IP>:8080` on a phone on the same Wi‑Fi (find the IP
with `ipconfig` on Windows or `hostname -I` on Linux). Allow Python through the firewall on private
networks if the page doesn't load.

**Install it like an app:** iPhone/iPad: Share → *Add to Home Screen*. Android: ⋮ → *Add to Home screen*
(full "Install app" with offline support needs HTTPS, e.g. via [Tailscale](https://tailscale.com) — which is
also the safe way to use the app away from home; don't open the port to the internet, there's no password).

**Notifications:** menu → Settings → *Phone notifications*. Install the free [ntfy](https://ntfy.sh) app,
subscribe to the topic shown, press *Send test notification*, then switch notifications on and save. You get a
message when the light turns green (and optionally red), skipping short periods and quiet hours.

## When the HAN hardware arrives

1. **Ask your grid company to open the HAN port** (often free; some do it remotely, e.g. via their
   web service). Until it is enabled, the port sends nothing.
2. Connect a reader to the RJ12 HAN port. Two common options:
   - **Wi‑Fi reader (ESP32/ESP8266)** in the meter cabinet — the easiest, no cable to your computer.
     Use one that publishes over **MQTT** or exposes the **raw telegram over TCP**.
   - **USB P1/HAN cable** to a Raspberry Pi in or near the cabinet.
   The HAN port gives 5 V but very little current — most ESP32 readers need their own power supply.
3. **Check what the meter sends** before switching the dashboard over:
   ```bash
   python3 -m app.sniff --serial /dev/ttyUSB0      # USB cable (pip install pyserial)
   python3 -m app.sniff --tcp 192.168.1.50:23      # Wi-Fi reader in raw TCP mode
   ```
   You should see a block of lines like `1-0:1.8.0(00012345.678*kWh)` with `CRC: OK` every 1–10 s.
4. Set `[meter] source` in `config.toml` to `p1_serial`, `p1_tcp` or `mqtt`, fill in that section,
   and restart. The yellow "simulated" banner disappears and the status badge turns green.
5. Delete `data/energy.db` first if you want to drop the simulated history.

### If your meter sends binary
Most Finnish HAN meters send the ASCII P1 telegram this app parses (same format as in Sweden and
Denmark). If `app.sniff` shows only gibberish/hex, your meter sends binary DLMS/COSEM frames. In that
case use an ESP32 reader firmware that decodes DLMS (e.g. AmsToMqttBridge / "amsreader") and set
`source = "mqtt"` with `payload = "json"`, mapping its field names under `[meter.mqtt.json_fields]`.
MQTT needs `pip install paho-mqtt`.

## Run it permanently (Raspberry Pi / Linux)

```bash
sudo cp sahkovahti.service /etc/systemd/system/
sudo nano /etc/systemd/system/sahkovahti.service   # check User and WorkingDirectory
sudo systemctl enable --now sahkovahti
journalctl -u sahkovahti -f                          # logs
```

For the USB cable, add the user to the `dialout` group: `sudo usermod -aG dialout $USER`.

## How cost is calculated

The meter's cumulative counter (`1.8.0`, kWh) is the source of truth. For each 15‑minute slot the
counter value at the slot boundaries is interpolated from the nearest readings, so energy lands in the
correct slot even when readings arrive irregularly; gaps over an hour show as missing rather than
guessed. Cost per slot = kWh × (spot + margin + transfer + tax). Data is stored in SQLite
(`data/energy.db`, about 600 MB per year at the default 10‑second storage interval, or ~100 MB
with `store_interval_s = 60` — the 15‑minute figures are equally accurate either way, since they
come from the counter).

## API

All JSON, handy for Home Assistant or your own scripts:

| Endpoint | Returns |
|---|---|
| `GET /api/live` | power, phases, price now/next, €/h, today's summary |
| `GET /api/day?date=YYYY-MM-DD` | 15‑min slots: spot, total price, kWh, cost |
| `GET /api/history?days=30` | daily totals + month to date |
| `GET /api/power?minutes=180` | power time series |
| `GET /api/cheapest` | cheapest upcoming 1–4 h windows |
| `GET /api/status` | meter/price status, tariff |
| `POST /api/prices/refresh` | fetch prices now |
| `GET /api/settings` | current tariff & traffic light settings, price breakdown now |
| `POST /api/settings` | save settings (JSON body `{"tariff": {...}, "traffic_light": {...}}`, `Content-Type: application/json`) |
| `POST /api/settings/test-notification` | send a test message with the given notification settings |

## Project layout

```
app/
  __main__.py      start-up
  config.py        config.toml loading, tariff maths
  db.py            SQLite storage, per-slot energy from the meter counter
  prices.py        spot price fetching
  analysis.py      day/history summaries, cheapest windows
  server.py        HTTP server + JSON API (standard library)
  sniff.py         hardware check tool
  meter/p1.py      HAN/P1 telegram parser with CRC check
  meter/sources.py simulator, serial, TCP, MQTT sources
  static/          dashboard (HTML/CSS/JS, no external libraries — works offline)
tests/             python3 -m unittest discover -s tests
```
