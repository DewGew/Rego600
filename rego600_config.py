"""
rego600_config.py
=================

Common configuration file for the Rego600 / Rego635 MQTT bridge.

This file contains all user-specific settings such as:
- Serial port to the heat pump
- MQTT broker and login credentials
- MQTT topic prefix
- Heat pump size (used for power and energy calculations)

Adjust this file according to your installation.
"""

# --------------------------------------------------
# 🔌 Serial communication
# --------------------------------------------------
# Specify which serial port the Rego600 is connected to.
# Examples:
#   /dev/ttyUSB0  (USB-RS485 adapter)
#   /dev/ttyAMA0 (GPIO UART on Raspberry Pi)
SERIAL_PORT = '/dev/ttyUSB0'

# --------------------------------------------------
# 🌐 MQTT settings
# --------------------------------------------------
# Address of the MQTT broker (e.g., Home Assistant / Mosquitto)
MQTT_BROKER = '192.168.1.24'

# MQTT port (default 1883)
MQTT_PORT = 1883

# Topic prefix for all published entities
# Example: rego600/sensor/Outdoor_GT2
MQTT_TOPIC_PREFIX = 'rego600'

# MQTT username (if authentication is used)
MQTT_USER = 'mqttuser'
MQTT_PASSW = 'mqttpassword'

# --------------------------------------------------
# ⚙️ Heat pump size
# --------------------------------------------------
# Nominal power of the pump in kW.
# Choose between 4, 5, 7, 9, 14, or 16 kW
#
# Used for:
# - Power calculation (W)
# - Energy calculation (kWh)
# - Correct labeling of auxiliary heating (3/6 kW or 5/10 kW)
#
# Examples:
#   5   → smaller models
#   14  → auxiliary heating 5 + 10 kW
#   16  → auxiliary heating 5 + 10 kW
PUMP_SIZE_KW = 5

# --------------------------------------------------
# ℹ️ Tips on the heating curve (source: Heat Pump Forum)
# --------------------------------------------------
#
# IVT's default heating curve is a straight line.
# This means that:
# - At +10 °C outside, it is often too cold inside
# - In severe cold, it is often too warm inside
#
# Recommended method:
#
# 1. Set the correct heating curve so that the house reaches the desired
#    indoor temperature at around 0 °C outside.
#
# 2. If you want, for example, +22 °C indoors:
#    Increase the "Fine adjustment" (menu 1.2) by 1–2 degrees.
#
# 3. Break the curve in menu 1.7:
#    - Increase the value at +10 °C and +15 °C by about +1 °C
#    - Decrease the value at −20 °C by about −4 °C
#    - Adjust other negative temperatures linearly
#      (e.g., −10 °C → −2 °C)
#
# If heating curve 3 is suitable for your house, these values
# should provide a more even and comfortable indoor temperature.
#
