/**
 * Sidebar: newsletter close (persisted), frequency buttons, email subscribe
 */
(function() {
  'use strict';
  var DISMISSED_KEY = 'newsletter_dismissed';
  var card = document.getElementById('newsletterCard');

  // Hide permanently if already dismissed
  if (card) {
    try {
      if (localStorage.getItem(DISMISSED_KEY) === '1') {
        card.style.display = 'none';
      }
    } catch (e) {}
  }

  var closeBtn = document.getElementById('newsletterClose');
  if (closeBtn && card) {
    closeBtn.addEventListener('click', function() {
      card.style.display = 'none';
      try { localStorage.setItem(DISMISSED_KEY, '1'); } catch (e) {}
    });
  }

  var freqButtons = document.querySelectorAll('.freq-btn');
  freqButtons.forEach(function(btn) {
    btn.addEventListener('click', function() {
      freqButtons.forEach(function(b) { b.classList.remove('active'); });
      this.classList.add('active');
    });
  });

  var newsletterInput = document.querySelector('.newsletter-input');
  if (newsletterInput) {
    newsletterInput.addEventListener('keypress', function(e) {
      if (e.key === 'Enter') {
        var email = this.value.trim();
        var activeFreqBtn = document.querySelector('.freq-btn.active');
        var frequency = activeFreqBtn ? activeFreqBtn.textContent.trim().toLowerCase() : 'daily';
        if (email && email.indexOf('@') !== -1) {
          window.location.href = '/digest';
        }
      }
    });
  }

  // Populate Today's Digest from live cluster data
  var digestList = document.getElementById('digestList');
  if (digestList) {
    fetch('/api/clusters/?limit=5&sort=score')
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(data) {
        if (!data || !data.items || !data.items.length) return;
        var frag = document.createDocumentFragment();
        data.items.forEach(function(cluster) {
          var li = document.createElement('li');
          var a = document.createElement('a');
          a.href = '/cluster?id=' + cluster.id;
          a.textContent = cluster.label;
          li.appendChild(a);
          frag.appendChild(li);
        });
        digestList.appendChild(frag);
      })
      .catch(function() {});
  }
})();
