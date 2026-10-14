"""Kafka producer: replays the validation tweets CSV as a live tweet stream.

Each CSV row is [tweet_id, entity, sentiment, tweet_text] and is sent to the
Kafka topic as a JSON list. The consumer only uses the last field (the text).

Configuration (environment variables, with host-friendly defaults):
    KAFKA_BOOTSTRAP_SERVERS  default "localhost:9092"  (docker: "kafka1:19092")
    KAFKA_TOPIC              default "twitter"
    TWEETS_CSV               default the twitter_validation.csv next to this file
    PRODUCER_DELAY           seconds to wait between tweets, default "3"
"""

import csv
import json
import os
import sys
from pathlib import Path
from time import sleep

from kafka import KafkaProducer
from kafka.errors import NoBrokersAvailable

BASE_DIR = Path(__file__).resolve().parent

BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092").split(",")
TOPIC = os.getenv("KAFKA_TOPIC", "twitter")
CSV_PATH = Path(os.getenv("TWEETS_CSV", BASE_DIR / "twitter_validation.csv"))
DELAY = float(os.getenv("PRODUCER_DELAY", "3"))


def connect(retries=30, backoff=2):
    """Wait for the Kafka broker to accept connections.

    Under docker-compose the broker is usually still starting up when this
    container launches, so a plain connect would crash immediately.
    """
    for attempt in range(1, retries + 1):
        try:
            return KafkaProducer(
                bootstrap_servers=BOOTSTRAP_SERVERS,
                value_serializer=lambda x: json.dumps(x).encode("utf-8"),
            )
        except NoBrokersAvailable:
            print(f"Kafka not reachable at {BOOTSTRAP_SERVERS} "
                  f"(attempt {attempt}/{retries}), retrying in {backoff}s...", flush=True)
            sleep(backoff)
    raise SystemExit(f"Could not reach Kafka at {BOOTSTRAP_SERVERS} - is the broker running?")


def main():
    if not CSV_PATH.exists():
        raise SystemExit(f"Tweets CSV not found: {CSV_PATH}")

    producer = connect()
    print(f"Connected to Kafka at {BOOTSTRAP_SERVERS}, streaming to topic '{TOPIC}'", flush=True)

    sent = 0
    # encoding is explicit: the tweets contain emoji, and Python on Windows
    # would otherwise open the file with cp1252 and raise UnicodeDecodeError.
    with open(CSV_PATH, encoding="utf-8", newline="") as file_obj:
        for row in csv.reader(file_obj):
            if not row:
                continue
            producer.send(TOPIC, value=row)
            sent += 1
            print(f"[{sent}] sent -> {row[-1][:90]}", flush=True)
            sleep(DELAY)

    producer.flush()
    print(f"Done. {sent} tweets sent to topic '{TOPIC}'.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped by user.")
        sys.exit(0)
