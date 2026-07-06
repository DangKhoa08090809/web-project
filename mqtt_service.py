"""MQTT integration seam.

An MQTT worker can authenticate a topic's device_id, then pass decoded payloads to
``services.telemetry.validate_record`` and ``store_records`` (using
``end_session=True`` for completed log batches). Keeping persistence
out of the transport layer makes HTTP and MQTT follow identical validation rules.
"""

TELEMETRY_TOPIC = "ecu/{device_id}/telemetry"
STATUS_TOPIC = "ecu/{device_id}/status"
LOGS_TOPIC = "ecu/{device_id}/logs"
