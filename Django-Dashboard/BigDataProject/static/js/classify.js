/* Classify panel: submits without a page reload, shows a loading state while
   Spark works (the first request starts Spark and can take a while) and falls
   back to a normal form POST if JavaScript is unavailable. */
(function () {
    'use strict';

    var panel = document.querySelector('[data-classify-panel]');
    if (!panel) { return; }

    var form = panel.querySelector('[data-classify-form]');
    var textarea = form.querySelector('textarea');
    var button = form.querySelector('[data-classify-button]');
    var label = form.querySelector('[data-classify-label]');
    var counter = panel.querySelector('[data-char-count]');
    var result = panel.querySelector('[data-classify-result]');
    var busy = false;

    function updateCount() { counter.textContent = textarea.value.length; }

    function card(className, sentiment) {
        var node = document.createElement('div');
        node.className = 'result-card' + (className ? ' ' + className : '');
        if (sentiment) { node.setAttribute('data-sentiment', sentiment); }
        return node;
    }

    function showError(message) {
        var node = card('is-error');
        node.textContent = message;
        result.replaceChildren(node);
    }

    function showResult(prediction, seconds) {
        var node = card('', prediction);
        var eyebrow = document.createElement('div');
        eyebrow.className = 'result-eyebrow';
        eyebrow.textContent = 'Predicted sentiment';
        var value = document.createElement('div');
        value.className = 'result-value';
        value.textContent = prediction;
        var time = document.createElement('div');
        time.className = 'result-time';
        time.textContent = 'Processed in ' + seconds.toFixed(1) + ' s';
        node.append(eyebrow, value, time);
        result.replaceChildren(node);
    }

    function setBusy(isBusy) {
        busy = isBusy;
        button.classList.toggle('is-loading', isBusy);
        button.setAttribute('aria-busy', isBusy ? 'true' : 'false');
        label.textContent = isBusy ? 'Classifying…' : 'Classify';
    }

    function submit(event) {
        if (event) { event.preventDefault(); }
        if (busy) { return; }
        if (!textarea.value.trim()) {
            showError('Enter some text to classify.');
            textarea.focus();
            return;
        }

        setBusy(true);
        var started = performance.now();
        fetch(form.action, {
            method: 'POST',
            headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' },
            body: new FormData(form),
            credentials: 'same-origin'
        }).then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (data) {
                if (!response.ok) {
                    throw new Error(data.error || 'Something went wrong. Try again.');
                }
                return data;
            });
        }).then(function (data) {
            showResult(data.prediction, (performance.now() - started) / 1000);
        }).catch(function (err) {
            showError(err.message || 'Could not reach the server.');
        }).then(function () {
            setBusy(false);
        });
    }

    form.addEventListener('submit', submit);
    textarea.addEventListener('input', updateCount);
    textarea.addEventListener('keydown', function (event) {
        if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { submit(event); }
    });

    panel.querySelectorAll('[data-sample]').forEach(function (sample) {
        sample.addEventListener('click', function () {
            textarea.value = sample.getAttribute('data-sample');
            updateCount();
            textarea.focus();
        });
    });

    updateCount();
})();
