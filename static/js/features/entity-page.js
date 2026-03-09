/**
 * Entity page: load entity by ?id= or ?slug=, render title, description, metadata, related clusters and entities.
 * Depends on mock-entities.js (or future API).
 */
(function() {
  var container = document.getElementById('entityPageContent');
  var notFoundEl = document.getElementById('entityNotFound');
  if (!container) return;

  function escapeHtml(s) {
    if (!s) return '';
    var div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
  }

  function getEntity() {
    var params = new URLSearchParams(window.location.search);
    var id = params.get('id');
    var slug = params.get('slug');
    var mock = window.CyberNews && window.CyberNews.mockEntities;
    if (!mock) return null;
    if (id) return mock.getEntityById(id);
    if (slug) return mock.getEntityBySlug(slug);
    return null;
  }

  function clusterLink(cluster) {
    if (!cluster) return '#';
    return 'category.html?category=' + encodeURIComponent(cluster.category || cluster.slug || cluster.id);
  }

  function entityLink(entity) {
    if (!entity) return '#';
    return '/pages/entity.html?id=' + encodeURIComponent(entity.id);
  }

  function render(entity) {
    var mock = window.CyberNews && window.CyberNews.mockEntities;
    var getTypeLabel = mock ? mock.getEntityTypeLabel.bind(mock) : function(t) { return t || 'Entity'; };
    var typeLabel = getTypeLabel(entity.type);

    var metaParts = [];
    if (entity.type) metaParts.push('<span class="entity-meta-item"><strong>Type</strong> ' + escapeHtml(typeLabel) + '</span>');
    if (entity.id) metaParts.push('<span class="entity-meta-item"><strong>ID</strong> ' + escapeHtml(entity.id) + '</span>');

    var relatedClustersHtml = '';
    if (entity.clusterIds && entity.clusterIds.length && mock && mock.clusters) {
      var clusters = entity.clusterIds.map(function(cid) { return mock.getClusterById(cid); }).filter(Boolean);
      if (clusters.length) {
        relatedClustersHtml =
          '<section class="entity-section">' +
            '<h2 class="entity-section-title"><i class="fas fa-layer-group"></i> Related Clusters</h2>' +
            '<div class="related-clusters-list">' +
            clusters.map(function(c) {
              return (
                '<a href="' + clusterLink(c) + '" class="related-cluster-card">' +
                  '<div class="cluster-name">' + escapeHtml(c.name) + '</div>' +
                  '<div class="cluster-summary">' + escapeHtml(c.summary || '') + '</div>' +
                '</a>'
              );
            }).join('') +
            '</div></section>';
      }
    }

    var relatedEntitiesHtml = '';
    if (entity.relatedEntityIds && entity.relatedEntityIds.length && mock) {
      var related = entity.relatedEntityIds.map(function(eid) { return mock.getEntityById(eid); }).filter(Boolean);
      if (related.length) {
        relatedEntitiesHtml =
          '<section class="entity-section">' +
            '<h2 class="entity-section-title"><i class="fas fa-link"></i> Related Entities</h2>' +
            '<div class="related-entities-list">' +
            related.map(function(e) {
              var shortDesc = (e.description || '').slice(0, 120);
              if (e.description && e.description.length > 120) shortDesc += '…';
              return (
                '<a href="' + entityLink(e) + '" class="related-entity-card">' +
                  '<div class="entity-name">' + escapeHtml(e.name) + '</div>' +
                  '<div class="entity-desc-short">' + escapeHtml(shortDesc) + '</div>' +
                '</a>'
              );
            }).join('') +
            '</div></section>';
      }
    }

    if (entity.name && document.title === 'Entity - CyberNews') {
      document.title = entity.name + ' - CyberNews';
    }
    container.innerHTML =
      '<a href="/pages/search.html" class="entity-back"><i class="fas fa-arrow-left"></i> Back to Search</a>' +
      '<header class="entity-header">' +
        '<h1 class="entity-title">' + escapeHtml(entity.name) + '</h1>' +
        '<span class="entity-type-badge">' + escapeHtml(typeLabel) + '</span>' +
        '<div class="entity-description">' + escapeHtml(entity.description || '') + '</div>' +
        (metaParts.length ? '<div class="entity-meta">' + metaParts.join('') + '</div>' : '') +
      '</header>' +
      relatedClustersHtml +
      relatedEntitiesHtml;
    container.hidden = false;
    if (notFoundEl) notFoundEl.hidden = true;
  }

  function showNotFound() {
    container.hidden = true;
    if (notFoundEl) {
      notFoundEl.hidden = false;
      notFoundEl.innerHTML =
        '<div class="state-icon"><i class="fas fa-question-circle"></i></div>' +
        '<div class="state-title">Entity not found</div>' +
        '<div class="state-message">The requested entity may not exist or the link may be invalid.</div>' +
        '<a href="search.html" class="entity-back"><i class="fas fa-arrow-left"></i> Back to Search</a>';
    }
  }

  function init() {
    var entity = getEntity();
    if (entity) render(entity);
    else showNotFound();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
