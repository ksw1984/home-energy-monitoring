"""Small MQTT transport for versioned HEMS measurement events."""

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from src.shared.measurement import Measurement
from src.shared.measurement_bus import MeasurementBus

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)
TOPIC_ROOT = "home-energy/v1/measurements"


def measurement_topic(source: str, metric: str) -> str:
    """Return the stable topic for a normalized measurement series."""
    return f"{TOPIC_ROOT}/{source}/{metric}"


def _message_payload(measurement: Measurement) -> bytes:
    event_id = uuid5(NAMESPACE_URL, f"{measurement.source}/{measurement.metric}/{measurement.timestamp.isoformat()}")
    return json.dumps(
        {
            "schema_version": 1,
            "event_id": str(event_id),
            "source": measurement.source,
            "metric": measurement.metric,
            "timestamp": measurement.timestamp.isoformat(),
            "published_at": datetime.now().astimezone().isoformat(),
            "value": measurement.value,
            "unit": measurement.unit,
            "measurement_type": measurement.measurement_type,
        },
        separators=(",", ":"),
    ).encode()


def _mqtt_client(client_id: str) -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    username = os.getenv("MQTT_USERNAME")
    password = os.getenv("MQTT_PASSWORD")
    if username:
        client.username_pw_set(username, password)
    return client


class MqttMeasurementPublisher:
    """Enqueue current measurements to MQTT without waiting in the HEMS loop."""

    def __init__(self) -> None:
        self.client = _mqtt_client("hems-publisher")
        self.connected = False
        self.host = os.getenv("MQTT_HOST", "mqtt")
        self.port = int(os.getenv("MQTT_PORT", "1883"))
        self.minimum_interval_s = 1.0
        self.allowed: set[tuple[str, str]] = set()
        self._last_published: dict[tuple[str, str], float] = {}
        self.client.on_connect = self._on_connect

    def configure(self, config: Any) -> None:
        """Read the live BEMS input mapping and cadence from shared config."""
        bems = config.bems
        if bems is None or not bems.enabled:
            self.allowed = set()
            return
        self.allowed = {
            (bems.inputs.grid.source, bems.inputs.grid.metric),
            (bems.inputs.household.source, bems.inputs.household.metric),
        }
        self.minimum_interval_s = bems.control.mqtt_publish_interval_s

    def _on_connect(self, _client: mqtt.Client, _userdata: Any, _flags: Any, reason_code: Any, _properties: Any) -> None:
        self.connected = reason_code == 0
        if self.connected:
            logger.info("Connected to MQTT broker %s:%s", self.host, self.port)
        else:
            logger.error("MQTT broker connection rejected: %s", reason_code)

    async def start(self) -> None:
        """Connect asynchronously and start the network loop."""
        self.client.connect_async(self.host, self.port, 30)
        self.client.loop_start()

    def publish(self, measurements: list[Measurement]) -> None:
        """Publish only configured grid and household series; retain latest values."""
        if not self.connected:
            return
        now = time.monotonic()
        for measurement in measurements:
            if (measurement.source, measurement.metric) not in self.allowed:
                continue
            key = (measurement.source, measurement.metric)
            if now - self._last_published.get(key, float("-inf")) < self.minimum_interval_s:
                continue
            info = self.client.publish(measurement_topic(measurement.source, measurement.metric), _message_payload(measurement), qos=1, retain=True)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                logger.warning("MQTT publish queue rejected %s/%s: %s", measurement.source, measurement.metric, info.rc)
            else:
                self._last_published[key] = now

    async def close(self) -> None:
        self.client.disconnect()
        self.client.loop_stop()


class MqttMeasurementSubscriber:
    """Receive HEMS measurements and feed the BEMS-local measurement bus."""

    def __init__(self, bus: MeasurementBus, *, sources: list[tuple[str, str]]) -> None:
        self.bus = bus
        self.sources = sources
        self.loop: asyncio.AbstractEventLoop | None = None
        self.client = _mqtt_client("bems-subscriber")
        self.host = os.getenv("MQTT_HOST", "mqtt")
        self.port = int(os.getenv("MQTT_PORT", "1883"))
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def _on_connect(self, client: mqtt.Client, _userdata: Any, _flags: Any, reason_code: Any, _properties: Any) -> None:
        if reason_code != 0:
            logger.error("MQTT subscriber connection rejected: %s", reason_code)
            return
        for source, metric in self.sources:
            client.subscribe(measurement_topic(source, metric), qos=1)
        logger.info("Subscribed to %d HEMS measurement topics", len(self.sources))

    def _on_message(self, _client: mqtt.Client, _userdata: Any, message: mqtt.MQTTMessage) -> None:
        try:
            payload = json.loads(message.payload)
            if payload.get("schema_version") != 1:
                raise ValueError("unsupported MQTT measurement schema")
            measurement = Measurement(
                timestamp=datetime.fromisoformat(payload["timestamp"]),
                source=str(payload["source"]),
                metric=str(payload["metric"]),
                value=float(payload["value"]),
                unit=str(payload.get("unit", "")),
                measurement_type=str(payload.get("measurement_type", "current")),
            )
            if measurement.timestamp.tzinfo is None or measurement.timestamp.utcoffset() is None:
                raise ValueError("measurement timestamp must include a timezone")
        except (KeyError, TypeError, ValueError):
            logger.exception("Discarding invalid MQTT measurement on %s", message.topic)
            return
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.bus.publish, [measurement])

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.client.connect_async(self.host, self.port, 30)
        self.client.loop_start()

    def update_sources(self, sources: list[tuple[str, str]]) -> None:
        """Replace topic subscriptions after BEMS input configuration changes."""
        removed = set(self.sources) - set(sources)
        added = set(sources) - set(self.sources)
        for source, metric in removed:
            self.client.unsubscribe(measurement_topic(source, metric))
        for source, metric in added:
            self.client.subscribe(measurement_topic(source, metric), qos=1)
        self.sources = sources

    async def close(self) -> None:
        self.client.disconnect()
        self.client.loop_stop()
