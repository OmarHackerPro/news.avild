/**
 * RSS config page: include options, filters, feed URL with token, copy, regenerate token.
 */
(function() {
  'use strict';

  var urlInput = document.getElementById('rssFeedUrl');
  var copyBtn = document.getElementById('rssCopyUrl');
  var regenBtn = document.getElementById('rssRegenerateToken');
  var includeCheckboxes = document.querySelectorAll('#rssInclude input[name="include"]');
  var filterTags = document.getElementById('rssFilterTags');
  var filterSources = document.getElementById('rssFilterSources');
  var filterFreq = document.getElementById('rssFilterFreq');
  var filterLang = document.getElementById('rssFilterLang');

  var api = window.CyberNews && window.CyberNews.rssConfig;
  if (!api) return;

  function t(key) {
    var dict = window.CyberNews && window.CyberNews.getDict && window.CyberNews.getDict();
    return (dict && dict[key]) || key;
  }

  function getInclude() {
    var out = [];
    includeCheckboxes.forEach(function(cb) {
      if (cb.checked) out.push(cb.value);
    });
    return out;
  }

  function getFilters() {
    return {
      tags: filterTags ? filterTags.value : '',
      sources: filterSources ? filterSources.value : '',
      frequency: filterFreq ? filterFreq.value : '',
      lang: filterLang ? filterLang.value : ''
    };
  }

  function updateUrl() {
    var token = api.getOrCreateToken();
    var data = api.load();
    data.include = getInclude();
    data.filters = getFilters();
    api.save(data);
    var url = api.buildFeedUrl(token, data.filters);
    if (urlInput) urlInput.value = url;
  }

  function trackEvent(props) {
    if (window.CyberNews && window.CyberNews.analytics) {
      window.CyberNews.analytics.track('export_click', props);
    }
  }

  if (copyBtn) {
    copyBtn.addEventListener('click', function() {
      if (!urlInput || !urlInput.value) return;
      navigator.clipboard.writeText(urlInput.value).then(function() {
        copyBtn.textContent = t('rss.copied');
        setTimeout(function() { copyBtn.textContent = t('rss.copyUrl'); }, 2000);
      });
      trackEvent({
        kind: 'rss_copy_url',
        source: 'rss_config',
        include: getInclude(),
        filters: getFilters(),
      });
    });
  }

  if (regenBtn) {
    regenBtn.addEventListener('click', function() {
      api.regenerateToken();
      updateUrl();
      trackEvent({ kind: 'rss_regenerate_token', source: 'rss_config' });
    });
  }

  includeCheckboxes.forEach(function(cb) {
    cb.addEventListener('change', updateUrl);
  });
  if (filterTags) filterTags.addEventListener('change', updateUrl);
  if (filterSources) filterSources.addEventListener('change', updateUrl);
  if (filterFreq) filterFreq.addEventListener('change', updateUrl);
  if (filterLang) filterLang.addEventListener('change', updateUrl);

  updateUrl();

  document.body.addEventListener('cybernews:languageChange', function() {
    if (regenBtn) regenBtn.textContent = t('rss.regenerateToken');
    if (copyBtn) copyBtn.textContent = t('rss.copyUrl');
  });
})();
