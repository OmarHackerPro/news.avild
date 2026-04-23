/**
 * Lightweight translation helper — proxied through /api/translate (server-side Google Translate).
 * Caches results in localStorage to avoid repeat calls.
 */
(function () {
  'use strict';

  var CACHE_PREFIX = 'txc_';
  var SUPPORTED = { az: true, ru: true, es: true, fr: true, de: true, ja: true, zh: true, ar: true, tr: true };

  // Purge any poisoned cache entries left over from the old MyMemory backend
  (function purgePoison() {
    try {
      var toDelete = [];
      for (var i = 0; i < localStorage.length; i++) {
        var k = localStorage.key(i);
        if (k && k.indexOf(CACHE_PREFIX) === 0) {
          var v = localStorage.getItem(k);
          if (v && v.toUpperCase().indexOf('MYMEMORY') !== -1) toDelete.push(k);
        }
      }
      toDelete.forEach(function (k) { localStorage.removeItem(k); });
    } catch (e) {}
  }());

  function cacheKey(text, lang) {
    return CACHE_PREFIX + lang + '_' + text.slice(0, 80);
  }

  function getCached(text, lang) {
    try { return localStorage.getItem(cacheKey(text, lang)); } catch (e) { return null; }
  }

  function setCached(text, lang, result) {
    try { localStorage.setItem(cacheKey(text, lang), result); } catch (e) {}
  }

  // Translate a single string. Returns a Promise<string>.
  function translateOne(text, lang) {
    if (!text || !text.trim()) return Promise.resolve(text);
    if (!SUPPORTED[lang]) return Promise.resolve(text);

    var cached = getCached(text, lang);
    if (cached !== null) return Promise.resolve(cached);

    var url = '/api/translate?lang=' + encodeURIComponent(lang) + '&q=' + encodeURIComponent(text.slice(0, 500));

    return fetch(url)
      .then(function (r) {
        if (!r.ok) return null;
        return r.json();
      })
      .then(function (data) {
        var translated = data && data.translated;
        if (!translated) return text;
        setCached(text, lang, translated);
        return translated;
      })
      .catch(function () { return text; });
  }

  // Translate an array of strings concurrently. Returns Promise<string[]>.
  function translateBatch(texts, lang) {
    return Promise.all(texts.map(function (t) { return translateOne(t, lang); }));
  }

  window.Translator = { translateOne: translateOne, translateBatch: translateBatch, isSupported: function (l) { return !!SUPPORTED[l]; } };
})();
