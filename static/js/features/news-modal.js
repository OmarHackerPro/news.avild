/**
 * News card click: opens the cluster drawer for the clicked card.
 * Requires cluster-drawer.js to be loaded first (see loader.js).
 */
(function () {
  'use strict';

  var newsGrid = document.getElementById('newsGrid');
  if (!newsGrid) return;

  newsGrid.addEventListener('click', function (e) {
    var card = e.target && e.target.closest ? e.target.closest('.news-card') : null;
    if (!card) return;
    var clusterId = card.getAttribute('data-cluster-id') || '';
    if (!clusterId) return;
    if (window.ClusterDrawer) window.ClusterDrawer.open(clusterId);
  });
})();
