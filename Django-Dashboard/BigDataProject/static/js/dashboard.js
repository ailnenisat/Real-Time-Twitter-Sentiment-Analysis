/* Dashboard: renders the stats payload (first paint comes from the page itself,
   later updates from /api/stats/) with plain DOM + SVG, no libraries. All
   tweet text goes through textContent, never innerHTML. */
(function () {
    'use strict';

    var SVG_NS = 'http://www.w3.org/2000/svg';
    var POLL_MS = 5000;
    var PAGE_SIZE = 10;
    var COLORS = {
        Positive: '#16a34a',
        Negative: '#dc2626',
        Neutral: '#64748b',
        Irrelevant: '#d97706'
    };

    var $ = function (selector) { return document.querySelector(selector); };
    var el = {
        pill: $('[data-live-pill]'),
        pillText: $('[data-live-text]'),
        banner: $('[data-error-banner]'),
        empty: $('[data-empty-state]'),
        body: $('[data-report-body]'),
        kpis: $('[data-kpis]'),
        donut: $('[data-chart-donut]'),
        legend: $('[data-legend]'),
        trend: $('[data-chart-trend]'),
        trendLegend: $('[data-trend-legend]'),
        wordsTabs: $('[data-words-tabs]'),
        wordsList: $('[data-words-list]'),
        wordsNote: $('[data-words-note]'),
        tableBody: $('[data-table-body]'),
        tableNote: $('[data-table-note]'),
        pagePrev: $('[data-page-prev]'),
        pageNext: $('[data-page-next]'),
        pageInfo: $('[data-page-info]'),
        filterSentiment: $('[data-filter-sentiment]'),
        filterKeyword: $('[data-filter-keyword]'),
        autoRefresh: $('[data-auto-refresh]')
    };
    if (!el.body) { return; }

    var state = {
        stats: null,
        page: 1,
        wordsClass: null,
        lastText: null,
        lastUpdate: null,
        failed: false,
        timer: null
    };

    /* ---------- helpers ---------- */

    function svg(name, attrs, children) {
        var node = document.createElementNS(SVG_NS, name);
        Object.keys(attrs || {}).forEach(function (key) { node.setAttribute(key, attrs[key]); });
        (children || []).forEach(function (child) { node.appendChild(child); });
        return node;
    }

    function html(tag, className, text) {
        var node = document.createElement(tag);
        if (className) { node.className = className; }
        if (text !== undefined) { node.textContent = text; }
        return node;
    }

    function titled(node, text) {
        node.appendChild(svg('title')).textContent = text;
        return node;
    }

    function clear(node) {
        while (node.firstChild) { node.removeChild(node.firstChild); }
    }

    function formatNumber(value) { return Number(value).toLocaleString(); }
    function formatRate(value) { return Number(value).toFixed(1) + '%'; }

    function relativeTime(iso) {
        if (!iso) { return '—'; }
        var seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
        if (seconds < 5) { return 'just now'; }
        if (seconds < 60) { return seconds + 's ago'; }
        var minutes = Math.floor(seconds / 60);
        if (minutes < 60) { return minutes + 'm ago'; }
        var hours = Math.floor(minutes / 60);
        if (hours < 24) { return hours + 'h ago'; }
        return new Date(iso).toLocaleDateString();
    }

    function clockTime(iso) {
        return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }

    function legendItem(label, value) {
        var item = html('li');
        var swatch = html('span', 'swatch');
        swatch.style.background = COLORS[label];
        item.appendChild(swatch);
        item.appendChild(document.createTextNode(label + (value ? ' ' + value : '')));
        return item;
    }

    /* ---------- renderers ---------- */

    function renderKpis(stats) {
        clear(el.kpis);
        var tiles = [{ label: 'Total tweets', value: formatNumber(stats.total), sub: 'classified so far' }];
        stats.labels.forEach(function (label) {
            tiles.push({
                label: label,
                value: formatRate(stats.rates[label]),
                sub: formatNumber(stats.counts[label]) + ' tweets',
                sentiment: label
            });
        });
        tiles.forEach(function (tile) {
            var node = html('div', 'kpi');
            if (tile.sentiment) { node.setAttribute('data-sentiment', tile.sentiment); }
            node.appendChild(html('div', 'kpi-label', tile.label));
            node.appendChild(html('div', 'kpi-value', tile.value));
            node.appendChild(html('div', 'kpi-sub', tile.sub));
            el.kpis.appendChild(node);
        });
    }

    function renderDonut(stats) {
        var radius = 60;
        var circumference = 2 * Math.PI * radius;
        var group = svg('g', { transform: 'rotate(-90 80 80)', fill: 'none', 'stroke-width': 22 });
        group.appendChild(svg('circle', { cx: 80, cy: 80, r: radius, stroke: '#eef0f4' }));
        var offset = 0;
        stats.labels.forEach(function (label) {
            var count = stats.counts[label];
            if (!count || !stats.total) { return; }
            var length = count / stats.total * circumference;
            var segment = svg('circle', {
                cx: 80, cy: 80, r: radius, stroke: COLORS[label],
                'stroke-dasharray': length + ' ' + (circumference - length),
                'stroke-dashoffset': -offset
            });
            group.appendChild(titled(segment, label + ': ' + formatNumber(count) + ' (' + formatRate(stats.rates[label]) + ')'));
            offset += length;
        });
        var total = svg('text', { x: 80, y: 85, 'text-anchor': 'middle', 'font-size': 19, 'font-weight': 700, fill: '#1d2433' });
        total.textContent = formatNumber(stats.total);
        var chart = svg('svg', { viewBox: '0 0 160 160', role: 'img', 'aria-label': 'Sentiment share donut chart', style: 'max-width:200px;margin:0 auto' }, [group, total]);
        clear(el.donut);
        el.donut.appendChild(chart);

        clear(el.legend);
        stats.labels.forEach(function (label) {
            el.legend.appendChild(legendItem(label, formatRate(stats.rates[label])));
        });
    }

    function renderTrend(stats) {
        var trend = stats.trend;
        clear(el.trend);
        clear(el.trendLegend);
        if (!trend.starts.length) {
            el.trend.appendChild(html('p', 'muted', 'Not enough data yet.'));
            return;
        }
        var width = 400, height = 190, left = 28, right = 8, top = 10, bottom = 24;
        var plotW = width - left - right, plotH = height - top - bottom;
        var points = trend.starts.length;
        var max = 1;
        stats.labels.forEach(function (label) {
            trend.series[label].forEach(function (v) { if (v > max) { max = v; } });
        });
        var niceMax = Math.ceil(max / 4) * 4 || 4;
        var x = function (i) { return left + (points === 1 ? plotW / 2 : i * plotW / (points - 1)); };
        var y = function (v) { return top + plotH - v / niceMax * plotH; };

        var chart = svg('svg', { viewBox: '0 0 ' + width + ' ' + height, role: 'img', 'aria-label': 'Tweets per minute by sentiment' });
        for (var g = 0; g <= 4; g++) {
            var value = niceMax / 4 * g;
            chart.appendChild(svg('line', { x1: left, x2: width - right, y1: y(value), y2: y(value), stroke: '#eef0f4' }));
            var tick = svg('text', { x: left - 6, y: y(value) + 3.5, 'text-anchor': 'end', 'font-size': 10, fill: '#98a2b3' });
            tick.textContent = Math.round(value);
            chart.appendChild(tick);
        }
        [0, Math.floor((points - 1) / 2), points - 1].forEach(function (i, n, all) {
            if (n > 0 && i === all[n - 1]) { return; }
            var label = svg('text', {
                x: x(i), y: height - 6, 'font-size': 10, fill: '#98a2b3',
                'text-anchor': n === 0 ? 'start' : (n === all.length - 1 ? 'end' : 'middle')
            });
            label.textContent = clockTime(trend.starts[i]);
            chart.appendChild(label);
        });
        stats.labels.forEach(function (label) {
            var series = trend.series[label];
            var path = series.map(function (v, i) { return x(i).toFixed(1) + ',' + y(v).toFixed(1); }).join(' ');
            chart.appendChild(svg('polyline', {
                points: path, fill: 'none', stroke: COLORS[label], 'stroke-width': 2.5,
                'stroke-linecap': 'round', 'stroke-linejoin': 'round'
            }));
            // Invisible dots carry the hover tooltips.
            series.forEach(function (v, i) {
                var dot = svg('circle', { cx: x(i), cy: y(v), r: 6, fill: 'transparent' });
                chart.appendChild(titled(dot, label + ' · ' + clockTime(trend.starts[i]) + ': ' + v));
            });
            el.trendLegend.appendChild(legendItem(label));
        });
        el.trend.appendChild(chart);
    }

    function renderWords(stats) {
        if (!state.wordsClass || stats.labels.indexOf(state.wordsClass) === -1) {
            state.wordsClass = stats.labels[0];
        }
        clear(el.wordsTabs);
        stats.labels.forEach(function (label) {
            var tab = html('button', 'chip', label);
            tab.type = 'button';
            tab.setAttribute('role', 'tab');
            tab.setAttribute('aria-selected', label === state.wordsClass ? 'true' : 'false');
            tab.addEventListener('click', function () {
                state.wordsClass = label;
                renderWords(state.stats);
            });
            el.wordsTabs.appendChild(tab);
        });
        el.wordsNote.textContent = stats.sample_size < stats.total
            ? 'last ' + formatNumber(stats.sample_size) + ' tweets' : '';

        clear(el.wordsList);
        var words = stats.top_words[state.wordsClass] || [];
        if (!words.length) {
            el.wordsList.appendChild(html('li', 'muted', 'No words for this class yet.'));
            return;
        }
        var max = words[0].count || 1;
        words.forEach(function (entry) {
            var row = html('li');
            var head = html('div', 'bar-row-head');
            head.appendChild(html('span', '', entry.word));
            head.appendChild(html('span', '', formatNumber(entry.count)));
            var track = html('div', 'bar-track');
            var fill = html('div', 'bar-fill');
            fill.style.width = (entry.count / max * 100) + '%';
            fill.style.background = COLORS[state.wordsClass];
            track.appendChild(fill);
            row.appendChild(head);
            row.appendChild(track);
            el.wordsList.appendChild(row);
        });
    }

    function filteredTweets() {
        var wanted = el.filterSentiment.value;
        var keyword = el.filterKeyword.value.trim().toLowerCase();
        return state.stats.tweets.filter(function (tweet) {
            if (wanted && tweet.prediction !== wanted) { return false; }
            return !keyword || tweet.tweet.toLowerCase().indexOf(keyword) !== -1;
        });
    }

    function renderTable() {
        var stats = state.stats;
        var rows = filteredTweets();
        var pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
        if (state.page > pages) { state.page = pages; }
        var start = (state.page - 1) * PAGE_SIZE;

        clear(el.tableBody);
        if (!rows.length) {
            var empty = html('tr');
            var cell = html('td', 'empty-row', 'No tweets match these filters.');
            cell.colSpan = 3;
            empty.appendChild(cell);
            el.tableBody.appendChild(empty);
        }
        rows.slice(start, start + PAGE_SIZE).forEach(function (tweet) {
            var row = html('tr');
            row.appendChild(html('td', 'tweet', tweet.tweet));
            var sentiment = html('td');
            var badge = html('span', 'badge', tweet.prediction || 'Unknown');
            if (tweet.prediction) { badge.setAttribute('data-sentiment', tweet.prediction); }
            sentiment.appendChild(badge);
            row.appendChild(sentiment);
            var when = html('td', 'when', relativeTime(tweet.received));
            if (tweet.received) { when.title = new Date(tweet.received).toLocaleString(); }
            row.appendChild(when);
            el.tableBody.appendChild(row);
        });

        el.tableNote.textContent = stats.total > stats.tweets.length
            ? 'latest ' + formatNumber(stats.tweets.length) + ' of ' + formatNumber(stats.total) : '';
        el.pageInfo.textContent = rows.length
            ? (start + 1) + '–' + Math.min(start + PAGE_SIZE, rows.length) + ' of ' + formatNumber(rows.length)
            : '0 results';
        el.pagePrev.disabled = state.page <= 1;
        el.pageNext.disabled = state.page >= pages;
    }

    function render(stats) {
        state.stats = stats;
        var hasData = stats.total > 0;
        el.empty.hidden = hasData;
        el.body.hidden = !hasData;
        if (!hasData) { return; }
        renderKpis(stats);
        renderDonut(stats);
        renderTrend(stats);
        renderWords(stats);
        renderTable();
    }

    /* ---------- live status ---------- */

    function setPill(mode, text) {
        el.pill.setAttribute('data-state', mode);
        el.pillText.textContent = text;
    }

    function refreshPill() {
        if (!el.autoRefresh.checked) { setPill('paused', 'Auto-refresh off'); return; }
        if (state.failed) { setPill('offline', 'Offline · retrying'); return; }
        if (!state.lastUpdate) { setPill('connecting', 'Connecting…'); return; }
        var seconds = Math.round((Date.now() - state.lastUpdate) / 1000);
        setPill('live', 'Live · updated ' + (seconds < 2 ? 'just now' : seconds + 's ago'));
    }

    function showError(message) {
        el.banner.textContent = message;
        el.banner.hidden = !message;
    }

    /* ---------- polling ---------- */

    function fetchStats() {
        var controller = 'AbortController' in window ? new AbortController() : null;
        var timeout = controller ? setTimeout(function () { controller.abort(); }, 8000) : null;
        return fetch(window.SENTIMENT_STATS_URL, {
            headers: { 'Accept': 'application/json' },
            cache: 'no-store',
            signal: controller ? controller.signal : undefined
        }).then(function (response) {
            return response.text().then(function (text) {
                if (!response.ok) {
                    var message = 'Cannot load the latest data.';
                    try { message = JSON.parse(text).error || message; } catch (e) { /* not JSON */ }
                    throw new Error(message);
                }
                return text;
            });
        }).then(function (text) {
            if (timeout) { clearTimeout(timeout); }
            state.failed = false;
            state.lastUpdate = Date.now();
            showError('');
            // Re-render only when something actually changed.
            if (text !== state.lastText) {
                state.lastText = text;
                render(JSON.parse(text));
            } else if (state.stats && state.stats.total > 0) {
                renderTable();   // keeps the "5s ago" labels fresh
            }
        }).catch(function (err) {
            if (timeout) { clearTimeout(timeout); }
            state.failed = true;
            showError(err && err.name === 'AbortError' ? 'The server took too long to respond.' : (err.message || 'Cannot load the latest data.'));
        }).then(refreshPill);
    }

    function schedule() {
        clearTimeout(state.timer);
        if (!el.autoRefresh.checked || document.hidden) { return; }
        state.timer = setTimeout(function () {
            fetchStats().then(schedule);
        }, POLL_MS);
    }

    /* ---------- wiring ---------- */

    function init() {
        var initialStats = document.getElementById('initial-stats');
        var initialError = document.getElementById('initial-error');
        if (initialStats) {
            state.lastText = null;
            state.lastUpdate = Date.now();
            render(JSON.parse(initialStats.textContent));
        }
        if (initialError && JSON.parse(initialError.textContent)) {
            state.failed = true;
            showError(JSON.parse(initialError.textContent));
        }
        refreshPill();

        el.filterSentiment.addEventListener('change', function () { state.page = 1; if (state.stats) { renderTable(); } });
        el.filterKeyword.addEventListener('input', function () { state.page = 1; if (state.stats) { renderTable(); } });
        el.pagePrev.addEventListener('click', function () { state.page -= 1; renderTable(); });
        el.pageNext.addEventListener('click', function () { state.page += 1; renderTable(); });

        el.autoRefresh.addEventListener('change', function () {
            refreshPill();
            if (el.autoRefresh.checked) { fetchStats().then(schedule); } else { clearTimeout(state.timer); }
        });
        document.addEventListener('visibilitychange', function () {
            if (!document.hidden && el.autoRefresh.checked) { fetchStats().then(schedule); } else { clearTimeout(state.timer); }
        });

        setInterval(refreshPill, 1000);
        // First poll right away when the page had no data (or failed to load it).
        if (!initialStats || state.failed) { fetchStats().then(schedule); } else { schedule(); }
    }

    init();
})();
