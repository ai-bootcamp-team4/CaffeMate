import base64
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast

import google.cloud.pubsub_v1
import rfc8785

from worker.outbox import MessagePublisher


class InvalidPubSubEnvelopeError(ValueError):
    pass


@dataclass(frozen=True)
class PubSubDelivery:
    message_id: str
    logical_topic: str
    payload: Mapping[str, object]
    attributes: Mapping[str, str]


class PublishFuture(Protocol):
    def result(self, timeout: float | None = None) -> str: ...


class PublisherClient(Protocol):
    def publish(self, topic: str, data: bytes, **attributes: str) -> PublishFuture: ...


class GooglePubSubPublisher(MessagePublisher):
    def __init__(
        self,
        *,
        topic_resources: Mapping[str, str],
        client: PublisherClient | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._topic_resources = dict(topic_resources)
        self._client = client or cast(PublisherClient, google.cloud.pubsub_v1.PublisherClient())
        self._timeout_seconds = timeout_seconds

    def publish(
        self,
        *,
        topic: str,
        payload: Mapping[str, object],
        attributes: Mapping[str, str],
    ) -> str:
        resource = self._topic_resources.get(topic)
        if resource is None:
            raise ValueError(f"Pub/Sub topic is not allowlisted: {topic}")
        data = rfc8785.dumps(dict(payload))  # type: ignore[arg-type]
        publish_attributes = {**attributes, "logical_topic": topic}
        return self._client.publish(resource, data, **publish_attributes).result(
            timeout=self._timeout_seconds
        )


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
        raise InvalidPubSubEnvelopeError("Pub/Sub data is not canonical JSON input") from error
    if not isinstance(payload, dict):
        raise InvalidPubSubEnvelopeError("Pub/Sub payload must be an object")
    logical_topic = attributes.get("logical_topic")
    expected_digest = attributes.get("payload_digest")
    actual_digest = hashlib.sha256(rfc8785.dumps(payload)).hexdigest()
    if not logical_topic or expected_digest != actual_digest:
        raise InvalidPubSubEnvelopeError("Pub/Sub topic or payload digest does not match")
    return PubSubDelivery(
        message_id=message_id,
        logical_topic=logical_topic,
        payload=payload,
        attributes=attributes,
    )
