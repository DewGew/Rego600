# REGO600 / REGO635 MQTT Bridge

This project connects a **Rego 600 / Rego 635** heat pump controller to **Home Assistant** via **MQTT**. The script communicates with the heat pump over a serial connection and uses **Home Assistant MQTT Discovery** to automatically create sensors, binary sensors, buttons, and settings. This setup uses custom-built hardware as described at [rago600.sourceforge.io](https://rago600.sourceforge.io/).


---

## Features

* 📡 Serial communication with Rego 600/635
* 🌡 Temperature and status sensors (GT1–GT11, etc.)
* 🔘 Binary sensors (pumps, compressor, auxiliary heat, alarm)
* 🖥 Real-time display row reading
* 🎛 Control buttons, wheel, and settings from Home Assistant
* ⚡ Calculation of instantaneous power (W)
* 🔋 Accumulated energy (kWh) with disk storage
* 🧠 Dynamic mapping depending on pump size (PUMP_SIZE_KW)
* 🔄 Stable MQTT availability with heartbeat and Last Will
* 🧰 Designed to run as a systemd service

---

## File Structure

```
rego600/
  rego600_MQTT.py      # Main script
  rego600_config.py    # User-specific configuration
  energy_total.json    # Saved energy data (automatically created)
  README.md            # Documentation
```

---

## Configuration (`rego600_config.py`)

All user-specific configuration is done in `rego600_config.py`.

### Serial Port

```python
SERIAL_PORT = '/dev/ttyUSB0'
```

Examples:

* `/dev/ttyUSB0` – USB–RS485 adapter
* `/dev/ttyAMA0` – UART via GPIO (Raspberry Pi)

---

### MQTT Settings

```python
MQTT_BROKER = '192.168.1.24'
MQTT_PORT = 1883
MQTT_TOPIC_PREFIX = 'rego600'
MQTT_USER = 'mqttuser'
MQTT_PASSW = 'password'
```

All entities are published under:

```
rego600/
```

---

### Pump Size

```python
PUMP_SIZE_KW = 5 # Choose betwwen 4, 5, 7, 9 , 14 or 16kw
```

Used for:

* Power and energy calculations
* Correct naming of auxiliary heat

| PUMP_SIZE_KW | Auxiliary Heat |
| -----------: | -------------- |
|     ≤ 9 kW   |   3 + 6 kW     |
|  14 / 16 kW  |   5 + 10 kW    |

---

## MQTT Availability

All entities share the same availability topic:

```
rego600/availability
```

Behavior:

* `online` is published at startup and periodically (heartbeat)
* `offline` is automatically published via MQTT Last Will if the script stops
* Availability is reset to `online` after reconnect

This ensures Home Assistant only shows *unavailable* on actual failure.

---

## Installation as a systemd Service (Raspberry Pi)

Example service file (`/etc/systemd/system/rego600.service`):

```ini
[Unit]
Description=REGO600 Monitor Script
After=network.target

[Service]
ExecStart=/usr/bin/python3 /home/pi/rego600/rego600_MQTT.py
WorkingDirectory=/home/pi/rego600
Restart=always
RestartSec=5
User=dietpi
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Enable the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable rego600.service
sudo systemctl start rego600.service
```

---

## Logs & Troubleshooting

Check status:

```bash
sudo systemctl status rego600.service
```

Follow logs live:

```bash
sudo journalctl -u rego600.service -f
```

Common issues to look for:

* `Serial error` → communication problem
* `MQTT disconnected` → network/broker issue
* Repeated restarts → unstable serial connection

---

## Heat Curve Tips (IVT / Rego)

IVT's heat curve is basically linear, which often results in:

* Too cold during mild weather
* Too warm during extreme cold

Recommended method:

1. Adjust the heat curve so that the desired indoor temperature is reached at approximately **0 °C outside**
2. Increase **Fine Adjustment (menu 1.2)** by 1–2 °C if warmer indoor temperature is desired
3. Break the curve in **menu 1.7**:

   * +10 °C / +15 °C: +1 °C
   * −20 °C: −4 °C
   * Adjust other negative temperatures linearly

This provides a more consistent indoor temperature year-round.

---

## Version & Further Development

* Version number is set in `rego600_MQTT.py`
* Script is designed for long-term stable operation
* Can be extended with additional registers, sensors, and controls as needed

---

## License / Usage

Free to use and modify for personal use. No warranty provided – use at your own risk.
