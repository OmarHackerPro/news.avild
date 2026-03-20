/**
 * News card click: navigates to source URL or cluster detail page (depends on features/news-grid)
 */
(function() {
  'use strict';

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

      // When FE-04 (cluster detail page) is built, always navigate to clusterUrl instead
      if (cluster && cluster.top_article && cluster.top_article.source_url) {
        window.open(cluster.top_article.source_url, '_blank', 'noopener');
      } else {
        window.location.href = clusterUrl;
      }
    });
  }
})();
