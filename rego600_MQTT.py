"""
REGO600 / REGO635 MQTT Bridge
============================

This script communicates with a Rego 600/635 heat pump controller via serial
communication and publishes sensors, binary sensors, settings, and status
to Home Assistant via MQTT (auto-discovery).

Features:
---------
- Reads temperatures, pumps, compressor, auxiliary heating, and alarms
- Publishes display lines in real time
- Allows control of buttons, wheel, and settings via Home Assistant
- Calculates instantaneous power (W) and accumulated energy (kWh)
- Supports different pump sizes (PUMP_SIZE_KW)
- Dynamic register and power mapping depending on pump model
- Runs as a systemd service (rego600.service)

MQTT & Availability:
-------------------
All entities use a common availability topic:

    <MQTT_TOPIC_PREFIX>/availability

The script:
- Publishes "online" at startup and periodically (heartbeat)
- Uses MQTT Last Will ("offline") if the script crashes or loses MQTT
- Restores "online" on MQTT reconnect

This ensures that Home Assistant only shows entities as "unavailable"
in case of real communication failure or if the service is not running.

Serial Communication:
-------------------
- Port and settings are defined in rego600_config.py
- All serial access is locked with threading.Lock for thread safety
- Display is read in a separate thread for fast updates

Execution:
----------
The script is intended to run as a systemd service:

    sudo systemctl start rego600.service
    sudo systemctl status rego600.service
    sudo journalctl -u rego600.service -f

Version:
--------
Version is set via the VERSION constant in the script.

Author / Customizations:
------------------------
Adapted for Home Assistant MQTT Discovery and long-term stable operation
on e.g., DietPi / Raspberry Pi.
"""

import serial
import time
import json
import logging
import paho.mqtt.client as mqtt
from typing import Callable, Optional
import threading
import json
import os
from rego600_config import (
    SERIAL_PORT,
    MQTT_BROKER,
    MQTT_PORT,
    MQTT_TOPIC_PREFIX,
    MQTT_USER,
    MQTT_PASSW,
    PUMP_SIZE_KW,
)

VERSION = "26.01"

# --- Logging setup ---
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
logging.info(f"Starting Rego600-635 MQTT bridge - Version {VERSION}")

BAUDRATE = 19200
TIMEOUT = 1
ENERGY_FILE = "energy_total.json"

# --- Power values for pumps and heaters (depending on pump size) ---
POWER_VALUES = {
    "compressor": 1500,   # watts 5kw pump
    "add_heat_3kw": 3000,
    "add_heat_6kw": 6000,
    "pump_p1": 55,
    "pump_p2": 46,
    "pump_p3": 106,
}

# --- Adjustments depending on PUMP_SIZE_KW ---
if int(PUMP_SIZE_KW) == 4:
    POWER_VALUES.update({
        "compressor": 1100,
        "pump_p1": 0,
        "pump_p2": 35,
        "pump_p3": 70,
    })
if int(PUMP_SIZE_KW) == 7:
    POWER_VALUES.update({
        "compressor": 1850,
    })
if int(PUMP_SIZE_KW) == 9:
    POWER_VALUES.update({
        "compressor": 2500,
    })
if int(PUMP_SIZE_KW) == 11:
    POWER_VALUES.update({
        "compressor": 4600,
    })
if int(PUMP_SIZE_KW) == 14:
    POWER_VALUES.update({
        "compressor": 4100,
        "add_heat_3kw": 5250,
        "add_heat_6kw": 10500,
    })
if int(PUMP_SIZE_KW) == 16:
    POWER_VALUES.update({
        "compressor": 4600,
        "add_heat_3kw": 5250,
        "add_heat_6kw": 10500,
        "pump_p1": 90,
        "pump_p2": 165,
    })

# --- Serial communication constants ---    
PUMP_ADDRESS = 0x81
PC_ADDRESS = 0x01

READ_FRONT_PANEL = 0x00
WRITE_FRONT_PANEL = 0x01
READ_SYSTEM_REGISTER = 0x02
WRITE_SYSTEM_REGISTER = 0x03
READ_TIMER_REGISTER = 0x04
WRITE_TIMER_REGISTER = 0x05
READ_DISPLAY = 0x20

# --- Sensor mappings ---
SENSOR_MAP = {
    'Radiator Return GT1': 0x0209,
    'Radiator Target GT1': 0x006E,
    'Outdoor GT2': 0x020A,
    'Hot Water GT3': 0x020B,
    'Hot Water Target GT3': 0x002B,
    'Forward Target GT4': 0x006D,
    'Room GT5': 0x020D,
    'Compressor GT6': 0x020E,
    'Heat fluid out GT8': 0x020F,
    'Heat fluid in GT9': 0x0210,
    'Cold fluid in GT10': 0x0211,
    'Cold fluid out GT11': 0x0212,
    'GT3 On': 0x0073,
    'GT3 Off': 0x0074,
    # 'External hot water GT3x': 0x0213
}

# --- Binary sensor mappings ---
BINARY_SENSOR_MAP = {
    'Three-way Valve': 0x0205,
    'Add Heat Percentage': 0x006C,
    'Radiator Pump P1': 0x0203,
    'Heat carrier pump P2': 0x0204,
    'Ground loop pump P3': 0x01FD,
    'Compressor': 0x01FE,
    'Alarm': 0x0206,
}

# --- Dynamic add-heat mapping depending on pump size ---
if int(PUMP_SIZE_KW) in (14, 16):
    BINARY_SENSOR_MAP.update({
        'Add heat 5kw': 0x01FF,
        'Add heat 10kw': 0x0200,
    })
else:
    BINARY_SENSOR_MAP.update({
        'Add heat 3kw': 0x01FF,
        'Add heat 6kw': 0x0200,
    })

# --- Settings register mapping ---
SETTINGS_MAP = {
    'Heat curve': 0x0000,
    'Heat curve fine adj.': 0x0001,
    'Indoor temp setting': 0x0021,
    'Curve infl. by in-temp.': 0x0022,
    'Heat curve coupling diff.': 0x0002,
    'Adjust curve at +20° out': 0x001E,
    'Adjust curve at +15° out': 0x001C,
    'Adjust curve at +10° out': 0x001A,
    'Adjust curve at +5° out': 0x0018,
    'Adjust curve at 0° out': 0x0016,
    'Adjust curve at -5° out': 0x0014,
    'Adjust curve at -10° out': 0x0012,
    'Adjust curve at -15° out': 0x0010,
    'Adjust curve at -20° out': 0x000E,
    'Adjust curve at -25° out': 0x000C,
    'Adjust curve at -30° out': 0x000A,
    'Adjust curve at -35° out': 0x0008,
}

# --- LED mapping ---
LED_MAP = {
    'LED1 Power On': 0x0012,
    'LED2 Pump': 0x0013,
    'LED3 Add Heat': 0x0014,
    'LED4 Boiler': 0x0015,
    'LED5 Alarm': 0x0016
}

# --- Display row mapping ---
DISPLAY_ROWS = {
    'Row 1': 0x0000,
    'Row 2': 0x0001,
    'Row 3': 0x0002,
    'Row 4': 0x0003
}

# --- Keypanel mapping ---
KEYPANEL_MAP = {
    'Key 1': 0x0009,
    'Key 2': 0x000A,
    'Key 3': 0x000B,
    'Wheel': 0x0044,
    # Special wheel values
    'Wheel left': 0xff00,
    'Wheel right': 0xff01
}

# --- Timer mapping ---
TIMER_MAP = {
    'Add heat timer in sec.': 0x0000
}

# --- Thread-safe serial access ---
serial_lock = threading.Lock()

def calculate_checksum(packet: list) -> int:
    checksum = 0
    for byte in packet[2:8]:
        checksum ^= byte
    return checksum

def encode_register(reg_addr: int) -> list:
    return [
        (reg_addr & 0xC000) >> 14,
        (reg_addr & 0x3F80) >> 7,
        (reg_addr & 0x007F)
    ]

def build_request(address: int, command: int, register: int) -> bytes:
    reg_bytes = encode_register(register)
    value = [0x00, 0x00, 0x00]
    packet = [address, command] + reg_bytes + value
    packet.append(calculate_checksum(packet))
    return bytes(packet)

def decode_rego_response(data: bytes) -> int:
    if len(data) < 5:
        raise ValueError("Response too short")
    b1 = data[1] & 0x7F
    b2 = data[2] & 0x7F
    b3 = data[3] & 0x7F
    raw_value = (b1 << 14) | (b2 << 7) | b3
    if raw_value & 0x10000:
        raw_value -= 0x20000
    return raw_value
    
def decode_rego_value(raw):
    # 16-bit signed integer
    if raw > 32767:
        raw = raw - 65536
    return raw / 10

def decode_display_response(data: bytes) -> str:
    if len(data) != 42:
        raise ValueError("Invalid display response length")
    if data[0] != PC_ADDRESS:
        raise ValueError("Unexpected display response address")

    display_text = ""
    for i in range(1, 41, 2):
        high_nibble = data[i] & 0x0F
        low_nibble = data[i + 1] & 0x0F
        char_code = (high_nibble << 4) | low_nibble
        char = chr(char_code)
        if char == 'ÿ': 
            char = ''
        if char == 'ß': 
            char = '°' 
        display_text += char

    return display_text.strip()

    
def validate_response_checksum(response: bytes) -> bool:
    return (response[1] ^ response[2] ^ response[3]) == response[4]

def open_serial_connection() -> Optional[serial.Serial]:
    try:
        ser = serial.Serial(SERIAL_PORT, BAUDRATE, serial.EIGHTBITS, serial.PARITY_NONE, serial.STOPBITS_ONE, timeout=TIMEOUT)
        logging.info(f"Connected to {SERIAL_PORT}")
        time.sleep(1)
        return ser
    except Exception as e:
        logging.error(f"Serial port error: {e}")
        return None

def mqtt_publish(client, topic, payload, retain=True):
    full_topic = f"{MQTT_TOPIC_PREFIX}/{topic}"
    client.publish(full_topic, payload, retain=retain)

def publish_sensor_map_discovery(client):
    
    for name, reg_addr in SENSOR_MAP.items():
        unique_id = f"rego600_{name.replace(' ', '_').lower()}"
        state_topic = f"{MQTT_TOPIC_PREFIX}/sensor/{name.replace(' ', '_')}"  # Default
                
        # Default to sensor (for temperatures, etc.)
        sensor_config = {
            "name": name,
            "state_topic": state_topic,
            "unique_id": unique_id,
            "availability_topic": f"{MQTT_TOPIC_PREFIX}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": {
                "identifiers": ["rego600"],
                "name": "REGO600 Monitor",
                "manufacturer": "IVT/Bosch",
                "model": "Rego600-635",
                "sw_version": VERSION
            },
            "unit_of_measurement": "°C",
            "value_template": "{% set v = value | int %}{% if v > 32767 %}{{ ((v - 65536) / 10) | round(1) }}{% else %}{{ (v / 10) | round(1) }}{% endif %}",
            "state_class": "measurement",
            "icon": "mdi:thermometer"
        }
        discovery_topic = f"homeassistant/sensor/{unique_id}/config"

        client.publish(discovery_topic, json.dumps(sensor_config), retain=True)
        
def publish_binary_sensor_map_discovery(client):
     
    for name, reg_addr in BINARY_SENSOR_MAP.items():
        unique_id = f"rego600_{name.replace(' ', '_').lower()}"
        state_topic = f"{MQTT_TOPIC_PREFIX}/sensor/{name.replace(' ', '_')}"  # Default

        if name == 'Add Heat Percentage':
            sensor_config = {
                "name": name,
                "state_topic": state_topic,
                "unique_id": unique_id,
                "availability_topic": f"{MQTT_TOPIC_PREFIX}/availability",
                "payload_available": "online",
                "payload_not_available": "offline",
                "device": {
                    "identifiers": ["rego600"],
                    "name": "REGO600 Monitor",
                    "manufacturer": "IVT/Bosch",
                    "model": "Rego600-635",
                    "sw_version": VERSION
                },
                "unit_of_measurement": "%",
                "value_template": "{{ (value | float / 10) }}",
                "icon": "mdi:percent",
                "state_class": "measurement",
             }
            discovery_topic = f"homeassistant/sensor/{unique_id}/config"
        else:
            sensor_config = {
                "name": name,
                "state_topic": state_topic, # Change to f"{MQTT_TOPIC_PREFIX}/binary_sensor/{name.replace(' ', '_')}"
                "unique_id": unique_id,
                "availability_topic": f"{MQTT_TOPIC_PREFIX}/availability",
                "payload_available": "online",
                "payload_not_available": "offline",
                "value_template": "{{ 'ON' if value|int > 0 else 'OFF' }}",
                "device": {
                    "identifiers": ["rego600"],
                    "name": "REGO600 Monitor",
                    "manufacturer": "IVT/Bosch",
                    "model": "Rego600-635",
                    "sw_version": VERSION
                }
            }
            discovery_topic = f"homeassistant/binary_sensor/{unique_id}/config"

        client.publish(discovery_topic, json.dumps(sensor_config), retain=True)
        
def publish_led_map_discovery(client):
    
    for name, reg_addr in LED_MAP.items():
        unique_id = f"rego600_{name.replace(' ', '_').lower()}"
        state_topic = f"{MQTT_TOPIC_PREFIX}/led/{name.replace(' ', '_')}"  # Maybe Change to "light"
        
        sensor_config = {
            "name": name,
            "state_topic": state_topic,
            "unique_id": unique_id,
            "availability_topic": f"{MQTT_TOPIC_PREFIX}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
            "value_template": "{{ 'ON' if value|int > 0 else 'OFF' }}",
            "device": {
                "identifiers": ["rego600"],
                "name": "REGO600 Monitor",
                "manufacturer": "IVT/Bosch",
                "model": "Rego600-635",
                "sw_version": VERSION
            }
        }
        discovery_topic = f"homeassistant/binary_sensor/{unique_id}/config" # Maybe Change to "light"

        client.publish(discovery_topic, json.dumps(sensor_config), retain=True)      
    
def publish_display_rows_discovery(client):
    
    for row_name, row_addr in DISPLAY_ROWS.items():
        unique_id = f"rego600_display_{row_name.replace(' ', '_').lower()}"
        state_topic = f"{MQTT_TOPIC_PREFIX}/display/{row_name.replace(' ', '_')}"

        sensor_config = {
            "name": f"Display {row_name}",
            "state_topic": state_topic,
            "unique_id": unique_id,
            "entity_category": "diagnostic",
            "availability_topic": f"{MQTT_TOPIC_PREFIX}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": {
                "identifiers": ["rego600"],
                "name": "REGO600 Monitor",
                "manufacturer": "IVT/Bosch",
                "model": "Rego600-635",
                "sw_version": VERSION
            },
            "icon": "mdi:television",
            "value_template": "{{ value }}"
        }

        discovery_topic = f"homeassistant/sensor/{unique_id}/config"
        client.publish(discovery_topic, json.dumps(sensor_config), retain=True)

def publish_key_controls(client):
    for i in range(1, 4):
        key_topic = f"{MQTT_TOPIC_PREFIX}/set/key/{i}"
        unique_id = f"rego600_key_{i}"
        topic = f"homeassistant/button/rego600_key_{i}/config"
        
        button_config = {
            "name": f"REGO600 Key {i}",
            "command_topic": key_topic,
            "payload_press": "1",
            "unique_id": unique_id,
            "availability_topic": f"{MQTT_TOPIC_PREFIX}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": {
                "identifiers": ["rego600"],
                "name": "REGO600 Monitor",
                "manufacturer": "IVT/Bosch",
                "model": "Rego600-635",
                "sw_version": VERSION
            }
        }
        client.publish(topic, json.dumps(button_config), retain=True)
        
    # Wheel Left
    wheel_left_config = {
        "name": "REGO600 Wheel Left",
        "command_topic": f"{MQTT_TOPIC_PREFIX}/set/key/wheel_left",
        "payload_press": "1",
        "unique_id": "rego600_wheel_left",
        "availability_topic": f"{MQTT_TOPIC_PREFIX}/availability",
        "payload_available": "online",
        "payload_not_available": "offline",
        "device": {
            "identifiers": ["rego600"],
            "name": "REGO600 Monitor",
            "manufacturer": "IVT/Bosch",
            "model": "Rego600-635",
            "sw_version": VERSION
        }
    }
    client.publish("homeassistant/button/rego600_wheel_left/config", json.dumps(wheel_left_config), retain=True)

    # Wheel Right
    wheel_right_config = {
        "name": "REGO600 Wheel Right",
        "command_topic": f"{MQTT_TOPIC_PREFIX}/set/key/wheel_right",
        "payload_press": "1",
        "unique_id": "rego600_wheel_right",
        "availability_topic": f"{MQTT_TOPIC_PREFIX}/availability",
        "payload_available": "online",
        "payload_not_available": "offline",
        "device": {
            "identifiers": ["rego600"],
            "name": "REGO600 Monitor",
            "manufacturer": "IVT/Bosch",
            "model": "Rego600-635",
            "sw_version": VERSION
        }
    }
    client.publish("homeassistant/button/rego600_wheel_right/config", json.dumps(wheel_right_config), retain=True)


def publish_ha_discovery(client):
    def create_number_config(name, key, min_val, max_val, step, unit="°C", icon="mdi:cog"):
        base = {
            "name": name,
            "command_topic": f"{MQTT_TOPIC_PREFIX}/set/setting/{key}",
            "state_topic": f"{MQTT_TOPIC_PREFIX}/setting/{key}",
            "availability_topic": f"{MQTT_TOPIC_PREFIX}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
            "min": min_val,
            "max": max_val,
            "step": step,
            "unit_of_measurement": unit,
            "value_template": "{{ (value | float / 10) | round(1) }}",
            "command_template": "{{ (value * 10) | int }}",
            "unique_id": f"rego600_{key}",
            "device": {
                "identifiers": ["rego600"],
                "name": "REGO600 Monitor",
                "manufacturer": "IVT/Bosch",
                "model": "Rego600-635",
                "sw_version": VERSION
            },
            "icon": icon
        }
        return base

    configs = [
        ("Indoor Temp Setting", "indoor_temp_setting", 10, 30, 0.1),
        ("Heat curve", "heat_curve", 0, 10, 0.1),
        ("Heat curve fine adj.", "heat_curve_fine_adj", -10, 10, 0.1),
        ("Curve infl. by in-temp.", "curve_infl_by_in_temp", -10, 10, 0.1),
        ("Heat curve coupling diff.", "heat_curve_coupling_diff", 0, 15, 1),
        ("Adjust curve at +20° out", "adjust_curve_at_20_out", -10, 10, 0.1),
        ("Adjust curve at +15° out", "adjust_curve_at_15_out", -10, 10, 0.1),
        ("Adjust curve at +10° out", "adjust_curve_at_10_out", -10, 10, 0.1),
        ("Adjust curve at +5° out", "adjust_curve_at_5_out", -10, 10, 0.1),
        ("Adjust curve at 0° out", "adjust_curve_at_0_out", -10, 10, 0.1),
        ("Adjust curve at -5° out", "adjust_curve_at_-5_out", -10, 10, 0.1),
        ("Adjust curve at -10° out", "adjust_curve_at_-10_out", -10, 10, 0.1),
        ("Adjust curve at -15° out", "adjust_curve_at_-15_out", -10, 10, 0.1),
        ("Adjust curve at -20° out", "adjust_curve_at_-20_out", -10, 10, 0.1),
        ("Adjust curve at -25° out", "adjust_curve_at_-25_out", -10, 10, 0.1),
        ("Adjust curve at -30° out", "adjust_curve_at_-30_out", -10, 10, 0.1),
        ("Adjust curve at -35° out", "adjust_curve_at_-35_out", -10, 10, 0.1),
        #("Add heat timer in sec.", "add_heat_timer_in_sec", 0, 1000, 1, "s", "mdi:timer"),
        # Add more if needed
    ]

    for name, key, min_val, max_val, step in configs:
        config = create_number_config(name, key, min_val, max_val, step)
        topic = f"homeassistant/number/rego600_{key}/config"
        client.publish(topic, json.dumps(config), retain=True)

    # Dynamic discovery for all registers in SENSOR_MAP
    publish_sensor_map_discovery(client)
    publish_binary_sensor_map_discovery(client)
    publish_led_map_discovery(client)
    publish_display_rows_discovery(client)
    publish_key_controls(client)

def setup_mqtt(ser):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, userdata={'serial': ser})
    client.username_pw_set(MQTT_USER, MQTT_PASSW)
    
    availability_topic = f"{MQTT_TOPIC_PREFIX}/availability"
    client.will_set(availability_topic, payload="offline", qos=1, retain=True)
    
    client.on_message = on_mqtt_message
    client.on_connect = on_mqtt_connect
    client.on_disconnect = on_mqtt_disconnect

    client.connect(MQTT_BROKER, MQTT_PORT, 60)

    client.subscribe(f"{MQTT_TOPIC_PREFIX}/set/#")
    
    publish_ha_discovery(client)

    client.publish(availability_topic, "online", qos=1, retain=True)
    
    return client
    
def on_mqtt_connect(client, userdata, flags, reason_code, properties):
    logging.info("MQTT connected")
    client.publish(f"{MQTT_TOPIC_PREFIX}/availability", "online", retain=True)

def on_mqtt_disconnect(client, userdata, rc):
    logging.warning(f"MQTT disconnected (rc={rc})")

def on_mqtt_message(client, userdata, msg):
    ser = userdata['serial']
    topic = msg.topic
    payload_raw = msg.payload.decode()
    
    try:
        payload = int(payload_raw)
    except ValueError:
        logging.warning(f"Ignores invalid payload: {payload_raw}")
        return
        
    setting_topics = {
        "indoor_temp_setting": "Indoor temp setting",
        "heat_curve": "Heat curve",
        "heat_curve_fine_adj": "Heat curve fine adj.",
        "curve_infl_by_in_temp": "Curve infl. by in-temp.",
        "heat_curve_coupling_diff": "Heat curve coupling diff.",
        "adjust_curve_at_20_out": "Adjust curve at +20° out",
        "adjust_curve_at_15_out": "Adjust curve at +15° out",
        "adjust_curve_at_10_out": "Adjust curve at +10° out",
        "adjust_curve_at_5_out": "Adjust curve at +5° out",
        "adjust_curve_at_0_out": "Adjust curve at 0° out",
        "adjust_curve_at_-5_out": "Adjust curve at -5° out",
        "adjust_curve_at_-10_out": "Adjust curve at -10° out",
        "adjust_curve_at_-15_out": "Adjust curve at -15° out",
        "adjust_curve_at_-20_out": "Adjust curve at -20° out",
        "adjust_curve_at_-25_out": "Adjust curve at -25° out",
        "adjust_curve_at_-30_out": "Adjust curve at -30° out",
        "adjust_curve_at_-35_out": "Adjust curve at -35° out",
    }
    
    key_topics = {
        "1": "Key 1",
        "2": "Key 2",
        "3": "Key 3",
        "wheel_left": "left",
        "wheel_right": "right",
    }
    
    for key, reg_name in setting_topics.items():
        if topic == f"{MQTT_TOPIC_PREFIX}/set/setting/{key}":
            reg_addr = SETTINGS_MAP[reg_name]
            write_setting(ser, reg_addr, payload)
            logging.info(f"Updated setting {reg_name} → {payload}")
            return
        
    for key, action in key_topics.items():
        if topic == f"{MQTT_TOPIC_PREFIX}/set/key/{key}":
            if "wheel" in key:
                turn_wheel(ser, action)
                logging.info(f"Turned wheel {action}")
            else:
                press_key(ser, KEYPANEL_MAP[action])
                logging.info(f"Pressed {action}")
            return

    logging.warning(f"Unexpected MQTT-topic: {topic}")
        

def read_register(ser: serial.Serial, reg_addr: int, command: int, expected_length: int, decode_func: Callable, delay: float = 0.1):
    """Read a register from the pump with validation and decoding."""
    with serial_lock:
        request = build_request(PUMP_ADDRESS, command, reg_addr)
        ser.write(request)
        time.sleep(delay)
        response = ser.read(expected_length)

    if len(response) != expected_length:
        logging.warning(f"Incomplete response for register {hex(reg_addr)}.")
        return None
    if response[0] != PC_ADDRESS:
        logging.warning(f"Unexpected response address: {hex(response[0])}")
        return None
    if expected_length == 5 and not validate_response_checksum(response):
        logging.warning("Checksum mismatch!")
        return None
    try:
        return decode_func(response)
    except ValueError as e:
        logging.warning(f"Decode error: {e}")
        return None

def read_sensor(ser: serial.Serial, reg_addr: int):
    return read_register(ser, reg_addr, READ_SYSTEM_REGISTER, 5, decode_rego_response)

def read_led_state(ser: serial.Serial, reg_addr: int):
    return read_register(ser, reg_addr, READ_FRONT_PANEL, 5, decode_rego_response)

def read_display_line(ser: serial.Serial, row_addr: int):
    return read_register(ser, row_addr, READ_DISPLAY, 42, decode_display_response)

def read_setting(ser: serial.Serial, reg_addr: int):
    return read_register(ser, reg_addr, READ_SYSTEM_REGISTER, 5, decode_rego_response)
    
# def read_timer(ser: serial.Serial, reg_addr: int):
    # return read_register(ser, reg_addr, READ_TIMER_REGISTER, 5, decode_rego_response)

def write_setting(ser: serial.Serial, reg_addr: int, value: int):
    with serial_lock:
        reg_bytes = encode_register(reg_addr)
        value_bytes = encode_register(value)
        packet = [PUMP_ADDRESS, WRITE_SYSTEM_REGISTER] + reg_bytes + value_bytes
        packet.append(calculate_checksum(packet))
        ser.write(bytes(packet))
        time.sleep(0.1)
        response = ser.read(1)
    return len(response) == 1 and response[0] == PC_ADDRESS

# def write_timer(ser: serial.Serial, reg_addr: int, value: int):
    # reg_bytes = encode_register(reg_addr)
    # value_bytes = encode_register(value)
    # packet = [PUMP_ADDRESS, WRITE_TIMER_REGISTER] + reg_bytes + value_bytes
    # packet.append(calculate_checksum(packet))
    # ser.write(bytes(packet))
    # time.sleep(0.1)
    # response = ser.read(1)
    # return len(response) == 1 and response[0] == PC_ADDRESS
    
def press_key(ser: serial.Serial, reg_addr: int) -> bool:
    with serial_lock:
        reg_bytes = encode_register(reg_addr)
        value_bytes = encode_register(1)  # Skicka "1" = tryck på knapp
        packet = [PUMP_ADDRESS, WRITE_FRONT_PANEL] + reg_bytes + value_bytes
        packet.append(calculate_checksum(packet))
        ser.write(bytes(packet))
        time.sleep(0.1)
        response = ser.read(1)
    return len(response) == 1 and response[0] == PC_ADDRESS
    
def turn_wheel(ser: serial.Serial, direction: str) -> bool:
    reg_bytes = encode_register(KEYPANEL_MAP['Wheel'])
    
    if direction == 'left':
        value = 0x1FFFFF
    elif direction == 'right':
        value = 0x000001
    else:
        logging.error(f"Invalid direction for wheel: {direction}")
        return False

    value_bytes = [
        (value & 0xC0000) >> 18,
        (value & 0x3F800) >> 11,
        (value & 0x007F00) >> 4
    ]
    value_bytes[2] = (value & 0x0000FF) >> 0 & 0x7F

    with serial_lock:
        packet = [PUMP_ADDRESS, WRITE_FRONT_PANEL] + reg_bytes + value_bytes
        packet.append(calculate_checksum(packet))
        ser.write(bytes(packet))
        time.sleep(0.1)
        response = ser.read(1)
    return len(response) == 1 and response[0] == PC_ADDRESS
    
def load_energy_total():
    """Read the latest accumulated energy value from file."""
    try:
        if os.path.exists(ENERGY_FILE):
            with open(ENERGY_FILE, "r") as f:
                data = json.load(f)
                return float(data.get("energy_total_kwh", 0.0))
    except Exception as e:
        logging.warning(f"Could not read {ENERGY_FILE}: {e}")
    return 0.0


def save_energy_total(value: float):
    """Save the current energy value to file."""
    try:
        with open(ENERGY_FILE, "w") as f:
            json.dump({"energy_total_kwh": round(value, 3)}, f)
    except Exception as e:
        logging.warning(f"Could not save {ENERGY_FILE}: {e}")
    
def monitor_loop(interval: float = 15.0, display_interval: float = 1):
    ser = open_serial_connection()
    mqtt_client = setup_mqtt(ser)
    mqtt_client.loop_start()

    last_full_update = 0.0
    last_display = {}

    energy_total_kwh = load_energy_total()
    logging.info(f"Loaded accumulated energy: {energy_total_kwh:.3f} kWh")
    last_energy_update = time.time()
    last_energy_save = time.time()
    
    last_heartbeat = 0
    HEARTBEAT_INTERVAL = 30  # seconds

    def publish_map(map_obj, read_func, topic_prefix):
        for name, reg in map_obj.items():
            try:
                value = read_func(ser, reg)
                if value is not None:
                    topic = f"{topic_prefix}/{name.replace(' ', '_')}"
                    mqtt_publish(mqtt_client, topic, value)
            except serial.SerialException as e:
                logging.error(f"Serial error while reading {name}: {e}")
                break
            except Exception as e:
                logging.warning(f"Error reading {name}: {e}")
                continue

    def publish_power_sensors():
        """Calculate instantaneous power based on active components."""
        try:
            compressor_on = read_sensor(ser, BINARY_SENSOR_MAP['Compressor']) == 1
            add3_on = read_sensor(ser, BINARY_SENSOR_MAP['Add heat 3kw']) == 1
            add6_on = read_sensor(ser, BINARY_SENSOR_MAP['Add heat 6kw']) == 1
            p1_on = read_sensor(ser, BINARY_SENSOR_MAP['Radiator Pump P1']) == 1
            p2_on = read_sensor(ser, BINARY_SENSOR_MAP['Heat carrier pump P2']) == 1
            p3_on = read_sensor(ser, BINARY_SENSOR_MAP['Ground loop pump P3']) == 1

            power_map = {
                "compressor": POWER_VALUES["compressor"] if compressor_on else 0,
                "add_heat_3kw": POWER_VALUES["add_heat_3kw"] if add3_on else 0,
                "add_heat_6kw": POWER_VALUES["add_heat_6kw"] if add6_on else 0,
                "pump_p1": POWER_VALUES["pump_p1"] if p1_on else 0,
                "pump_p2": POWER_VALUES["pump_p2"] if p2_on else 0,
                "pump_p3": POWER_VALUES["pump_p3"] if p3_on else 0,
            }

            total = sum(power_map.values())
            power_map["total"] = total

            for name, value in power_map.items():
                mqtt_publish(mqtt_client, f"power/{name}", value)

            logging.debug(f"Energy sensors updated: total {total} W")
            return total

        except Exception as e:
            logging.warning(f"Error in calculating power: {e}")
            return 0

    def display_monitor():
        """Separate thread: read display lines frequently and publish only on change."""
        nonlocal last_display
        while True:
            try:
                for row_name, row_addr in DISPLAY_ROWS.items():
                    new_value = read_display_line(ser, row_addr)
                    if new_value is None:
                        continue

                    if last_display.get(row_name) != new_value:
                        last_display[row_name] = new_value
                        topic = f"display/{row_name.replace(' ', '_')}"
                        mqtt_publish(mqtt_client, topic, new_value)
                        logging.debug(f"Display changed: {row_name} = {new_value}")

                time.sleep(display_interval)

            except serial.SerialException as e:
                logging.error(f"Display serial error: {e}")
                time.sleep(2.0)
            except Exception as e:
                logging.warning(f"Display monitor error: {e}")
                time.sleep(1.0)

    # --- Start a separate thread for fast display updates ---
    threading.Thread(target=display_monitor, daemon=True).start()
    logging.info(f"Display-monitor started (intervall: {display_interval}s)")

    # --- Create discovery objects for power sensors ---
    for name in list(POWER_VALUES.keys()) + ["total"]:
        unique_id = f"rego600_power_{name}"
        topic = f"homeassistant/sensor/{unique_id}/config"
        sensor_cfg = {
            "name": f"Rego600 {name.replace('_', ' ').title()}",
            "state_topic": f"{MQTT_TOPIC_PREFIX}/power/{name}",
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
            "unique_id": unique_id,
            "availability_topic": f"{MQTT_TOPIC_PREFIX}/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": {
                "identifiers": ["rego600"],
                "name": "REGO600 Monitor",
                "manufacturer": "IVT/Bosch",
                "model": "Rego600-635",
                "sw_version": VERSION
            }
        }
        mqtt_client.publish(topic, json.dumps(sensor_cfg), retain=True)

    # --- Create discovery object for energy sensor (kWh) ---
    unique_id_energy = "rego600_energy_total"
    topic_energy = f"homeassistant/sensor/{unique_id_energy}/config"
    energy_cfg = {
        "name": "Rego600 Total Energy",
        "state_topic": f"{MQTT_TOPIC_PREFIX}/energy/total",
        "unit_of_measurement": "kWh",
        "device_class": "energy",
        "state_class": "total_increasing",
        "unique_id": unique_id_energy,
        "availability_topic": f"{MQTT_TOPIC_PREFIX}/availability",
        "payload_available": "online",
        "payload_not_available": "offline",
        "device": {
            "identifiers": ["rego600"],
            "name": "REGO600 Monitor",
            "manufacturer": "IVT/Bosch",
            "model": "Rego600-635",
            "sw_version": VERSION
        }
    }
    mqtt_client.publish(topic_energy, json.dumps(energy_cfg), retain=True)

    try:
        while True:
            now = time.time()
            # --- MQTT heartbeat (keep availability online) ---
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                try:
                    mqtt_client.publish(
                        f"{MQTT_TOPIC_PREFIX}/availability",
                        "online",
                        retain=True
                    )
                    last_heartbeat = now
                except Exception as e:
                    logging.warning(f"Heartbeat failed: {e}")

            # --- Full update (sensors, LEDs, settings) ---
            if now - last_full_update >= interval:
                publish_map(SENSOR_MAP, read_sensor, "sensor")
                publish_map(BINARY_SENSOR_MAP, read_sensor, "sensor")
                publish_map(LED_MAP, read_led_state, "led")

               # --- Power and energy update ---
                total_power_w = publish_power_sensors()

                now_energy = time.time()
                dt_hours = (now_energy - last_energy_update) / 3600.0
                energy_total_kwh += (total_power_w * dt_hours) / 1000.0
                last_energy_update = now_energy
                # --- Occasionally save to disk (e.g., every 10 minutes) ---
                if now_energy - last_energy_save >= 600:  # Every 10 minute
                    save_energy_total(energy_total_kwh)
                    last_energy_save = now_energy
                    logging.debug(f"Energy saved: {energy_total_kwh:.3f} kWh")
                mqtt_publish(mqtt_client, "energy/total", round(energy_total_kwh, 3))

                # --- Read and publish settings ---
                setting_keys = [
                    ("Indoor temp setting", "indoor_temp_setting"),
                    ("Heat curve", "heat_curve"),
                    ("Heat curve fine adj.", "heat_curve_fine_adj"),
                    ("Curve infl. by in-temp.", "curve_infl_by_in_temp"),
                    ("Heat curve coupling diff.", "heat_curve_coupling_diff"),
                    ("Adjust curve at +20° out", "adjust_curve_at_20_out"),
                    ("Adjust curve at +15° out", "adjust_curve_at_15_out"),
                    ("Adjust curve at +10° out", "adjust_curve_at_10_out"),
                    ("Adjust curve at +5° out", "adjust_curve_at_5_out"),
                    ("Adjust curve at 0° out", "adjust_curve_at_0_out"),
                    ("Adjust curve at -5° out", "adjust_curve_at_-5_out"),
                    ("Adjust curve at -10° out", "adjust_curve_at_-10_out"),
                    ("Adjust curve at -15° out", "adjust_curve_at_-15_out"),
                    ("Adjust curve at -20° out", "adjust_curve_at_-20_out"),
                    ("Adjust curve at -25° out", "adjust_curve_at_-25_out"),
                    ("Adjust curve at -30° out", "adjust_curve_at_-30_out"),
                    ("Adjust curve at -35° out", "adjust_curve_at_-35_out"),
                ]

                for key, topic_key in setting_keys:
                    try:
                        reg = SETTINGS_MAP[key]
                        value = read_setting(ser, reg)
                        mqtt_publish(mqtt_client, f"setting/{topic_key}", value)
                    except serial.SerialException as e:
                        logging.error(f"Serial error while reading setting {key}: {e}")
                        break
                    except Exception as e:
                        logging.error(f"Unexpected error while reading setting {key}: {e}")
                        continue

                last_full_update = now

            time.sleep(0.1)

    except KeyboardInterrupt:
        logging.info("Monitoring stopped by the user.")
    finally:
        try:
            mqtt_client.publish(f"{MQTT_TOPIC_PREFIX}/availability", "offline", qos=1, retain=True)
        except Exception:
            pass
        mqtt_client.loop_stop()
        ser.close()
        logging.info("Closed MQTT and serial connection.")
        save_energy_total(energy_total_kwh)
        logging.info(f"Energy saved at closed: {energy_total_kwh:.3f} kWh")

if __name__ == '__main__':
    monitor_loop()
    

