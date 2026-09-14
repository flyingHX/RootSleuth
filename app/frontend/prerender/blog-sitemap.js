import fs from 'node:fs';
import path from 'node:path';
import { seoContentDir, normalizeRouteFromMarkdown, collectMarkdownFiles } from './utils.js';

function collectMarkdownLastmod(dir) {
  const bucket = {};

  for (const fullPath of collectMarkdownFiles(dir)) {
    const relativePath = path.relative(seoContentDir, fullPath);
    const route = normalizeRouteFromMarkdown(relativePath);
    bucket[route] = fs.statSync(fullPath).mtime;
  }

  return bucket;
}

function getLatestContentMtime(lastmodMap) {
  const dates = Object.values(lastmodMap).filter((value) => value instanceof Date);

  if (dates.length === 0) {
    return undefined;
  }

  return new Date(Math.max(...dates.map((date) => date.getTime())));
}

export function getSitemapLastmod() {
  const contentLastmod = collectMarkdownLastmod(seoContentDir);
  const latestContentMtime = getLatestContentMtime(contentLastmod);

  return {
    ...(latestContentMtime ? { '/blog/': latestContentMtime } : {}),
    ...contentLastmod,
  };
}
