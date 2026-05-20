/**
 * Entity page: load entity by ?id=, render title, type, description, aliases,
 * CVSS score, and linked articles from /api/entities/{id}.
 */
(function() {
  var container = document.getElementById('entityPageContent');
  var notFoundEl = document.getElementById('entityNotFound');
  var loadingEl = document.getElementById('entityLoading');
  var errorEl = document.getElementById('entityError');
  var errorMsgEl = document.getElementById('entityErrorMsg');
  var retryBtn = document.getElementById('entityRetryBtn');
  if (!container) return;

  var TYPE_LABELS = {
    cve: 'CVE', vendor: 'Vendor', product: 'Product',
    actor: 'Threat Actor', malware: 'Malware', tool: 'Tool',
    campaign: 'Campaign', vuln_alias: 'Vulnerability',
  };

  function showState(state) {
    if (loadingEl)  loadingEl.hidden  = (state !== 'loading');
    if (errorEl)    errorEl.hidden    = (state !== 'error');
    if (notFoundEl) notFoundEl.hidden = (state !== 'notfound');
    if (container)  container.hidden  = (state !== 'content');
  }

  function escapeHtml(s) {
    if (!s) return '';
    var div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
  }

  function timeAgo(isoStr) {
    if (!isoStr) return '';
    var diff = (Date.now() - new Date(isoStr).getTime()) / 1000;
    if (diff < 0) diff = 0;
    if (diff < 60) return Math.floor(diff) + 's ago';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    return Math.floor(diff / 86400) + 'd ago';
  }

  function render(entity) {
    var typeLabel = TYPE_LABELS[entity.type] || entity.type || 'Entity';

    var cvssHtml = entity.cvss_score != null
      ? '<span class="entity-cvss-badge">CVSS ' + Number(entity.cvss_score).toFixed(1) + '</span>'
      : '';

    var aliasesHtml = '';
    if (entity.aliases && entity.aliases.length) {
      aliasesHtml =
        '<section class="entity-section">' +
          '<h2 class="entity-section-title">Also known as</h2>' +
          '<div class="entity-aliases">' +
            entity.aliases.map(function(a) {
              return '<span class="entity-alias-chip">' + escapeHtml(a) + '</span>';
            }).join('') +
          '</div>' +
        '</section>';
    }

    var metaParts = [
      entity.first_seen
        ? '<span class="entity-meta-item"><strong>First seen</strong> ' + timeAgo(entity.first_seen) + '</span>'
        : null,
      entity.last_seen
        ? '<span class="entity-meta-item"><strong>Last seen</strong> ' + timeAgo(entity.last_seen) + '</span>'
        : null,
      entity.article_count != null
        ? '<span class="entity-meta-item"><strong>Articles</strong> ' + entity.article_count + '</span>'
        : null,
    ].filter(Boolean);

    var articlesHtml = '';
    if (entity.articles && entity.articles.length) {
      articlesHtml =
        '<section class="entity-section">' +
          '<h2 class="entity-section-title"><i class="fas fa-newspaper"></i> Related Articles</h2>' +
          '<div class="related-articles-list">' +
            entity.articles.map(function(a) {
              var meta = [
                a.source_name ? escapeHtml(a.source_name) : '',
                a.published_at ? timeAgo(a.published_at) : '',
              ].filter(Boolean).join(' · ');
              return (
                '<a href="' + escapeHtml(a.source_url || '#') + '" target="_blank" rel="noopener" class="related-article-row">' +
                  '<div class="article-title">' + escapeHtml(a.title || '') + '</div>' +
                  (meta ? '<div class="article-meta">' + meta + '</div>' : '') +
                '</a>'
              );
            }).join('') +
          '</div>' +
        '</section>';
    }

    container.innerHTML =
      '<a href="/search" class="entity-back"><i class="fas fa-arrow-left"></i> Back to Search</a>' +
      '<header class="entity-header">' +
        '<h1 class="entity-title">' + escapeHtml(entity.name || '') + '</h1>' +
        '<span class="entity-type-badge">' + escapeHtml(typeLabel) + '</span>' +
        cvssHtml +
        (entity.description
          ? '<div class="entity-description">' + escapeHtml(entity.description) + '</div>'
          : '<div class="entity-description entity-description--empty">No description available yet.</div>') +
        (metaParts.length ? '<div class="entity-meta">' + metaParts.join('') + '</div>' : '') +
      '</header>' +
      aliasesHtml +
      articlesHtml;

    showState('content');
  }

  function showNotFound() {
    if (notFoundEl) {
      notFoundEl.innerHTML =
        '<div class="state-block-icon"><i class="fas fa-question-circle"></i></div>' +
        '<p class="state-block-title">Entity not found</p>' +
        '<p class="state-block-message">The requested entity may not exist or the link may be invalid.</p>' +
        '<div class="state-block-actions">' +
          '<a href="/search" class="state-block-btn"><i class="fas fa-arrow-left"></i> Back to Search</a>' +
        '</div>';
      notFoundEl.classList.add('state-block', 'is-empty');
    }
    showState('notfound');
  }

  function showError(message) {
    if (errorMsgEl && message) errorMsgEl.textContent = message;
    showState('error');
  }

  async function init() {
    showState('loading');
    var params = new URLSearchParams(window.location.search);
    var id = params.get('id');
    if (!id) { showNotFound(); return; }
    try {
      var resp = await fetch('/api/entities/' + encodeURIComponent(id));
      if (resp.status === 404) { showNotFound(); return; }
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      var entity = await resp.json();
      render(entity);
    } catch (e) {
      showError(e && e.message ? e.message : null);
    }
  }

  if (retryBtn) retryBtn.addEventListener('click', init);

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
