// A Lambda só aceita pedidos que o CloudFront assina (OAC). Num POST o CloudFront exige o
// SHA-256 do corpo no header x-amz-content-sha256; sem ele a AWS responde 403.
(() => {
  const originalFetch = window.fetch.bind(window);

  async function sha256(text) {
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
    return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
  }

  window.fetch = async (input, init = {}) => {
    const method = (init.method || 'GET').toUpperCase();
    const sameOrigin = new URL(input, location.href).origin === location.origin;
    if (!sameOrigin || (method !== 'POST' && method !== 'PUT')) return originalFetch(input, init);
    const headers = new Headers(init.headers);
    headers.set('x-amz-content-sha256', await sha256(init.body ?? ''));
    return originalFetch(input, { ...init, headers });
  };
})();
