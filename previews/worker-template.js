// Build with build-worker.py; the two markers are replaced with JSON payloads.
const files = __FILES_PAYLOAD__;
const imageAsset = __IMAGE_PAYLOAD__;
const imageBytes = Uint8Array.from(atob(imageAsset.base64), c => c.charCodeAt(0));
const headers = {
  'Cache-Control': 'no-store',
  'X-Content-Type-Options': 'nosniff',
  'X-Robots-Tag': 'noindex, nofollow',
  'Referrer-Policy': 'no-referrer',
  'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-src 'self'; connect-src 'none'; base-uri 'none'; form-action 'none'"
};
export default {
  async fetch(request) {
    const url = new URL(request.url);
    if (request.method !== 'GET' && request.method !== 'HEAD') return new Response('Method not allowed', {status:405,headers:{...headers,Allow:'GET, HEAD'}});
    let content, type = 'text/html; charset=utf-8', status = 200;
    if (url.pathname === imageAsset.path) {content=imageBytes;type='image/webp';}
    else if (files[url.pathname]) {content=files[url.pathname].body;type=files[url.pathname].type;}
    else if (url.pathname === '/__review/mobile') {
      const width = Math.max(320,Math.min(768,Number.parseInt(url.searchParams.get('width'),10)||390));
      content=`<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex"><title>404 / Mobile preview</title><style>html{background:#17191b;color:#ddd;font:12px monospace}body{margin:0;display:flex;min-height:100vh;align-items:center;justify-content:center;flex-direction:column;gap:15px;padding:24px;box-sizing:border-box}a{color:#ddd}iframe{width:${width}px;height:844px;max-width:100%;border:1px solid #454749;border-radius:16px;background:#080b08}p{margin:0}</style></head><body><p>404 / ${width}px mobile preview · <a href="/">Open full website ↗</a></p><iframe src="/" title="404 mobile website preview" width="${width}" height="844"></iframe></body></html>`;
    } else if(url.pathname==='/robots.txt') {content='User-agent: *\nDisallow: /\n';type='text/plain; charset=utf-8';}
    else {content='404 — This page has no identity.';type='text/plain; charset=utf-8';status=404;}
    return new Response(request.method==='HEAD'?null:content,{status,headers:{...headers,'Content-Type':type}});
  }
};
