/**
 * News grid: templates, filtering, infinite scroll (depends on data/translations, components/filters)
 */
(function() {
  'use strict';
  var translations = window.CyberNews && window.CyberNews.translations ? window.CyberNews.translations : { en: {} };

  var newsTemplates = [
    { id: 'card1',  tags: ['CISA', 'Zero-Day'],    title: 'CISA Adds Critical VPN Flaw to Known Exploited Catalog',    desc: 'Federal agencies must patch within two weeks as attacks escalate.',              keywords: ['CVE-2026-0001', 'VPN', 'RCE', 'Zero-Day'],         time: '15m',     severity: 'critical', type: 'advisory',  category: 'research',    access: 'link' },
    { id: 'card2',  tags: ['APT29', 'Breaches'],   title: 'APT29 Campaign Linked to Recent Government Breaches',       desc: 'Intelligence agencies attribute multiple incidents to same actor.',             keywords: ['APT29', 'Breach', 'Government'],                    time: '1h 10m',  severity: 'critical', type: 'analysis',  category: 'deep-dives' },
    { id: 'card3',  tags: ['Ransomware'],           title: 'New Ransomware Variant Targets Healthcare Sector',          desc: 'Hospitals and clinics report encrypted systems and ransom demands.',           keywords: ['Ransomware', 'Healthcare', 'Encryption'],           time: '2h 40m',  severity: 'high',     type: 'news',      category: 'research' },
    { id: 'card4',  tags: ['Zero-Day'],             title: 'Second Zero-Day in Same VPN Product Under Attack',          desc: 'Researchers confirm exploitation of an additional vulnerability in the same product.', keywords: ['Zero-Day', 'VPN', 'CVE'],                     time: '4h',      severity: 'high',     type: 'news',      category: 'research' },
    { id: 'card5',  tags: ['CISA'],                 title: 'Emergency Directive: Patch VPN Zero-Day by Friday',         desc: 'CISA orders federal agencies to apply vendor patches immediately.',             keywords: ['CISA', 'Directive', 'VPN'],                         time: '5h',      severity: 'high',     type: 'advisory',  category: 'research' },
    { id: 'card6',  tags: ['Mandiant', 'Report'],   title: 'APT41 Expands Supply Chain Attacks in 2026',                desc: 'New report details evolving TTPs and infrastructure used by the group.',       keywords: ['APT41', 'Supply Chain', 'Mandiant'],                time: '6h 30m',  severity: 'medium',   type: 'report',    category: 'deep-dives' },
    { id: 'card7',  tags: ['Malware'],              title: 'Stealer Malware Spreads via Fake Software Updates',         desc: 'Users tricked into installing trojanized installers from spoofed sites.',     keywords: ['Malware', 'Stealer', 'Fake Updates'],               time: '8h',      severity: 'medium',   type: 'news',      category: 'beginner' },
    { id: 'card8',  tags: ['Threat Intel'],         title: 'IOC Database Updated with Latest Campaign Signatures',      desc: 'New indicators of compromise available for detection rules.',                keywords: ['IOC', 'Threat Intel', 'Signatures'],                time: '10h',     severity: 'medium',   type: 'advisory',  category: 'research' },
    { id: 'card9',  tags: ['Bug Bounty'],           title: 'Major Bug Bounty Program Doubles Critical Payouts',         desc: 'Platform announces increased rewards for critical vulnerabilities.',           keywords: ['Bug Bounty', 'Payouts', 'Critical'],                time: '12h',     severity: 'low',      type: 'news',      category: 'beginner',  access: 'public' },
    { id: 'card10', tags: ['Pentest', 'Report'],    title: 'Penetration Testing Framework Updated for Cloud',           desc: 'New modules added for AWS, Azure, and GCP assessments.',                     keywords: ['Pentest', 'Cloud', 'AWS'],                          time: '14h',     severity: 'low',      type: 'report',    category: 'deep-dives' },
    { id: 'card11', tags: ['CISA'],                 title: 'CISA Releases Advisory on RDP Hardening',                  desc: 'Best practices to reduce risk of RDP-based attacks published.',               keywords: ['CISA', 'RDP', 'Hardening'],                         time: '17h',     severity: 'low',      type: 'advisory',  category: 'beginner' },
    { id: 'card12', tags: ['Breaches'],             title: 'Retail Giant Discloses Third-Party Data Exposure',          desc: 'Supplier breach may have exposed millions of customer records.',              keywords: ['Breach', 'Retail', 'Third-Party'],                  time: '20h',     severity: 'medium',   type: 'news',      category: 'dark-web' }
  ];

  var newsIndex = 0;
  var loadedNewsList = [];
  window.loadedNewsList = loadedNewsList;

  function getNextNewsItem() {
    var item = newsTemplates[newsIndex % newsTemplates.length];
    newsIndex++;
    return Object.assign({}, item);
  }

  function getSelectedFilterValue(dropdownId) {
    var dropdown = document.getElementById(dropdownId);
    if (!dropdown) return null;
    var sel = dropdown.querySelector('.filter-dropdown-option.selected');
    return sel ? sel.getAttribute('data-value') : null;
  }

  var newsGrid = document.getElementById('newsGrid');
  var loadIndicator = document.getElementById('loadIndicator');

  function buildCard(data, index) {
    var lang = window.currentLanguage || document.documentElement.lang || 'en';
    var dict = translations[lang] || translations.en;
    var baseDict = translations.en;
    var title = data.title;
    var desc = data.desc;
    if (data.id) {
      var baseKey = 'news.' + data.id + '.';
      title = (dict[baseKey + 'title'] || baseDict[baseKey + 'title'] || title);
      desc = (dict[baseKey + 'desc'] || baseDict[baseKey + 'desc'] || desc);
    }
    var readLabel = (dict && dict['card.read']) || (baseDict && baseDict['card.read']) || 'Read';

    var card = document.createElement('article');
    card.className = 'news-card';
    card.setAttribute('data-news-id', data.id || '');
    card.style.animationDelay = (index % 12) * 0.03 + 's';
    var accessBadge = '';
    if (data.access === 'private') accessBadge = '<span class="access-badge private card-access"><i class="fas fa-lock"></i> Private</span>';
    else if (data.access === 'link') accessBadge = '<span class="access-badge link card-access"><i class="fas fa-link"></i> Link</span>';
    else if (data.access === 'public') accessBadge = '<span class="access-badge public card-access"><i class="fas fa-globe"></i> Public</span>';
    var tagSpans = (accessBadge ? accessBadge : '') + data.tags.map(function(t) {
      var c = t.toLowerCase().replace(/\s/g, '');
      return '<span class="card-tag ' + c + '">' + t + '</span>';
    }).join('');
    var sevLabels = { critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low' };
    var sevIcons  = { critical: 'fas fa-skull-crossbones', high: 'fas fa-exclamation-triangle', medium: 'fas fa-exclamation-circle', low: 'fas fa-info-circle' };
    if (data.severity && sevLabels[data.severity]) {
      tagSpans += '<span class="card-tag sev-' + data.severity + '"><i class="' + sevIcons[data.severity] + '"></i> ' + sevLabels[data.severity] + '</span>';
    }
    var keywordSpans = data.keywords.map(function(k, i) {
      var cl = i === 0 ? 'card-keyword highlight' : 'card-keyword';
      return '<span class="' + cl + '">' + k + '</span>';
    }).join('');
    card.innerHTML =
      '<div class="card-tags">' + tagSpans + '</div>' +
      '<h3 class="card-title">' + title + '</h3>' +
      '<p class="card-desc">' + desc + '</p>' +
      '<div class="card-keywords">' + keywordSpans + '</div>' +
      '<div class="card-meta"><span><i class="far fa-clock"></i> ' + data.time + '</span>' +
      '<button class="card-read" data-news-id="' + (data.id || '') + '">' + readLabel + '</button></div>';
    return card;
  }

  /** Map URL category param to data category or special filter. */
  function applyCategoryFromUrl() {
    var params = new URLSearchParams(location.search);
    var urlCategory = params.get('category');
    if (!urlCategory) {
      window.currentMoreCategory = null;
      window.breakingOnly = false;
      return;
    }
    if (urlCategory === 'breaking') {
      window.currentMoreCategory = null;
      window.breakingOnly = true;
      return;
    }
    window.breakingOnly = false;
    var map = {
      'threat-intel': 'deep-dives',
      'apt': 'deep-dives',
      'pentest': 'deep-dives',
      'malware': 'research',
      'breaches': 'dark-web',
      'bug-bounty': 'beginner',
      'deep-dives': 'deep-dives',
      'beginner': 'beginner',
      'research': 'research',
      'dark-web': 'dark-web'
    };
    window.currentMoreCategory = map[urlCategory] || null;
  }

  applyCategoryFromUrl();

  function parseTimeToHours(t) {
    if (!t) return 0;
    var h = 0;
    var mHour = t.match(/(\d+)\s*h/);
    var mMin = t.match(/(\d+)\s*m/);
    if (mHour) h += parseInt(mHour[1], 10);
    if (mMin) h += parseInt(mMin[1], 10) / 60;
    return h;
  }

  function itemPassesFilter(item) {
    var typeVals = window.selectedTypes && window.selectedTypes.length > 0 ? window.selectedTypes : null;
    var sourcesVal = getSelectedFilterValue('sourcesDropdown');
    var category = window.currentMoreCategory || null;
    var breakingOnly = window.breakingOnly === true;
    if (breakingOnly) return item.severity === 'high';

    // Search query filter
    var q = (window.searchQuery || '').trim().toLowerCase();
    if (q) {
      var haystack = [item.title, item.desc].concat(item.keywords || []).concat(item.tags || []).join(' ').toLowerCase();
      if (haystack.indexOf(q) === -1) return false;
    }

    // Priority filter
    var priority = window.selectedPriority || 'all';
    if (priority !== 'all' && (item.severity || 'low') !== priority) return false;

    // Time filter
    var timeFilter = window.mainFilterTime || '24h';
    if (timeFilter === '1h' && parseTimeToHours(item.time) > 1) return false;
    if (timeFilter === '7d' && parseTimeToHours(item.time) > 168) return false;

    // Content type (main filter panel overrides legacy)
    var mainType = window.mainFilterType || null;
    if (mainType) {
      if (item.type !== mainType) return false;
    } else if (typeVals && typeVals.length > 0) {
      if (typeVals.indexOf(item.type) === -1) return false;
    } else {
      if (item.type !== 'news') return false;
    }

    // Source filter
    var mainSource = window.mainFilterSource || 'all';
    if (mainSource !== 'all' && item.type !== mainSource) return false;
    if (sourcesVal && sourcesVal !== 'all' && item.type !== sourcesVal) return false;

    if (category && item.category !== category) return false;
    return true;
  }

  window.applyFilters = function() {
    var filtered = loadedNewsList.filter(itemPassesFilter);
    if (newsGrid) newsGrid.innerHTML = '';
    filtered.forEach(function(data, i) {
      if (newsGrid) newsGrid.appendChild(buildCard(data, i));
    });
    if (loadIndicator) loadIndicator.classList.toggle('hidden', filtered.length === 0);
  };

  function appendNews(count) {
    var startLen = loadedNewsList.length;
    for (var i = 0; i < count; i++) loadedNewsList.push(getNextNewsItem());
    if (typeof window.applyFilters === 'function') {
      var newItems = loadedNewsList.slice(startLen);
      var toAppend = newItems.filter(itemPassesFilter);
      toAppend.forEach(function(data, j) {
        var idx = startLen + j;
        if (newsGrid) newsGrid.appendChild(buildCard(data, idx));
      });
      if (loadIndicator) loadIndicator.classList.toggle('hidden', false);
    } else {
      loadedNewsList.slice(startLen).forEach(function(data, i) {
        if (newsGrid) newsGrid.appendChild(buildCard(data, startLen + i));
      });
    }
  }

  appendNews(6);

  var loading = false;
  var loadMoreThreshold = 400;
  function maybeLoadMore() {
    if (loading || !loadIndicator) return;
    var rect = loadIndicator.getBoundingClientRect();
    if (rect.top < window.innerHeight + loadMoreThreshold) {
      loading = true;
      loadIndicator.classList.remove('hidden');
      setTimeout(function() {
        appendNews(3);
        loadIndicator.classList.add('hidden');
        loading = false;
      }, 800);
    }
  }
  window.addEventListener('scroll', function() { maybeLoadMore(); }, { passive: true });
  maybeLoadMore();
})();
