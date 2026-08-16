#!/usr/bin/env bash
set -euo pipefail

: "${MQTT_HOST:?MQTT_HOST is required}"
export MQTT_HOST
export MQTT_PORT="${MQTT_PORT:-1883}"
export MQTT_USER="${MQTT_USER:-}"
export MQTT_PASS="${MQTT_PASS:-}"
export FAN_MQTT_BASE_TOPIC="${FAN_MQTT_BASE_TOPIC:-home/fans}"
export RTL433_FAN_FREQ="${RTL433_FAN_FREQ:-304250000}"
export RTL433_FAN_RATE="${RTL433_FAN_RATE:-2048000}"
export RTL433_FAN_GAIN="${RTL433_FAN_GAIN:-49.6}"

exec python3 /usr/local/bin/fan_wallswitch_bridge.py
