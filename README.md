# REGO600 / REGO635 MQTT Bridge

Detta projekt kopplar en **Rego 600 / Rego 635** värmepumpsstyrning till **Home Assistant** via **MQTT**.
Scriptet kommunicerar med värmepumpen över seriell anslutning och använder **Home Assistant MQTT Discovery** för automatisk skapande av sensorer, binära sensorer, knappar och inställningar.

---

## Funktioner

* 📡 Seriell kommunikation med Rego 600/635
* 🌡 Temperatur- och statusgivare (GT1–GT11 m.fl.)
* 🔘 Binära sensorer (pumpar, kompressor, tillsatsvärme, larm)
* 🖥 Realtidsavläsning av displayrader
* 🎛 Styrning av knappar, ratt och inställningar från Home Assistant
* ⚡ Beräkning av momentan effekt (W)
* 🔋 Ackumulerad energi (kWh) med lagring till disk
* 🧠 Dynamisk mappning beroende på pumpstorlek (PUMP_SIZE_KW)
* 🔄 Stabil MQTT availability med heartbeat och Last Will
* 🧰 Avsedd att köras som systemd-tjänst

---

## Filstruktur

```
rego600_MQTT.py      # Huvudscript
rego600_config.py   # Användarspecifik konfiguration
energy_total.json   # Sparad energidata (skapas automatiskt)
README.md            # Dokumentation
```

---

## Konfiguration (`rego600_config.py`)

All användarspecifik konfiguration görs i `rego600_config.py`.

### Seriell port

```python
SERIAL_PORT = '/dev/ttyUSB0'
```

Exempel:

* `/dev/ttyUSB0` – USB–RS485-adapter
* `/dev/ttyAMA0` – UART via GPIO (Raspberry Pi)

---

### MQTT-inställningar

```python
MQTT_BROKER = '192.168.1.24'
MQTT_PORT = 1883
MQTT_TOPIC_PREFIX = 'rego600'
MQTT_USER = 'mqttuser'
MQTT_PASSW = 'password'
```

Alla entiteter publiceras under:

```
rego600/
```

---

### Pumpstorlek

```python
PUMP_SIZE_KW = 5
```

Används för:

* Effekt- och energiberäkning
* Korrekt namn på tillsatsvärme

| PUMP_SIZE_KW | Tillsatsvärme |
| -----------: | ------------- |
|       ≤ 9 kW | 3 + 6 kW      |
|   14 / 16 kW | 5 + 10 kW     |

---

## MQTT Availability

Alla entiteter delar samma availability-topic:

```
rego600/availability
```

Beteende:

* `online` publiceras vid start och regelbundet (heartbeat)
* `offline` publiceras automatiskt via MQTT Last Will om scriptet dör
* Vid reconnect återställs availability till `online`

Detta säkerställer att Home Assistant endast visar *unavailable* vid verkligt fel.

---

## Installation som systemd-tjänst (Raspberry Pi)

Exempel på service-fil (`/etc/systemd/system/rego600.service`):

```ini
[Unit]
Description=REGO600 Monitor Script
After=network.target

[Service]
ExecStart=/usr/bin/python3 /home/pi/rego600/rego600_MQTT.py
WorkingDirectory=/home/pi/rego600
Restart=always
RestartSec=5
User=pi
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Aktivera tjänsten:

```bash
sudo systemctl daemon-reload
sudo systemctl enable rego600.service
sudo systemctl start rego600.service
```

---

## Loggar & felsökning

Visa status:

```bash
sudo systemctl status rego600.service
```

Följ loggar live:

```bash
sudo journalctl -u rego600.service -f
```

Vanliga saker att leta efter:

* `Serial error` → kommunikationsproblem
* `MQTT disconnected` → nätverk/broker
* Upprepade restarts → instabil seriell anslutning

---

## Tips om värmekurva (IVT / Rego)

IVT:s reglerkurva är i grunden linjär, vilket ofta ger:

* För kallt vid milt väder
* För varmt vid sträng kyla

Rekommenderad metod:

1. Justera värmekurvan så att rätt innetemperatur nås vid ca **0 °C ute**
2. Höj **Finjustering (meny 1.2)** med 1–2 °C om du vill ha varmare inne
3. Knäck kurvan i **meny 1.7**:

   * +10 °C / +15 °C: +1 °C
   * −20 °C: −4 °C
   * Justera övriga minusgrader linjärt

Detta ger jämnare innetemperatur över hela året.

---

## Version & vidareutveckling

* Versionsnummer sätts i `rego600_MQTT.py`
* Scriptet är anpassat för långtidstabil drift
* Kan utökas med fler register, sensorer och styrningar vid behov

---

## Licens / Användning

Fritt att använda och anpassa för privat bruk.
Ingen garanti lämnas – använd på egen risk.

