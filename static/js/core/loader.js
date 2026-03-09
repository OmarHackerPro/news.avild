/**
 * Loads page from partials (layout/ + components/) and scripts in dependency order.
 * Served by FastAPI — run: uvicorn main:app --reload
 */
(function() {
  var partialsBase = '/static/partials/';
  var jsBase = '/static/js/';

  function fetchHtml(path) {
    return fetch(partialsBase + path).then(function(r) {
      if (!r.ok) throw new Error(path);
      return r.text();
    });
  }

  function loadScript(src) {
    return new Promise(function(resolve, reject) {
      var s = document.createElement('script');
      s.src = jsBase + src;
      s.async = false;
      s.onload = resolve;
      s.onerror = reject;
      document.body.appendChild(s);
    });
  }

  function loadScriptsInOrder(list) {
    return list.reduce(function(p, src) {
      return p.then(function() { return loadScript(src); });
    }, Promise.resolve());
  }

  function injectPartials() {
    return Promise.all([
      fetchHtml('layout/content.html'),
      fetchHtml('layout/sidebar.html'),
      fetchHtml('components/modals.html')
    ]).then(function(parts) {
      var contentHtml = parts[0].trim();
      var sidebarHtml = parts[1].trim();
      var modalsHtml = parts[2].trim();

      var main = document.createElement('main');
      main.className = 'layout';

      var contentArea = document.createElement('div');
      contentArea.className = 'content-area';
      contentArea.innerHTML = contentHtml;
      main.appendChild(contentArea);

      var sidebarWrap = document.createElement('div');
      sidebarWrap.innerHTML = sidebarHtml;
      var sidebar = sidebarWrap.firstElementChild;
      if (sidebar) main.appendChild(sidebar);

      var loaderScript = document.querySelector('script[src*="loader"]');
      if (loaderScript) {
        document.body.insertBefore(main, loaderScript);
      } else {
        document.body.appendChild(main);
      }

      var modalsWrap = document.createElement('div');
      modalsWrap.innerHTML = modalsHtml;
      while (modalsWrap.firstChild) document.body.appendChild(modalsWrap.firstChild);

      // Apply category filter after content is injected
      setTimeout(function() {
        if (typeof window.applyCategoryFilter === 'function') {
          window.applyCategoryFilter();
        }
      }, 50);

      var scriptOrder = [
        'data/translations.js',
        'components/nav.js',
        'components/language.js',
        'components/theme.js',
        'components/rss.js',
        'components/search-tooltip.js',
        'components/filters.js',
        'features/category-filter.js',
        'features/priority-filter.js',
        'features/news-grid.js',
        'features/news-modal.js',
        'features/share-modal.js',
        'features/breaking.js',
        'features/sidebar.js'
      ];
      return loadScriptsInOrder(scriptOrder).then(function() {
        // Ensure category filter runs after all scripts are loaded
        if (typeof window.applyCategoryFilter === 'function') {
          window.applyCategoryFilter();
        }
      });
    });
  }

  injectPartials().catch(function(err) {
    document.body.innerHTML = '<p style="padding:2rem;color:#f85149;">Could not load page. Start the server: <code>uvicorn main:app --reload</code></p>';
    console.error(err);
  });
})();
