"""Kafka consumer: classifies incoming tweets with the Spark ML pipeline
and stores the results in MongoDB.

Pipeline: Kafka topic -> clean text -> Spark PipelineModel
          (Tokenizer -> StopWordsRemover -> CountVectorizer -> LogisticRegression)
          -> MongoDB collection `bigdata_project.tweets`

Configuration (environment variables, with host-friendly defaults):
    KAFKA_BOOTSTRAP_SERVERS  default "localhost:9092"  (docker: "kafka1:19092")
    KAFKA_TOPIC              default "twitter"
    MONGO_URI                default "mongodb://localhost:27017/"
    MODEL_PATH               default the logistic_regression_model.pkl next to this file
"""

import os
import re
import sys
from json import loads
from pathlib import Path
from time import sleep

from kafka import KafkaConsumer
from kafka.errors import NoBrokersAvailable
from pymongo import MongoClient
from pyspark.ml import PipelineModel
from pyspark.sql import SparkSession

BASE_DIR = Path(__file__).resolve().parent

BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092").split(",")
TOPIC = os.getenv("KAFKA_TOPIC", "twitter")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
MODEL_PATH = Path(os.getenv("MODEL_PATH", BASE_DIR / "logistic_regression_model.pkl"))

# The label encoder used at training time mapped the sentiment strings to these
# indices, so the model's numeric `prediction` is decoded back through it.
class_index_mapping = {0: "Negative", 1: "Positive", 2: "Neutral", 3: "Irrelevant"}


def clean_text(text):
    """Same normalisation that was applied to the training data."""
    if not text:
        return ''
    # Remove links starting with https://, http://, www., or containing .com
    text = re.sub(r'https?://\S+|www\.\S+|\.com\S+|youtu\.be/\S+', '', text)
    # Remove words starting with # or @
    text = re.sub(r'(@|#)\w+', '', text)
    # Convert to lowercase
    text = text.lower()
    # Remove non-alphanumeric characters
    text = re.sub(r'[^a-zA-Z\s]', '', text)
    # Remove extra whitespaces
    return re.sub(r'\s+', ' ', text).strip()


def connect_kafka(retries=30, backoff=2):
    """Wait for the broker; under docker-compose it may still be starting."""
    for attempt in range(1, retries + 1):
        try:
            return KafkaConsumer(
                TOPIC,
                bootstrap_servers=BOOTSTRAP_SERVERS,
                auto_offset_reset='earliest',
                enable_auto_commit=True,
                group_id='my-group',
                value_deserializer=lambda x: loads(x.decode('utf-8')),
            )
        except NoBrokersAvailable:
            print(f"Kafka not reachable at {BOOTSTRAP_SERVERS} "
                  f"(attempt {attempt}/{retries}), retrying in {backoff}s...", flush=True)
            sleep(backoff)
    raise SystemExit(f"Could not reach Kafka at {BOOTSTRAP_SERVERS} - is the broker running?")


def main():
    if not MODEL_PATH.exists():
        raise SystemExit(f"Spark model not found: {MODEL_PATH}")

    client = MongoClient(MONGO_URI)
    db = client['bigdata_project']
    collection = db['tweets']
    # Fail fast with a clear message rather than hanging on the first insert.
    client.admin.command('ping')
    print(f"Connected to MongoDB at {MONGO_URI}", flush=True)

    spark = SparkSession.builder.appName("classify tweets").getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")

    # as_posix() keeps the path readable by Hadoop's FileSystem on every OS
    pipeline = PipelineModel.load(MODEL_PATH.as_posix())
    print(f"Loaded Spark pipeline model from {MODEL_PATH}", flush=True)

    consumer = connect_kafka()
    print(f"Listening on Kafka topic '{TOPIC}' at {BOOTSTRAP_SERVERS}...", flush=True)

    for message in consumer:
        value = message.value
        # Rows arrive as [id, entity, sentiment, text]; the text is the last field.
        tweet = value[-1] if isinstance(value, list) else str(value)
        preprocessed_tweet = clean_text(tweet)
        if not preprocessed_tweet:
            print(f"-> skipped (nothing left after cleaning): {tweet[:80]}", flush=True)
            continue

        data = spark.createDataFrame([(preprocessed_tweet,)], ["Text"])
        processed = pipeline.transform(data)
        # Select the column by name: positional indexing breaks if the pipeline
        # ever gains or reorders a stage.
        prediction = processed.select("prediction").collect()[0]["prediction"]
        label = class_index_mapping[int(prediction)]

        print("-> Tweet:", tweet)
        print("-> preprocessed_tweet:", preprocessed_tweet)
        print("-> Predicted Sentiment:", prediction)
        print("-> Predicted Sentiment classname:", label)

        collection.insert_one({"tweet": tweet, "prediction": label})
        print("/" * 50, flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped by user.")
        sys.exit(0)
