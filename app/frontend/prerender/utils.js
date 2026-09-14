import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const currentFile = fileURLToPath(import.meta.url);
const __dirname = path.dirname(currentFile);
const projectRoot = path.resolve(__dirname, '..');

export const seoContentDir = path.resolve(projectRoot, 'seo', 'content');

export function normalizeRouteFromMarkdown(relativePath) {
  const normalized = relativePath
    .replace(/\\/g, '/')
    .replace(/\/index\.md$/, '')
    .replace(/\.md$/, '');

  return normalized ? `/blog/${normalized}/` : '/blog/';
}

export function collectMarkdownFiles(dir, bucket = []) {
  if (!fs.existsSync(dir)) {
    return bucket;
  }

  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name.startsWith('.')) {
      continue;
    }

    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      collectMarkdownFiles(fullPath, bucket);
      continue;
    }

    if (entry.isFile() && entry.name.endsWith('.md')) {
      bucket.push(fullPath);
    }
  }

  return bucket;
}
