/**
 * News card click: navigates to source URL or cluster detail page (depends on features/news-grid)
 */
(function() {
  'use strict';
  var newsModal = document.getElementById('newsModal');
  var newsModalContent = newsModal ? newsModal.querySelector('.news-modal__content') : null;
  var modalOpen = false;

  function escapeHtml(str) {
    return String(str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function makeDetailsText(item) {
    if (item && item.details) return String(item.details);
    var title = item && item.title ? item.title : 'Untitled';
    var desc = item && item.desc ? item.desc : '';
    var keywords = (item && item.keywords) ? item.keywords.join(', ') : '';
    return (
      desc + '\n\nWhat happened:\n- Initial reports indicate active exploitation and ongoing incident response.\n- Analysts recommend prioritizing patching and monitoring for related IOCs.\n\nWhy it matters:\n- This affects common enterprise environments and could lead to lateral movement.\n\nRecommended actions:\n- Patch/mitigate immediately.\n- Review logs and EDR alerts.\n- Add detections for related indicators.\n\nKeywords: ' + keywords + '\nTitle: ' + title
    ).trim();
  }

  function openNewsModal(item) {
    if (!newsModal || !newsModalContent || !item) return;
    modalOpen = true;
    newsModal.setAttribute('aria-hidden', 'false');
    newsModal.classList.add('open');
    document.body.classList.add('no-scroll');

    var tags = (item.tags || []).map(function(t) {
      return '<span class="news-modal__chip">' + escapeHtml(t) + '</span>';
    }).join('');
    var keywords = (item.keywords || []).map(function(k) {
      return '<span class="news-modal__chip">' + escapeHtml(k) + '</span>';
    }).join('');
    var details = escapeHtml(makeDetailsText(item)).replace(/\n/g, '<br>');

    newsModalContent.innerHTML =
      '<div class="news-modal__meta">' +
        '<span><i class="far fa-clock"></i> ' + escapeHtml(item.time || '') + '</span>' +
        '<span><i class="fas fa-tag"></i> ' + escapeHtml(item.type || '') + '</span>' +
        (item.category ? '<span><i class="fas fa-layer-group"></i> ' + escapeHtml(item.category) + '</span>' : '') +
      '</div>' +
      '<h2 class="news-modal__title" id="newsModalTitle">' + escapeHtml(item.title || '') + '</h2>' +
      (item.desc ? '<p class="card-desc">' + escapeHtml(item.desc) + '</p>' : '') +
      (tags ? '<div class="news-modal__section-title">Tags</div><div class="news-modal__chips">' + tags + '</div>' : '') +
      (keywords ? '<div class="news-modal__section-title">Keywords</div><div class="news-modal__chips">' + keywords + '</div>' : '') +
      '<div class="news-modal__section-title">Full details</div><div class="news-modal__body">' + details + '</div>';

    var closeBtn = newsModal.querySelector('.news-modal__close');
    if (closeBtn) closeBtn.focus();
  }

  function closeNewsModal() {
    if (!newsModal) return;
    modalOpen = false;
    newsModal.setAttribute('aria-hidden', 'true');
    newsModal.classList.remove('open');
    document.body.classList.remove('no-scroll');
    if (newsModalContent) newsModalContent.innerHTML = '';
  }

  var newsGrid = document.getElementById('newsGrid');
  if (newsGrid) {
    newsGrid.addEventListener('click', function(e) {
      var card = e.target && e.target.closest ? e.target.closest('.news-card') : null;
      if (!card) return;
      e.preventDefault();
      var clusterId = card.getAttribute('data-cluster-id') || '';
      if (!clusterId) return;

      // Try cluster detail page first; fallback to top article source URL
      var list = window.loadedClusterList || [];
      var cluster = list.find(function(x) { return (x.id || '') === clusterId; });
      var clusterUrl = '/cluster/' + encodeURIComponent(clusterId);

      // Check if cluster detail page exists (FE-04). For now, fallback to source_url.
      if (cluster && cluster.top_article && cluster.top_article.source_url) {
        window.open(cluster.top_article.source_url, '_blank', 'noopener');
      } else {
        window.location.href = clusterUrl;
      }
    });
  }

  if (newsModal) {
    newsModal.addEventListener('click', function(e) {
      var closeTarget = e.target && e.target.closest ? e.target.closest('[data-modal-close="true"]') : null;
      if (closeTarget) closeNewsModal();
    });
  }

  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && modalOpen) closeNewsModal();
  });
})();
