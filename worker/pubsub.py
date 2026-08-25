import base64
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

import rfc8785


class InvalidPubSubEnvelopeError(ValueError):
    pass


@dataclass(frozen=True)
class PubSubDelivery:
    message_id: str
    logical_topic: str
    payload: Mapping[str, object]
    attributes: Mapping[str, str]


def decode_push_envelope(
    body: Mapping[str, object],
    *,
    expected_subscription: str,
) -> PubSubDelivery:
    if body.get("subscription") != expected_subscription:
        raise InvalidPubSubEnvelopeError("Unexpected Pub/Sub subscription")
    message = body.get("message")
    if not isinstance(message, dict):
        raise InvalidPubSubEnvelopeError("Pub/Sub message is missing")
    message_id = message.get("messageId") or message.get("message_id")
    encoded_data = message.get("data")
    attributes = message.get("attributes")
    if not isinstance(message_id, str) or not isinstance(encoded_data, str):
        raise InvalidPubSubEnvelopeError("Pub/Sub message identity or data is invalid")
    if not isinstance(attributes, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in attributes.items()
    ):
        raise InvalidPubSubEnvelopeError("Pub/Sub attributes are invalid")
    try:
        decoded = base64.b64decode(encoded_data, validate=True)
        payload = json.loads(decoded)
    except (ValueError, json.JSONDecodeError) as error:
        raise InvalidPubSubEnvelopeError("Pub/Sub data is invalid") from error
    if not isinstance(payload, dict):
        raise InvalidPubSubEnvelopeError("Pub/Sub payload must be an object")
    logical_topic = attributes.get("logical_topic")
    expected_digest = attributes.get("payload_digest")
    actual_digest = hashlib.sha256(rfc8785.dumps(payload)).hexdigest()
    if logical_topic != "WORKFLOW_STAGE_READY" or expected_digest != actual_digest:
        raise InvalidPubSubEnvelopeError("Pub/Sub topic or payload digest does not match")
    return PubSubDelivery(
        message_id=message_id,
        logical_topic=logical_topic,
        payload=payload,
        attributes=attributes,
    )