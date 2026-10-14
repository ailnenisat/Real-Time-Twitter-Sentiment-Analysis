import os
import re
from collections import Counter
from datetime import timedelta

import nltk
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET
from pymongo import MongoClient
from pymongo.errors import PyMongoError

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
# A short selection timeout keeps the page (and the live polling) responsive
# with a clear error when MongoDB is down, instead of hanging for 30 seconds.
client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
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
# the word and trend charts are built from at most PLOT_SAMPLE_LIMIT of them.
# The totals, counts and percentages are still computed over the whole
# collection - they are aggregated inside MongoDB rather than in Python.
TABLE_LIMIT = int(os.getenv('DASHBOARD_TABLE_LIMIT', '200'))
PLOT_SAMPLE_LIMIT = int(os.getenv('DASHBOARD_PLOT_LIMIT', '500'))

TOP_WORDS_PER_CLASS = 5
TREND_MINUTES = 15

# Tokenising the sample costs real time, and the live page asks for fresh stats
# every few seconds, so the word counts are only recomputed when the number of
# stored tweets has changed.
_top_words_cache = {'key': None, 'value': None}


def top_words_per_class(sample, cache_key):
    if cache_key is not None and _top_words_cache['key'] == cache_key:
        return _top_words_cache['value']

    words_per_class = {label: [] for label in class_list}
    for entry in sample:
        prediction = entry.get('prediction')
        tweet = entry.get('tweet')
        # Skip documents with an unexpected or missing label instead of
        # raising KeyError and taking the dashboard down.
        if prediction not in words_per_class or not tweet:
            continue
        words_per_class[prediction].extend(preprocess_text(tweet))

    result = {
        label: [{'word': word, 'count': count}
                for word, count in Counter(words).most_common(TOP_WORDS_PER_CLASS)]
        for label, words in words_per_class.items()
    }
    _top_words_cache.update(key=cache_key, value=result)
    return result


def received_at(doc):
    """When the consumer stored the tweet; MongoDB encodes it in the ObjectId."""
    stamp = getattr(doc.get('_id'), 'generation_time', None)
    return stamp


def trend_per_minute(sample):
    """Tweets per minute and class over the last TREND_MINUTES minutes of data.

    The window ends at the newest stored tweet rather than at "now", so the
    chart still shows something sensible after the stream has been stopped.
    """
    stamped = [(received_at(doc), doc.get('prediction')) for doc in sample]
    stamped = [(when, label) for when, label in stamped if when is not None]
    if not stamped:
        return {'starts': [], 'series': {label: [] for label in class_list}}

    newest = max(when for when, _ in stamped).replace(second=0, microsecond=0)
    starts = [newest - timedelta(minutes=offset)
              for offset in range(TREND_MINUTES - 1, -1, -1)]
    series = {label: [0] * TREND_MINUTES for label in class_list}
    for when, label in stamped:
        slot = TREND_MINUTES - 1 - int((newest - when.replace(second=0, microsecond=0))
                                       .total_seconds() // 60)
        if label in series and 0 <= slot < TREND_MINUTES:
            series[label][slot] += 1
    return {'starts': [start.isoformat() for start in starts], 'series': series}


def collect_stats():
    """Everything the dashboard shows, as plain JSON-serialisable data.

    Used both for the first render of the page and by the live /api/stats/
    endpoint, so the two can never disagree.
    """
    # count_documents is a server-side count: no documents cross the wire.
    total = db.tweets.count_documents({})

    counts = {label: 0 for label in class_list}
    # Group by label inside MongoDB instead of pulling every document into
    # Python just to tally it.
    for row in db.tweets.aggregate([{'$group': {'_id': '$prediction', 'n': {'$sum': 1}}}]):
        if row['_id'] in counts:
            counts[row['_id']] = row['n']

    rates = {label: (round(count / total * 100, 1) if total else 0.0)
             for label, count in counts.items()}

    # One bounded query serves the table and the charts. Sorting by _id
    # descending gives newest-first, since ObjectIds increase with insertion.
    recent = list(
        db.tweets.find()
        .sort('_id', -1)
        .limit(max(TABLE_LIMIT, PLOT_SAMPLE_LIMIT))
    )
    sample = recent[:PLOT_SAMPLE_LIMIT]

    tweets = []
    for doc in recent[:TABLE_LIMIT]:
        when = received_at(doc)
        tweets.append({
            'id': str(doc.get('_id')),
            'tweet': doc.get('tweet') or '',
            'prediction': doc.get('prediction') or '',
            'received': when.isoformat() if when else None,
        })

    return {
        'total': total,
        'counts': counts,
        'rates': rates,
        'tweets': tweets,
        'table_limit': TABLE_LIMIT,
        'sample_size': len(sample),
        'top_words': top_words_per_class(sample, cache_key=total) if total else
                     {label: [] for label in class_list},
        'trend': trend_per_minute(sample),
        'labels': class_list,
    }

# -----------------------------------------------------

def dashboard(request):
    try:
        stats = collect_stats()
        error = ''
    except PyMongoError:
        stats = None
        error = 'Cannot reach MongoDB. Check that it is running, then reload.'
    return render(request, 'dashboard/index.html', {'stats': stats, 'error': error})


@require_GET
def api_stats(request):
    """Live data for the dashboard; polled by static/js/dashboard.js."""
    try:
        response = JsonResponse(collect_stats())
    except PyMongoError:
        return JsonResponse({'error': 'Cannot reach MongoDB.'}, status=503)
    response['Cache-Control'] = 'no-store'
    return response


def wants_json(request):
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


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
            error_text = "Enter some text to classify."

        if wants_json(request):
            if error:
                status = 400 if not text.strip() else 500
                return JsonResponse({'error': error_text}, status=status)
            return JsonResponse({'prediction': str(prediction), 'text': text.strip()})

    context = {
        "error": error,
        "error_text": error_text,
        "prediction": prediction,
        "text": text,
        "text_len": len(text.strip()),
    }
    return render(request, 'dashboard/classify.html', context)
