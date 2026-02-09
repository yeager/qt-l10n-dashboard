#!/usr/bin/env node
// Fetches l10n data from qt.io and injects it into index.html as localStorage cache
// Usage: node update.js
// Then open index.html in browser - it will use the cached data if CORS blocks live fetch

const https = require('https');
const fs = require('fs');
const path = require('path');

function fetch(url) {
  return new Promise((resolve, reject) => {
    https.get(url, res => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => resolve(data));
    }).on('error', reject);
  });
}

async function main() {
  console.log('Fetching from https://l10n-files.qt.io/l10n-files/ ...');
  const html = await fetch('https://l10n-files.qt.io/l10n-files/');
  
  // Write raw HTML for debugging
  fs.writeFileSync(path.join(__dirname, 'raw.html'), html);
  console.log(`Got ${html.length} bytes, saved to raw.html`);
  
  // Write a data.json that can be loaded as fallback
  // We'll inject a <script> block into index.html that seeds localStorage
  
  const dataScript = `
<script>
// Auto-generated cache from update.js - ${new Date().toISOString()}
try {
  const _rawHtml = ${JSON.stringify(html)};
  localStorage.setItem('qt-l10n-raw', _rawHtml);
  localStorage.setItem('qt-l10n-raw-ts', '${Date.now()}');
} catch(e) {}
</script>`;

  // Read index.html, inject before </body>
  const indexPath = path.join(__dirname, 'index.html');
  let indexHtml = fs.readFileSync(indexPath, 'utf8');
  
  // Remove any previous auto-generated cache
  indexHtml = indexHtml.replace(/\n<script>\n\/\/ Auto-generated cache from update\.js[\s\S]*?<\/script>/g, '');
  
  // Inject before </body>
  indexHtml = indexHtml.replace('</body>', dataScript + '\n</body>');
  
  fs.writeFileSync(indexPath, indexHtml);
  console.log('Injected cached data into index.html');
  console.log('Open index.html in your browser - it will use live data if possible, cached data otherwise.');
}

main().catch(e => { console.error(e); process.exit(1); });
