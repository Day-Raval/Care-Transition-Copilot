"""
CLI Entry point to run the Care Transition Copilot persistent Kafka consumer daemon.

Usage:
    python scripts/run_kafka_consumer.py
    python scripts/run_kafka_consumer.py --group-id my-group --workers 4
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

sys.path.insert(0, ".")
from src.data_services.consumer import KafkaEventConsumer
from src.utils.logging_config import setup_logging

setup_logging()
logger = logging.getLogger("kafka_consumer_service")


def main():
    parser = argparse.ArgumentParser(description="Care Transition Copilot Kafka Consumer Daemon")
    parser.add_argument("--group-id", help="Kafka consumer group ID", default=None)
    parser.add_argument("--workers", type=int, help="Worker thread pool size", default=4)
    parser.add_argument("--max-in-flight", type=int, help="Backpressure in-flight limit", default=50)
    args = parser.parse_args()

    consumer = KafkaEventConsumer(
        group_id=args.group_id,
        max_workers=args.workers,
        max_in_flight=args.max_in_flight,
    )

    def handle_signal(sig, frame):
        logger.info("Received termination signal %d. Shutting down consumer...", sig)
        consumer.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    logger.info("Starting Care Transition Copilot Kafka consumer daemon...")
    consumer.start()

    try:
        while consumer.is_running():
            time.sleep(2.0)
            stats = consumer.stats
            logger.info(
                "Consumer stats: consumed=%d processed=%d duplicates=%d dlq=%d in_flight=%d",
                stats["consumed_count"],
                stats["processed_count"],
                stats["duplicate_count"],
                stats["dlq_count"],
                stats["in_flight"],
            )
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received. Stopping...")
        consumer.stop()


if __name__ == "__main__":
    main()
