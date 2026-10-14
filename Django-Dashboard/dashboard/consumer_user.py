"""On-demand tweet classification for the /classify page.

Loads the same Spark ML pipeline the streaming consumer uses, but lazily: the
SparkSession is only started the first time a user actually submits text, so
`manage.py runserver`, `migrate` and `collectstatic` stay fast.
"""

import os
import re
from pathlib import Path

from pyspark.ml import PipelineModel
from pyspark.sql import SparkSession

# dashboard/ -> Django-Dashboard/
BASE_DIR = Path(__file__).resolve().parent.parent
MODEL_PATH = Path(os.getenv("MODEL_PATH", BASE_DIR / "logistic_regression_model.pkl"))

class_index_mapping = {0: "Negative", 1: "Positive", 2: "Neutral", 3: "Irrelevant"}

_spark = None
_pipeline = None


def _get_pipeline():
    """Start Spark and load the model once, on first use."""
    global _spark, _pipeline
    if _pipeline is None:
        if not MODEL_PATH.exists():
            raise FileNotFoundError(f"Spark model not found: {MODEL_PATH}")
        _spark = SparkSession.builder.appName("classify tweets").getOrCreate()
        _spark.sparkContext.setLogLevel("ERROR")
        _pipeline = PipelineModel.load(MODEL_PATH.as_posix())
    return _spark, _pipeline


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
    # Remove non-alphabetic characters
    text = re.sub(r'[^a-zA-Z\s]', '', text)
    # Remove extra whitespaces
    return re.sub(r'\s+', ' ', text).strip()


def classify_text(text: str) -> str:
    """Return the predicted sentiment label for a piece of free text."""
    preprocessed_tweet = clean_text(text)
    if not preprocessed_tweet:
        # Nothing classifiable left (e.g. the user typed only emoji or a URL).
        return "Irrelevant"

    spark, pipeline = _get_pipeline()
    data = spark.createDataFrame([(preprocessed_tweet,)], ["Text"])
    processed = pipeline.transform(data)
    # Select by name rather than by position so pipeline changes can't silently
    # return the wrong column.
    prediction = processed.select("prediction").collect()[0]["prediction"]
    label = class_index_mapping[int(prediction)]

    print("-> Tweet : ", text)
    print("-> preprocessed_tweet : ", preprocessed_tweet)
    print("-> Predicted Sentiment : ", prediction)
    print("-> Predicted Sentiment classname : ", label)
    print("/" * 50)

    return label
