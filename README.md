# Big Data Project: Real-Time Twitter Sentiment Analysis Using Kafka, Spark (MLLib & Streaming), MongoDB and Django.

## Overview

This repository contains a Big Data project focused on real-time sentiment analysis of Twitter data (classification of tweets). The project leverages various technologies to collect, process, analyze, and visualize sentiment data from tweets in real-time.

## Project Architecture

The project is built using the following components:

- **Apache Kafka**: Used for real-time data ingestion from Twitter DataSet.
- **Spark Streaming**: Processes the streaming data from Kafka to perform sentiment analysis.
- **MongoDB**: Stores the processed sentiment data.
- **Django**: Serves as the web framework for building a real-time dashboard to visualize the sentiment analysis results.
- **Inline SVG charts** : the dashboard draws its own charts (no external libraries, works offline) and refreshes itself from the `/api/stats/` endpoint every 5 seconds.

- This is the project plan :
   ![project img](imgs/flow.png)

## Features

- **Real-time Data Ingestion**: Collects live tweets using Kafka from the Twitter DataSet.
- **Stream Processing**: Utilizes Spark Streaming to process and analyze the data in real-time.
- **Sentiment Analysis**: Classifies tweets into different sentiment categories (positive, negative, neutral) using natural language processing (NLP) techniques.
- **Data Storage**: Stores the sentiment analysis results in MongoDB for persistence.
- **Visualization**: Provides a real-time dashboard built with Django to visualize the sentiment trends and insights.

## Data description:

In This Project I'm using a Dataset (twitter_training.csv and twitter_validation.csv) to create pyspark Model and for create live tweets using Kafka. Each line of the "twitter_training.csv" learning database represents a Tweet, it contains over 74682 lines;

The data types of Features are:
- Tweet ID: int
- Entity: string
- Sentiment: string (Target)
- Tweet content: string

The validation database “twitter_validation.csv” contains 998 lines (Tweets) with the same features of “twitter_training.csv”.

This is the Data Source:
https://www.kaggle.com/datasets/jp797498e/twitter-entity-sentiment-analysis

### Use a reviewed TweetClaw or X export

The Kafka producer reads `Kafka-PySpark/twitter_validation.csv`, which expects
four fields: tweet ID, entity, sentiment, and text. To test newer reviewed
tweet exports without hand-editing the CSV, convert them first:

```bash
python tools/normalize_tweet_export.py examples/tweetclaw-reviewed-export.csv \
  --output Kafka-PySpark/twitter_validation.csv
```

The converter accepts common Twitter/X and TweetClaw CSV headers such as
`tweet_id`, `account`, `sentiment`, `label`, `text`, `tweet`, `full_text`, and
`content`. Sentiment values must map to `Negative`, `Positive`, `Neutral`, or
`Irrelevant`. TweetClaw exports from https://github.com/Xquik-dev/tweetclaw are
best used after a review step adds one of those sentiment labels.

## Repository Structure

- **Django-Dashboard** : this folder contains Dashboard Django Application
- **Kafka-PySpark** : this folder contains kafka provider and pyspark streaming (kafka consumer).
- **ML PySpark Model** : this folder contains the trained model with jupyter notebook and datasets.
- **docker-compose.yml** : the full stack (Kafka, MongoDB, producer, consumer, dashboard).
- **docker/Dockerfile** : shared Python 3.11 + JDK 17 image for the three Python services.
- **zk-single-kafka-single.yml** : the original Kafka-only stack, superseded by `docker-compose.yml`.
- **bigdataproject rapport** : a brief report about the project (in french).


## Getting Started

The whole stack runs with Docker Compose. This is the recommended path on every
OS, and the only practical one on Windows: PySpark needs a JVM plus Hadoop's
native `winutils.exe` / `hadoop.dll` binaries to read files from a Windows disk,
and the containers sidestep that entirely by running on Linux with a real JDK 17.

### Prerequisites

- **Docker Desktop** (the only hard requirement - it provides Kafka, MongoDB,
  Java and Python)
- Optionally **MongoDB Compass** to browse the stored predictions; the stack
  publishes MongoDB on `localhost:27017`.

### Run everything

```bash
docker compose up -d --build
```

That starts five services:

| Service     | What it does                                                        | Port   |
|-------------|---------------------------------------------------------------------|--------|
| `kafka1`    | Kafka 3.9 broker in KRaft mode (no Zookeeper)                        | 9092   |
| `mongo`     | MongoDB 7, stores the classified tweets                              | 27017  |
| `producer`  | Replays `twitter_validation.csv` into the `twitter` topic            | -      |
| `consumer`  | Spark ML pipeline: reads Kafka, classifies, writes to MongoDB        | -      |
| `dashboard` | Django site: live dashboard + the `/classify` page                   | 8000   |

Then open **http://127.0.0.1:8000**. The first build takes a few minutes
(PySpark is a ~320 MB download); afterwards startup is seconds.

### Watch it work

```bash
docker compose logs -f consumer     # tweets being classified in real time
docker compose logs -f producer     # tweets being pushed into Kafka
```

Check what landed in MongoDB:

```bash
docker exec mongo mongosh --quiet --eval   'db.getSiblingDB("bigdata_project").tweets.countDocuments()'
```

### Useful commands

```bash
docker compose ps                   # service health
docker compose restart producer     # replay the CSV from the top
docker compose down                 # stop, keep the data
docker compose down -v              # stop and wipe MongoDB + Kafka volumes
```

The producer sends one tweet every 3 seconds by default. To speed up a demo:

```bash
PRODUCER_DELAY=0.2 docker compose up -d producer
```

### Configuration

Every service reads its settings from environment variables, with defaults that
also work when running the scripts directly on the host:

| Variable                  | Default                      | Used by            |
|---------------------------|------------------------------|--------------------|
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092`             | producer, consumer |
| `KAFKA_TOPIC`             | `twitter`                    | producer, consumer |
| `MONGO_URI`               | `mongodb://localhost:27017/` | consumer, dashboard|
| `MODEL_PATH`              | model next to the script     | consumer, dashboard|
| `PRODUCER_DELAY`          | `3`                          | producer           |
| `DJANGO_ALLOWED_HOSTS`    | `localhost,127.0.0.1,...`    | dashboard          |
| `DASHBOARD_TABLE_LIMIT`   | `200`                        | dashboard          |
| `DASHBOARD_PLOT_LIMIT`    | `500`                        | dashboard          |

The dashboard table shows the most recent `DASHBOARD_TABLE_LIMIT` tweets and the
word-frequency plot samples the most recent `DASHBOARD_PLOT_LIMIT`, so the page
stays fast however long the consumer has been running. The totals and the
percentages per class are aggregated inside MongoDB and always cover the whole
collection.

### Running without Docker (optional)

The Python scripts work on the host too, as long as Kafka and MongoDB are
reachable and a JDK is installed:

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
docker compose up -d kafka1 mongo        # just the infrastructure

cd Kafka-PySpark && python producer-validation-tweets.py   # terminal 1
cd Kafka-PySpark && python consumer-pyspark.py             # terminal 2
cd Django-Dashboard && python manage.py migrate && python manage.py runserver
```

> **Windows note:** anything that touches Spark (`consumer-pyspark.py` and the
> `/classify` page) additionally needs `winutils.exe` + `hadoop.dll` for
> Hadoop 3.3 on `HADOOP_HOME`, otherwise Spark fails with
> `UnsatisfiedLinkError: NativeIO$Windows.access0`. Running those two in Docker
> avoids the problem.

### Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `UnsatisfiedLinkError: NativeIO$Windows.access0` | Spark on Windows without winutils - use Docker. |
| `UnsupportedClassVersionError` / Spark won't start | PySpark 4.x needs Java 17+. This project pins PySpark 3.5.x to match the saved model. |
| Dashboard says "the DB is Empty!!" | The consumer hasn't written anything yet; check `docker compose logs consumer`. |
| `zoo1` exits with a `NullPointerException` | The old `zk-single-kafka-single.yml` stack; use `docker-compose.yml` (KRaft, no Zookeeper). |

## More informations : 

- Django Dashboard get the data from MongoDb DataBase.
- the User can classify his owne text in `http://127.0.0.1:8000/classify` link.
- in the Dashboard, There is a table contains tweets with labels.
- in the Dashboard, There is 3 statistics or plots : labels rates - pie plot - bar plot.


## Team :

- [Khattabi Idriss](https://github.com/drisskhattabi6) 
- [Boufarhi Ayman](https://github.com/aymanboufarhi) 
- [Abdelali IBN TABET](https://github.com/abd-ibn)

## Supervised By : 

- Prof. **Yasyn El Yusufi**

---

Abdelmalek Essaadi University - Faculty of Sciences and Technology of Tangier

- Master: Artificial Intelligence and Data Science
- Module: Big Data

---

- By following the above instructions, you should be able to set up and run the real-time Twitter sentiment analysis project on your local machine. Happy coding!

- Feel free to explore the project and customize it according to your requirements. If you encounter any issues or have any questions, don't hesitate to reach out!
