/**
 * Auth: reads JWT from localStorage, calls /api/auth/me,
 * updates the navbar user link (icon → login page, or name → profile page).
 */
(function () {
  'use strict';

  var TOKEN_KEY = 'auth_token';
  var USER_KEY  = 'auth_user';

  function getToken() {
    try { return localStorage.getItem(TOKEN_KEY) || ''; } catch (e) { return ''; }
  }

  function setUserState(user) {
    var link       = document.getElementById('navUserLink');
    var iconEl     = document.getElementById('navUserIcon');
    var avatarWrap = document.getElementById('navUserAvatar');
    var avatarImg  = document.getElementById('navUserAvatarImg');
    var nameEl     = document.getElementById('navUserName');
    if (!link) return;

    if (user && user.name) {
      link.href = '/profile';
      link.setAttribute('aria-label', 'Account: ' + user.name);
      if (nameEl) { nameEl.textContent = user.name; nameEl.hidden = false; }

      var hasPic = typeof user.profile_picture === 'string' && user.profile_picture.trim().length > 0;
      if (hasPic && avatarWrap && avatarImg) {
        avatarImg.src = user.profile_picture.indexOf('http') === 0
          ? user.profile_picture
          : ('/static/' + user.profile_picture);
        avatarImg.alt   = user.name;
        avatarWrap.hidden = false;
        if (iconEl) iconEl.hidden = true;
      } else {
        if (avatarWrap) avatarWrap.hidden = true;
        if (iconEl)     iconEl.hidden     = false;
      }
    } else {
      link.href = '/static/login.html';
      link.setAttribute('aria-label', 'Log in or account');
      if (nameEl)     { nameEl.textContent = ''; nameEl.hidden = true; }
      if (avatarWrap) avatarWrap.hidden = true;
      if (iconEl)     iconEl.hidden     = false;
    }
  }

  function fetchMe() {
    var token = getToken();
    if (!token) { setUserState(null); return; }
    fetch('/api/auth/me', { headers: { 'Authorization': 'Bearer ' + token } })
      .then(function (r) {
        if (!r.ok) {
          try { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(USER_KEY); } catch (e) {}
          setUserState(null);
          return null;
        }
        return r.json();
      })
      .then(function (user) {
        if (user && user.name) {
          setUserState(user);
          try { localStorage.setItem(USER_KEY, JSON.stringify(user)); } catch (e) {}
        } else {
          setUserState(null);
        }
      })
      .catch(function () { setUserState(null); });
  }

  // Listen for postMessage from popup login/signup windows
  window.addEventListener('message', function (e) {
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
