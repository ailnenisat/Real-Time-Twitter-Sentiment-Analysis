import os
import re
from collections import Counter

import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend (no GUI in a web server)
import matplotlib.pyplot as plt
import nltk
import pandas as pd
from django.shortcuts import render
from pymongo import MongoClient

# -----------------------------------------------------
# NLTK data
# -----------------------------------------------------

# nltk >= 3.9 renamed the tokenizer tables from "punkt" to "punkt_tab";
# both are requested so the app works on old and new nltk alike, and a
# missing download (e.g. offline machine) must not crash the server.
for _pkg in ('punkt', 'punkt_tab', 'stopwords'):
    try:
        nltk.download(_pkg, quiet=True)
    except Exception:  # pragma: no cover - network/offline issues only
        pass

try:
    from nltk.corpus import stopwords
    STOP_WORDS = set(stopwords.words('english'))
except Exception:  # pragma: no cover - corpus not downloaded
    STOP_WORDS = set()


def tokenize(text):
    """Tokenise with NLTK, falling back to a regex split if its data is missing."""
    try:
        from nltk.tokenize import word_tokenize
        return word_tokenize(text)
    except Exception:
        return re.findall(r'\b\w+\b', text)

# -----------------------------------------------------
# MongoDB - the dashboard reads the results the Spark consumer wrote
# -----------------------------------------------------

MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017/')
client = MongoClient(MONGO_URI)
db = client['bigdata_project']

# -----------------------------------------------------

def clean_text(text):
    # Remove URLs using regular expression
    text = re.sub(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', '', text)
    # Remove words starting with '@'
    text = re.sub(r'\b@\w+\b', '', text)
    # Remove non-alphanumeric characters
    text = re.sub(r'[^a-zA-Z0-9\s]', '', text)
    # Convert text to lowercase
    text = text.lower()
    return text.strip()


def preprocess_text(text):
    tokens = tokenize(clean_text(text))
    # Remove stopwords
    return [word for word in tokens if word.lower() not in STOP_WORDS]


class_list = ['Negative', 'Positive', 'Neutral', 'Irrelevant']

# The collection grows without bound while the consumer runs, so the dashboard
# never reads all of it: the table shows the most recent TABLE_LIMIT tweets and
# the word-frequency plot is built from at most PLOT_SAMPLE_LIMIT of them.
# The totals, counts and percentages are still computed over the whole
# collection - they are aggregated inside MongoDB rather than in Python.
TABLE_LIMIT = int(os.getenv('DASHBOARD_TABLE_LIMIT', '200'))
PLOT_SAMPLE_LIMIT = int(os.getenv('DASHBOARD_PLOT_LIMIT', '500'))


_last_plot_key = None


def plot_word_frequencies_per_class(data, cache_key=None):
    """Render the 'top words per sentiment class' bar chart into the static dir.

    Tokenising the sample and rendering the figure costs real time, so the work
    is skipped when `cache_key` shows nothing has changed since the last render
    and the PNG is still on disk.
    """
    global _last_plot_key

    static_folder = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), 'BigDataProject', 'static', 'imgs'
    )
    # The folder is not in version control on a fresh clone, and savefig would
    # fail with FileNotFoundError rather than creating it.
    os.makedirs(static_folder, exist_ok=True)
    plot_path = os.path.join(static_folder, 'Word_Frequencies_for_all_classes.png')

    if cache_key is not None and cache_key == _last_plot_key and os.path.exists(plot_path):
        return

    all_words_per_class = {class_label: [] for class_label in class_list}
    color_map = {'Negative': 'red', 'Positive': 'blue', 'Neutral': 'green', 'Irrelevant': 'purple'}

    for entry in data:
        prediction = entry.get('prediction')
        tweet = entry.get('tweet')
        # Skip documents with an unexpected or missing label instead of
        # raising KeyError and taking the whole dashboard down.
        if prediction not in all_words_per_class or not tweet:
            continue
        all_words_per_class[prediction].extend(preprocess_text(tweet))

    plt.figure(figsize=(12, 8))
    plotted = False

    for class_label, class_words in all_words_per_class.items():
        if not class_words:
            continue
        top_words = dict(Counter(class_words).most_common(5))
        df = pd.DataFrame(list(top_words.items()), columns=['Word', 'Frequency'])
        plt.bar(df['Word'] + f' ({class_label})', df['Frequency'],
                color=color_map[class_label], alpha=0.7, label=class_label)
        plotted = True

    plt.xlabel('Words')
    plt.ylabel('Frequency')
    if plotted:
        plt.legend()
    plt.xticks(rotation=70)
    plt.tight_layout()

    plt.savefig(plot_path)
    plt.close()  # release the figure; otherwise every request leaks one
    _last_plot_key = cache_key

# -----------------------------------------------------

def dashboard(request):
    # count_documents is a server-side count: no documents cross the wire.
    len_data = db.tweets.count_documents({})
    print("len_data : ", len_data)

    if len_data == 0:
        return render(request, 'dashboard/index.html', {'len_data': len_data})

    # Group by label inside MongoDB instead of pulling every document into
    # Python just to tally it.
    sentiment_counts = {label: 0 for label in class_list}
    for row in db.tweets.aggregate([{'$group': {'_id': '$prediction', 'n': {'$sum': 1}}}]):
        if row['_id'] in sentiment_counts:
            sentiment_counts[row['_id']] = row['n']

    # Calculate the rate of each class
    sentiment_rates = {label: round((count / len_data) * 100, 2)
                       for label, count in sentiment_counts.items()}

    # One bounded query serves both the table and the plot. Sorting by _id
    # descending gives newest-first, since ObjectIds increase with insertion.
    recent = list(
        db.tweets.find()
        .sort('_id', -1)
        .limit(max(TABLE_LIMIT, PLOT_SAMPLE_LIMIT))
    )
    data = recent[:TABLE_LIMIT]
    plot_sample = recent[:PLOT_SAMPLE_LIMIT]

    plot_word_frequencies_per_class(plot_sample, cache_key=len_data)

    context = {
        'data': data,
        'len_data': len_data,
        'shown_count': len(data),
        'plot_sample_size': len(plot_sample),
        'sentiment_rates': sentiment_rates,
        'sentiment_counts': sentiment_counts,
    }
    return render(request, 'dashboard/index.html', context)


def classify(request):
    error = False
    error_text = ""
    prediction = ""
    text = ""

    if request.method == 'POST':
        # `or ''` guards against the field being absent from the POST body,
        # which would otherwise raise AttributeError on None.strip().
        text = request.POST.get('text') or ''
        if text.strip():
            from .consumer_user import classify_text
            try:
                prediction = classify_text(text)
            except Exception as exc:  # Spark failed to start or model missing
                error = True
                error_text = f"Could not classify the text: {exc}"
        else:
            error = True
            error_text = "the Text is empty!! PLZ Enter Your Text."

    context = {
        "error": error,
        "error_text": error_text,
        "prediction": prediction,
        "text": text,
        "text_len": len(text.strip()),
    }
    return render(request, 'dashboard/classify.html', context)
