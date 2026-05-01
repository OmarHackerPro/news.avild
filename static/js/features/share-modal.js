/**
 * Share Insight modal: access (Private / Link / Public), Generate Link, Copy, Revoke.
 * Access badges are consistent for use in feed and detail views.
 */
(function() {
  var modal = document.getElementById('shareModal');
  if (!modal) return;

  var closeTargets = modal.querySelectorAll('[data-share-close="true"]');
  var generateBtn = document.getElementById('shareGenerateBtn');
  var generatedBlock = document.getElementById('shareGenerated');
  var linkInput = document.getElementById('shareLinkInput');
  var copyBtn = document.getElementById('shareCopyBtn');
  var revokeBtn = document.getElementById('shareRevokeBtn');
  var accessRadios = modal.querySelectorAll('input[name="shareAccess"]');

  var currentLink = null;

  function open() {
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('no-scroll');
  }

  function close() {
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('no-scroll');
  }

  function getAccess() {
    var r = modal.querySelector('input[name="shareAccess"]:checked');
    return r ? r.value : 'private';
  }

  function trackExport(kind, extra) {
    if (window.CyberNews && window.CyberNews.analytics) {
      window.CyberNews.analytics.track('export_click', Object.assign({
        kind: kind,
        source: 'share_modal',
      }, extra || {}));
    }
  }

  function generateLink() {
    var access = getAccess();
    if (access === 'private') {
      if (generatedBlock) generatedBlock.hidden = true;
      currentLink = null;
      trackExport('share_link_private', { access: 'private' });
      return;
    }
    var base = window.location.origin + (window.location.pathname || '/');
    var id = 'id=' + Date.now();
    currentLink = base + (base.indexOf('?') >= 0 ? '&' : '?') + id + '&access=' + access;
    if (linkInput) linkInput.value = currentLink;
    if (generatedBlock) generatedBlock.hidden = false;
    trackExport('share_link_generate', { access: access });
  }

  function copyLink() {
    if (!linkInput || !currentLink) return;
    linkInput.select();
    linkInput.setSelectionRange(0, 99999);
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(currentLink);
      } else {
        document.execCommand('copy');
      }
      trackExport('share_link_copy', { access: getAccess() });
      var dict = (window.CyberNews && window.CyberNews.translations) ? (window.CyberNews.translations[window.currentLanguage || 'en'] || window.CyberNews.translations.en) : {};
      if (copyBtn) copyBtn.textContent = dict['share.copied'] || 'Copied!';
      setTimeout(function() {
        var d = (window.CyberNews && window.CyberNews.translations) ? (window.CyberNews.translations[window.currentLanguage || 'en'] || window.CyberNews.translations.en) : {};
        if (copyBtn) copyBtn.textContent = d['share.copy'] || 'Copy';
      }, 2000);
    } catch (e) {}
  }

  function revoke() {
    currentLink = null;
    if (linkInput) linkInput.value = '';
    if (generatedBlock) generatedBlock.hidden = true;
    var privateRadio = modal.querySelector('input[value="private"]');
    if (privateRadio) privateRadio.checked = true;
  }

  closeTargets.forEach(function(el) { el.addEventListener('click', close); });
  if (generateBtn) generateBtn.addEventListener('click', generateLink);
  if (copyBtn) copyBtn.addEventListener('click', copyLink);
  if (revokeBtn) revokeBtn.addEventListener('click', revoke);

  modal.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') close();
  });

  var navShare = document.getElementById('navShareBtn');
  if (navShare) navShare.addEventListener('click', open);

  window.CyberNews = window.CyberNews || {};
  window.CyberNews.openShareModal = open;
})();
