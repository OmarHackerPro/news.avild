/**
 * Auth state: reads JWT from localStorage, calls /api/auth/me,
 * shows user name/avatar in navbar. Listens for postMessage from popup login.
 */
(function() {
  'use strict';

  var TOKEN_KEY = 'auth_token';
  var USER_KEY  = 'auth_user';

  function getToken() {
    try { return localStorage.getItem(TOKEN_KEY) || ''; } catch (e) { return ''; }
  }

  function setUserState(user) {
    var nameEl = document.getElementById('navUserName');
    var link   = document.getElementById('navUserLink');
    var iconEl = document.getElementById('navUserIcon');
    var avatarWrap = document.getElementById('navUserAvatar');
    var avatarImg  = document.getElementById('navUserAvatarImg');
    if (!nameEl || !link) return;

    if (user && user.name) {
      nameEl.textContent = user.name;
      nameEl.hidden = false;
      link.setAttribute('aria-label', 'Profile: ' + user.name);
      link.setAttribute('href', '/profile');
      var hasPic = typeof user.profile_picture === 'string' && user.profile_picture.trim().length > 0;
      if (hasPic && avatarWrap && avatarImg) {
        avatarImg.src = user.profile_picture.indexOf('http') === 0
          ? user.profile_picture
          : ('/static/' + user.profile_picture);
        avatarImg.alt = user.name;
        avatarWrap.hidden = false;
        if (iconEl) iconEl.hidden = true;
      } else {
        if (avatarWrap) avatarWrap.hidden = true;
        if (avatarImg) avatarImg.removeAttribute('src');
        if (iconEl) iconEl.hidden = false;
      }
    } else {
      nameEl.textContent = '';
      nameEl.hidden = true;
      link.setAttribute('aria-label', 'Log in or account');
      link.setAttribute('href', '/login');
      if (avatarWrap) avatarWrap.hidden = true;
      if (iconEl) iconEl.hidden = false;
    }
  }

  function fetchMe() {
    var token = getToken();
    if (!token) { setUserState(null); return; }
    fetch('/api/auth/me', { headers: { 'Authorization': 'Bearer ' + token } })
      .then(function(r) {
        if (!r.ok) {
          try { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(USER_KEY); } catch (e) {}
          setUserState(null);
          return null;
        }
        return r.json();
      })
      .then(function(user) {
        if (user && user.name) {
          setUserState(user);
          try { localStorage.setItem(USER_KEY, JSON.stringify(user)); } catch (e) {}
        } else {
          setUserState(null);
        }
      })
      .catch(function() { setUserState(null); });
  }

  // Listen for postMessage from popup login/signup windows
  window.addEventListener('message', function(e) {
    if (e.origin !== window.location.origin) return;
    if (e.data && e.data.type === 'auth' && e.data.user) {
      setUserState(e.data.user);
    }
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', fetchMe);
  } else {
    fetchMe();
  }
})();
