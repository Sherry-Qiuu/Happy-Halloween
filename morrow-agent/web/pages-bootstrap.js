/* GitHub Pages has no backend: open the local demo automatically. */
(function () {
  'use strict';
  var isStaticHost = /(^|\.)github\.io$/i.test(location.hostname) ||
    /(^|\.)github\.preview\.app$/i.test(location.hostname);
  if (!isStaticHost) return;
  var tries = 0;
  var timer = setInterval(function () {
    var button = document.getElementById('authLocal');
    if (button && !button.hidden) {
      clearInterval(timer);
      button.click();
      return;
    }
    tries += 1;
    if (tries > 60) clearInterval(timer);
  }, 200);
})();
