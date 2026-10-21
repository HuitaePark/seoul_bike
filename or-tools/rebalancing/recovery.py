"""Bounded request recovery. Payloads remain unchanged; provenance lives in headers."""
from dataclasses import dataclass
import math

from .journal import RequestConflict
from .kafka_transport import REQUEST_TOPIC, RETRY_TOPIC, DLQ_TOPIC

ORIGINAL = "kafka_original-topic"
ERROR = "kafka_exception-message"
PARTITION = "optimizer_original-partition"
OFFSET = "optimizer_original-offset"
ATTEMPT = "optimizer_retry-attempt"
STAGE = "optimizer_failure-stage"
RESERVED = frozenset((ORIGINAL, ERROR, PARTITION, OFFSET, ATTEMPT, STAGE))


@dataclass(frozen=True)
class Delivery:
    topic: str
    partition: int
    offset: int
    attempt: int = 0


class Recovery:
    def __init__(self, transport, *, attempts=3, delay_seconds=2):
        if type(attempts) is not int or not 0 <= attempts <= 10:
            raise ValueError("retry-attempts must be an integer between 0 and 10")
        if not math.isfinite(delay_seconds) or not 0 <= delay_seconds <= 60:
            raise ValueError("retry-delay-seconds must be finite and between 0 and 60")
        self.transport, self.attempts, self.delay_seconds = transport, attempts, delay_seconds

    @staticmethod
    def source(message):
        return Delivery(message.topic(), message.partition(), message.offset())

    def inspect(self, message):
        source = self.source(message)
        if source.topic == REQUEST_TOPIC:
            return source  # Never trust a producer's retry counter or provenance.
        if source.topic != RETRY_TOPIC:
            raise ValueError("unexpected input topic")
        headers = {}
        for key, value in message.headers() or []:
            if key in RESERVED:
                if key in headers:
                    raise ValueError("duplicate recovery header")
                headers[key] = value
        if headers.get(ORIGINAL) != REQUEST_TOPIC.encode():
            raise ValueError("invalid original topic")

        def number(key, maximum, minimum=0):
            raw = headers.get(key)
            if (not isinstance(raw, bytes) or not raw or len(raw) > 19
                    or any(c < 48 or c > 57 for c in raw)):
                raise ValueError("invalid recovery number")
            value = int(raw)
            if not minimum <= value <= maximum:
                raise ValueError("recovery number out of range")
            return value

        return Delivery(REQUEST_TOPIC, number(PARTITION, 2**31 - 1), number(OFFSET, 2**63 - 1),
                        number(ATTEMPT, self.attempts, minimum=1))

    def handle(self, message, delivery, *, stage, error, code):
        permanent = (stage in ("decode", "validate") and isinstance(error, ValueError)
                     or stage == "lookup" and isinstance(error, RequestConflict))
        retryable = stage in ("compute", "publish")
        if not permanent and not retryable:
            return None  # State corruption, disk errors and source commit errors must stop the worker.
        return self.route(message, delivery, stage=stage, code=code, retryable=retryable)

    def route(self, message, delivery, *, stage, code, retryable=False):
        retry = retryable and delivery.attempt < self.attempts
        attempt = delivery.attempt + 1 if retry else delivery.attempt
        topic = RETRY_TOPIC if retry else DLQ_TOPIC
        headers = [(key, value) for key, value in (message.headers() or []) if key not in RESERVED]
        headers.extend([(ORIGINAL, delivery.topic.encode()), (PARTITION, str(delivery.partition).encode()),
                        (OFFSET, str(delivery.offset).encode()), (ATTEMPT, str(attempt).encode()),
                        (STAGE, stage.encode()), (ERROR, code.encode())])
        # An enqueue is insufficient. forward() must confirm broker ACK before source commit.
        self.transport.forward(topic=topic, key=message.key(), value=message.value(), headers=headers)
        self.transport.commit(message)
        return "retry" if retry else "dead_letter"
