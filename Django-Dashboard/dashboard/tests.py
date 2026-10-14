import sys
import types
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest import mock

from bson import ObjectId
from django.test import SimpleTestCase
from django.urls import reverse
from pymongo.errors import ServerSelectionTimeoutError

from . import views


def make_doc(tweet, prediction, minutes_ago=0, base=None):
    """A stored tweet whose ObjectId carries a known creation time."""
    base = base or datetime(2026, 1, 1, 12, 0, 30, tzinfo=timezone.utc)
    when = base - timedelta(minutes=minutes_ago)
    return {'_id': ObjectId.from_datetime(when), 'tweet': tweet, 'prediction': prediction}


@contextmanager
def stub_classifier(**mock_kwargs):
    """Replace dashboard.consumer_user so tests never start Spark."""
    module = types.ModuleType('dashboard.consumer_user')
    module.classify_text = mock.Mock(**mock_kwargs)
    with mock.patch.dict(sys.modules, {'dashboard.consumer_user': module}):
        yield module.classify_text


class FakeCursor(list):
    def sort(self, *_args, **_kwargs):
        return FakeCursor(sorted(self, key=lambda doc: doc['_id'], reverse=True))

    def limit(self, n):
        return FakeCursor(self[:n])


class FakeCollection:
    def __init__(self, docs):
        self.docs = docs

    def count_documents(self, _query):
        return len(self.docs)

    def aggregate(self, _pipeline):
        counts = {}
        for doc in self.docs:
            counts[doc['prediction']] = counts.get(doc['prediction'], 0) + 1
        return [{'_id': label, 'n': n} for label, n in counts.items()]

    def find(self):
        return FakeCursor(self.docs)


class FakeDb:
    def __init__(self, docs):
        self.tweets = FakeCollection(docs)


SAMPLE_DOCS = [
    make_doc('I love this phone, great battery', 'Positive', 0),
    make_doc('Worst support ever, I hate it', 'Negative', 1),
    make_doc('Worst delay, really bad', 'Negative', 1),
    make_doc('Watching the game tonight', 'Neutral', 3),
    make_doc('Follow me for a free link', 'Irrelevant', 20),   # outside the trend window
    make_doc('Odd label document', 'Mystery', 2),
    {**make_doc('', 'Positive', 5), 'tweet': None},               # tweet text missing
]


class DashboardTests(SimpleTestCase):
    def setUp(self):
        views._top_words_cache.update(key=None, value=None)
        patcher = mock.patch.object(views, 'db', FakeDb(list(SAMPLE_DOCS)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_api_stats_shape_and_values(self):
        response = self.client.get(reverse('api_stats'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-store')
        data = response.json()
        self.assertEqual(data['total'], len(SAMPLE_DOCS))
        self.assertEqual(data['counts'], {'Negative': 2, 'Positive': 2, 'Neutral': 1, 'Irrelevant': 1})
        self.assertEqual(data['rates']['Negative'], round(2 / 7 * 100, 1))
        self.assertEqual(data['labels'], views.class_list)
        self.assertEqual(len(data['tweets']), len(SAMPLE_DOCS))
        self.assertTrue(data['tweets'][0]['received'].startswith('2026-01-01'))

    def test_top_words_ignore_stopwords_and_bad_documents(self):
        data = self.client.get(reverse('api_stats')).json()
        negative = {entry['word']: entry['count'] for entry in data['top_words']['Negative']}
        self.assertEqual(negative.get('worst'), 2)
        self.assertNotIn('Mystery', data['top_words'])
        self.assertEqual(set(data['top_words']), set(views.class_list))

    def test_trend_has_one_slot_per_minute(self):
        data = self.client.get(reverse('api_stats')).json()
        trend = data['trend']
        self.assertEqual(len(trend['starts']), views.TREND_MINUTES)
        for label in views.class_list:
            self.assertEqual(len(trend['series'][label]), views.TREND_MINUTES)
        # Newest tweet is in the last slot; the 20-minute-old one is dropped.
        self.assertEqual(trend['series']['Positive'][-1], 1)
        self.assertEqual(trend['series']['Negative'][-2], 2)
        self.assertEqual(trend['series']['Neutral'][-4], 1)
        self.assertEqual(sum(trend['series']['Irrelevant']), 0)

    def test_dashboard_renders_with_initial_stats(self):
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="initial-stats"')
        self.assertContains(response, 'js/dashboard.js')
        self.assertContains(response, 'data-classify-form')

    def test_tweet_text_is_escaped_in_embedded_json(self):
        views.db.tweets.docs.append(make_doc('</script><script>alert(1)</script>', 'Neutral', 0))
        response = self.client.get(reverse('dashboard'))
        self.assertNotContains(response, '</script><script>alert(1)')

    def test_empty_collection(self):
        views.db.tweets.docs.clear()
        data = self.client.get(reverse('api_stats')).json()
        self.assertEqual(data['total'], 0)
        self.assertEqual(data['tweets'], [])
        self.assertEqual(data['trend']['starts'], [])
        self.assertEqual(data['rates'], {label: 0.0 for label in views.class_list})
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)

    def test_mongo_down(self):
        failing = mock.MagicMock()
        failing.tweets.count_documents.side_effect = ServerSelectionTimeoutError('down')
        with mock.patch.object(views, 'db', failing):
            api = self.client.get(reverse('api_stats'))
            page = self.client.get(reverse('dashboard'))
        self.assertEqual(api.status_code, 503)
        self.assertIn('error', api.json())
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Cannot reach MongoDB')

    def test_api_stats_rejects_post(self):
        self.assertEqual(self.client.post(reverse('api_stats')).status_code, 405)


class ClassifyTests(SimpleTestCase):
    ajax = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}

    def test_get_renders_form(self):
        response = self.client.get(reverse('classify'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-classify-form')

    def test_json_success(self):
        with stub_classifier(return_value='Positive'):
            response = self.client.post(reverse('classify'), {'text': ' great day '}, **self.ajax)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'prediction': 'Positive', 'text': 'great day'})

    def test_json_empty_text_is_400(self):
        response = self.client.post(reverse('classify'), {'text': '   '}, **self.ajax)
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.json())

    def test_json_missing_field_is_400(self):
        self.assertEqual(self.client.post(reverse('classify'), {}, **self.ajax).status_code, 400)

    def test_json_model_failure_is_500(self):
        with stub_classifier(side_effect=RuntimeError('no spark')):
            response = self.client.post(reverse('classify'), {'text': 'hello'}, **self.ajax)
        self.assertEqual(response.status_code, 500)
        self.assertIn('no spark', response.json()['error'])

    def test_plain_post_still_renders_result_page(self):
        with stub_classifier(return_value='Negative'):
            response = self.client.post(reverse('classify'), {'text': 'awful'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Negative')
        self.assertContains(response, 'awful')

    def test_plain_post_escapes_text(self):
        with stub_classifier(return_value='Neutral'):
            response = self.client.post(reverse('classify'), {'text': '<b>x</b>'})
        self.assertNotContains(response, '<b>x</b>')
