# Shared image for the three Python services (producer, Spark consumer, Django).
#
# Why a container at all: PySpark needs a JVM, and on Windows it additionally
# needs Hadoop's winutils.exe/hadoop.dll native binaries. Running on a Linux
# base image with a real JDK avoids that entirely and makes the project
# reproducible on any machine that has Docker.
FROM python:3.11-slim-bookworm

# openjdk-17  : the JVM PySpark 3.5 runs on
# procps      : Spark shells out to `ps` when it starts a worker
# build-essential is not needed - every dependency ships a manylinux wheel.
RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-17-jre-headless \
        procps \
    && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NLTK_DATA=/usr/share/nltk_data

WORKDIR /app

COPY requirements.txt .
# pyspark is a ~320MB wheel, so the default pip timeout is far too short.
RUN pip install --no-cache-dir --timeout 120 --retries 10 -r requirements.txt

# Bake the NLTK corpora into the image so the dashboard never has to reach the
# network at request time. "punkt_tab" is the nltk>=3.9 replacement for "punkt".
RUN python -m nltk.downloader -d /usr/share/nltk_data punkt punkt_tab stopwords

# The source is bind-mounted by docker-compose, so edits on the host take
# effect without a rebuild.
