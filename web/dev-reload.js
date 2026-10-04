(function() {
  let connected = false;
  const es = new EventSource('/dev/reload');

  function reloadWhenAllowed() {
    if (typeof window.devReloadBlocked === 'function' && window.devReloadBlocked()) {
      setTimeout(reloadWhenAllowed, 1000);
      return;
    }
    location.reload();
  }

  es.addEventListener('hello', () => {
    connected = true;
    console.info('[dev-reload] on');
  });

  es.addEventListener('reload', () => {
    reloadWhenAllowed();
  });

  es.onerror = () => {
    if (!connected) {
      es.close();
      return;
    }
    es.close();
    let elapsed = 0;
    const poll = setInterval(() => {
      elapsed += 500;
      if (elapsed >= 60000) {
        clearInterval(poll);
        return;
      }
      fetch('/health', { cache: 'no-store' })
        .then(res => {
          if (res.ok) {
            clearInterval(poll);
            reloadWhenAllowed();
          }
        })
        .catch(() => {});
    }, 500);
  };
})();
