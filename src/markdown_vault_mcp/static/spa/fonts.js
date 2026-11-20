// Full font licenses are included in shell.html and live in spa/fonts/.
// Binary FontFace sources work with the MCP Apps font-src policy; see
// docs/design/reference/mcp-apps-fonts.md.
(() => {
  const faces = [
    ["Newsreader", "400 600", "/*@@B64:fonts/newsreader-latin-opsz-normal.woff2@@*/"],
    ["Public Sans", "400 700", "/*@@B64:fonts/public-sans-latin-wght-normal.woff2@@*/"],
    ["IBM Plex Mono", "400", "/*@@B64:fonts/IBMPlexMono-Regular-Latin1.woff2@@*/"],
    ["IBM Plex Mono", "500", "/*@@B64:fonts/IBMPlexMono-Medium-Latin1.woff2@@*/"],
    ["IBM Plex Mono", "600", "/*@@B64:fonts/IBMPlexMono-SemiBold-Latin1.woff2@@*/"],
  ];
  for (const [family, weight, data] of faces) {
    try {
      const bytes = Uint8Array.from(atob(data), c => c.charCodeAt(0));
      document.fonts.add(new FontFace(family, bytes, {weight, style: "normal", display: "swap"}));
    } catch (_) {
      // The existing system font stack remains available.
    }
  }
})();
