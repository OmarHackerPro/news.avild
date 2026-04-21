/**
 * Lightweight translation helper using MyMemory free API.
 * Caches results in localStorage to avoid repeat calls.
 */
(function () {
  'use strict';

  var CACHE_PREFIX = 'txc_';
  var SUPPORTED = { az: true, ru: true, es: true, fr: true, de: true, ja: true, zh: true, ar: true, tr: true };

  function cacheKey(text, lang) {
    // Simple key: lang + first 80 chars of text (good enough for article titles/descs)
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

    var url = 'https://api.mymemory.translated.net/get?q=' +
      encodeURIComponent(text.slice(0, 500)) + '&langpair=en|' + lang;

    return fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var translated = (data && data.responseData && data.responseData.translatedText) || text;
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
