(function() {
  'use strict';

  var STORAGE_KEY = 'digestSubscription';
  var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

  var TOPIC_LABELS = {
    'breaking': 'Breaking',
    'threat-intel': 'Threat Intel',
    'malware': 'Malware',
    'apt': 'APT',
    'breaches': 'Breaches',
    'pentest': 'Pentest',
    'bug-bounty': 'Bug Bounty',
  };

  var els = {};

  document.addEventListener('DOMContentLoaded', function() {
    els.form = document.getElementById('digestForm');
    els.email = document.getElementById('digestEmail');
    els.emailRow = els.email ? els.email.closest('.digest-email-row') : null;
    els.emailError = document.getElementById('digestEmailError');
    els.subscribeBtn = document.getElementById('digestSubscribeBtn');
    els.subscribeLabel = document.getElementById('digestSubscribeLabel');
    els.previewBtn = document.getElementById('digestPreviewBtn');
    els.toast = document.getElementById('digestToast');
    els.statusCard = document.getElementById('digestStatusCard');
    els.statusEmail = document.getElementById('digestStatusEmail');
    els.statusMeta = document.getElementById('digestStatusMeta');
    els.unsubscribeBtn = document.getElementById('digestUnsubscribeBtn');
    els.editBtn = document.getElementById('digestEditBtn');
    els.cancelEditBtn = document.getElementById('digestCancelEditBtn');
    els.subscribeSection = document.getElementById('digestSubscribeSection');
    els.previewSection = document.getElementById('digestPreviewSection');
    els.previewBadge = document.getElementById('digestPreviewBadge');
    els.previewSubject = document.getElementById('digestPreviewSubject');
    els.previewArticles = document.getElementById('digestPreviewArticles');
    els.previewEmailEcho = document.getElementById('digestPreviewEmailEcho');

    if (!els.form) return;

    var existing = loadSubscription();
    if (existing) hydrateForm(existing);
    renderStatus(existing);
    setEditMode(!existing);
    renderPreview();

    els.form.addEventListener('submit', handleSubmit);
    els.form.addEventListener('change', renderPreview);
    els.form.addEventListener('input', onFormInput);
    if (els.previewBtn) els.previewBtn.addEventListener('click', function() {
      renderPreview();
      flash(els.previewBtn);
    });
    if (els.unsubscribeBtn) els.unsubscribeBtn.addEventListener('click', handleUnsubscribe);
    if (els.editBtn) els.editBtn.addEventListener('click', function() {
      setEditMode(true);
      if (els.email) els.email.focus();
    });
    if (els.cancelEditBtn) els.cancelEditBtn.addEventListener('click', function() {
      var current = loadSubscription();
      if (current) hydrateForm(current);
      setEmailError(false);
      renderPreview();
      setEditMode(false);
    });
  });

  function setEditMode(editing) {
    var subscribed = !!loadSubscription();
    if (els.subscribeSection) els.subscribeSection.hidden = subscribed && !editing;
    if (els.previewSection) els.previewSection.hidden = subscribed && !editing;
    if (els.cancelEditBtn) els.cancelEditBtn.hidden = !subscribed || !editing;
    if (els.subscribeLabel) {
      els.subscribeLabel.textContent = subscribed ? 'Update subscription' : 'Subscribe';
    }
  }

  function loadSubscription() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      var parsed = JSON.parse(raw);
      if (!parsed || !parsed.email) return null;
      return parsed;
    } catch (e) {
      return null;
    }
  }

  function saveSubscription(sub) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(sub));
    } catch (e) {}
  }

  function clearSubscription() {
    try { localStorage.removeItem(STORAGE_KEY); } catch (e) {}
  }

  function hydrateForm(sub) {
    if (sub.email) els.email.value = sub.email;
    if (sub.frequency) {
      var freqEl = els.form.querySelector('input[name="frequency"][value="' + sub.frequency + '"]');
      if (freqEl) freqEl.checked = true;
    }
    var topicBoxes = els.form.querySelectorAll('input[name="topics"]');
    if (Array.isArray(sub.topics)) {
      topicBoxes.forEach(function(cb) { cb.checked = sub.topics.indexOf(cb.value) !== -1; });
    }
    if (sub.minSeverity) {
      var sev = document.getElementById('digestMinSeverity');
      if (sev) sev.value = sub.minSeverity;
    }
  }

  function readForm() {
    var fd = new FormData(els.form);
    var topics = [];
    fd.getAll('topics').forEach(function(v) { topics.push(String(v)); });
    return {
      email: (fd.get('email') || '').toString().trim(),
      frequency: (fd.get('frequency') || 'daily').toString(),
      topics: topics,
      minSeverity: (fd.get('minSeverity') || 'all').toString(),
    };
  }

  function onFormInput(e) {
    if (e.target === els.email) setEmailError(false);
  }

  function handleSubmit(e) {
    e.preventDefault();
    var data = readForm();
    if (!EMAIL_RE.test(data.email)) {
      setEmailError(true);
      els.email.focus();
      return;
    }
    if (data.topics.length === 0) {
      showToast('Pick at least one topic to include in your digest.', 'error');
      return;
    }
    var wasSubscribed = !!loadSubscription();
    var sub = Object.assign({}, data, { subscribedAt: new Date().toISOString() });
    saveSubscription(sub);
    renderStatus(sub);
    setEditMode(false);
    renderPreview();
    showToast(wasSubscribed ? 'Subscription updated.' : 'Subscribed! Digest settings saved locally in your browser.', 'success');
  }

  function handleUnsubscribe() {
    clearSubscription();
    renderStatus(null);
    setEditMode(true);
    showToast('You have been unsubscribed.', 'success');
  }

  function setEmailError(show) {
    if (!els.emailError || !els.emailRow) return;
    els.emailError.hidden = !show;
    els.emailRow.classList.toggle('is-invalid', !!show);
  }

  function showToast(message, kind) {
    if (!els.toast) return;
    els.toast.textContent = message;
    els.toast.hidden = false;
    els.toast.classList.remove('is-success', 'is-error');
    els.toast.classList.add(kind === 'error' ? 'is-error' : 'is-success');
    clearTimeout(els.toast._timer);
    els.toast._timer = setTimeout(function() { els.toast.hidden = true; }, 4000);
  }

  function renderStatus(sub) {
    if (!els.statusCard) return;
    if (!sub) {
      els.statusCard.hidden = true;
      if (els.subscribeLabel) els.subscribeLabel.textContent = 'Subscribe';
      return;
    }
    els.statusCard.hidden = false;
    if (els.statusEmail) els.statusEmail.textContent = sub.email;
    var parts = [];
    parts.push(capitalize(sub.frequency) + ' digest');
    if (Array.isArray(sub.topics) && sub.topics.length) {
      parts.push(sub.topics.length + ' ' + (sub.topics.length === 1 ? 'topic' : 'topics'));
    }
    if (sub.minSeverity && sub.minSeverity !== 'all') {
      parts.push(capitalize(sub.minSeverity) + '+ severity');
    }
    if (els.statusMeta) els.statusMeta.textContent = parts.join(' · ');
    if (els.subscribeLabel) els.subscribeLabel.textContent = 'Update subscription';
  }

  function renderPreview() {
    var data = readForm();
    if (els.previewBadge) {
      els.previewBadge.textContent = data.frequency === 'weekly' ? 'Weekly Digest' : 'Daily Digest';
    }
    if (els.previewEmailEcho) {
      els.previewEmailEcho.textContent = data.email && EMAIL_RE.test(data.email) ? data.email : 'your email';
    }
    if (els.previewSubject) {
      var count = mockArticleCount(data);
      var topic = data.topics[0] ? (TOPIC_LABELS[data.topics[0]] || data.topics[0]) : 'security';
      var freqWord = data.frequency === 'weekly' ? 'weekly' : 'daily';
      els.previewSubject.textContent = 'Your ' + freqWord + ' ' + topic.toLowerCase() + ' digest — ' + count + ' ' + (count === 1 ? 'story' : 'stories') + ' to read';
    }
    if (els.previewArticles) {
      var articles = buildMockArticles(data);
      if (articles.length === 0) {
        els.previewArticles.innerHTML = '<li class="digest-preview-empty">No topics selected — pick at least one to see a preview.</li>';
      } else {
        els.previewArticles.innerHTML = articles.map(function(a) {
          return (
            '<li class="digest-preview-article">' +
              '<span class="digest-preview-sev sev-' + escHtml(a.sev) + '">' + capitalize(a.sev) + '</span>' +
              '<div>' +
                '<p class="digest-preview-title">' + escHtml(a.title) + '</p>' +
                '<p class="digest-preview-meta">' + escHtml(a.topic) + ' · ' + escHtml(a.ago) + '</p>' +
              '</div>' +
            '</li>'
          );
        }).join('');
      }
    }
  }

  function buildMockArticles(data) {
    var SAMPLES = {
      'breaking': { sev: 'critical', title: 'Zero-day exploited in widely used VPN appliance', ago: '2h ago' },
      'threat-intel': { sev: 'high', title: 'State-backed actor targets telecom providers in EU', ago: '4h ago' },
      'malware': { sev: 'high', title: 'New ransomware family targets healthcare sector', ago: '5h ago' },
      'apt': { sev: 'critical', title: 'APT group shifts tactics, uses signed drivers', ago: '7h ago' },
      'breaches': { sev: 'medium', title: 'Major breach disclosed — 4M records exposed', ago: '9h ago' },
      'pentest': { sev: 'medium', title: 'New auth bypass chain in popular CMS plugin', ago: '11h ago' },
      'bug-bounty': { sev: 'low', title: 'Researcher earns $25k for SSRF in cloud console', ago: '1d ago' },
    };
    var sevRank = { 'low': 1, 'medium': 2, 'high': 3, 'critical': 4 };
    var min = data.minSeverity === 'all' ? 0 : (sevRank[data.minSeverity] || 0);
    var out = [];
    data.topics.forEach(function(topic) {
      var sample = SAMPLES[topic];
      if (!sample) return;
      if (sevRank[sample.sev] < min) return;
      out.push({ sev: sample.sev, title: sample.title, topic: TOPIC_LABELS[topic] || topic, ago: sample.ago });
    });
    return out.slice(0, 5);
  }

  function mockArticleCount(data) {
    return buildMockArticles(data).length;
  }

  function capitalize(s) {
    if (!s) return '';
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  function flash(btn) {
    if (!btn) return;
    btn.style.transform = 'scale(0.97)';
    setTimeout(function() { btn.style.transform = ''; }, 120);
  }

  function escHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }
})();
