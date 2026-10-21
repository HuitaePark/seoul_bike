"""Kafka I/O only; processing and recovery are in worker.py."""
from __future__ import annotations

import argparse

REQUEST_TOPIC = "solve.requests"
RESULT_TOPIC = "solve.results"
RETRY_TOPIC = "solve.requests.retry"
DLQ_TOPIC = "solve.requests.dlq"


class KafkaTransport:
    def __init__(self, bootstrap_servers: str, group_id: str, *, delivery_timeout_s: float = 30):
        from confluent_kafka import Consumer, Producer

        self.timeout = delivery_timeout_s
        self.consumer = Consumer({"bootstrap.servers": bootstrap_servers, "group.id": group_id,
                                  "enable.auto.commit": False, "enable.auto.offset.store": False,
                                  "auto.offset.reset": "earliest", "allow.auto.create.topics": False})
        self.producer = Producer({"bootstrap.servers": bootstrap_servers, "acks": "all",
                                  "enable.idempotence": True,
                                  "message.timeout.ms": int(delivery_timeout_s * 1000)})
        self.consumer.subscribe([REQUEST_TOPIC, RETRY_TOPIC])

    def poll(self, timeout: float = 1):
        message = self.consumer.poll(timeout)
        if message is not None and message.error():
            raise RuntimeError(f"Kafka consumption failed: {message.error()}")
        return message

    def publish(self, *, key: bytes, value: bytes, headers: list[tuple[str, bytes]]) -> None:
        self.forward(topic=RESULT_TOPIC, key=key, value=value, headers=headers)

    def forward(self, *, topic: str, key: bytes | None, value: bytes | None,
                headers: list[tuple[str, bytes | None]]) -> None:
        if topic not in (RESULT_TOPIC, RETRY_TOPIC, DLQ_TOPIC):
            raise ValueError("unsupported output topic")
        delivered = []

        def completed(error, _message):
            delivered.append(error)

        self.producer.produce(topic, key=key, value=value, headers=headers, on_delivery=completed)
        pending = self.producer.flush(self.timeout)
        if pending or not delivered or delivered[0] is not None:
            raise RuntimeError("Kafka delivery was not acknowledged; input remains uncommitted")

    def commit(self, message) -> None:
        partitions = self.consumer.commit(message=message, asynchronous=False)
        if not partitions or any(partition.error is not None for partition in partitions):
            raise RuntimeError("Kafka input offset was not committed")

    def close(self) -> None:
        # Automatic commits are disabled; closing after failure cannot skip work.
        self.consumer.close()


def create_solve_topics(bootstrap_servers: str) -> None:
    from confluent_kafka import KafkaError, KafkaException
    from confluent_kafka.admin import AdminClient, NewTopic

    admin = AdminClient({"bootstrap.servers": bootstrap_servers})
    futures = admin.create_topics([NewTopic(name, num_partitions=1, replication_factor=1)
                                  for name in (REQUEST_TOPIC, RESULT_TOPIC, RETRY_TOPIC, DLQ_TOPIC)], request_timeout=20)
    for future in futures.values():
        try:
            future.result(timeout=25)
        except KafkaException as error:
            if error.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
                raise


def main():
    parser = argparse.ArgumentParser(description="Create the four solve topics in a local development broker")
    parser.add_argument("--bootstrap-servers", required=True)
    args = parser.parse_args()
    create_solve_topics(args.bootstrap_servers)


if __name__ == "__main__":
    main()
