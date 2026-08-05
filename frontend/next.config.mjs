/** @type {import('next').NextConfig} */
// Static export for GitHub Pages. basePath is env-driven so a project-page deploy
// (username.github.io/TarifeAI) sets NEXT_PUBLIC_BASE_PATH=/TarifeAI, while local dev
// and a future custom-domain deploy leave it empty and serve from the root.
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";

const nextConfig = {
  output: "export",
  basePath,
  assetPrefix: basePath || undefined,
  trailingSlash: true, // GH Pages resolves routes as directories (/path/index.html)
  images: { unoptimized: true }, // next/image is unused; keeps export safe if added later
};

export default nextConfig;
