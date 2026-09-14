import { parse as parseYaml } from 'yaml';

type FrontmatterValue = string | string[];

type BlogFrontmatter = Record<string, FrontmatterValue | undefined> & {
  title?: string;
  description?: string;
  date?: string;
  tags?: string[];
};

type BlogPost = {
  slug: string;
  markdown: string;
  title: string;
  description: string;
  frontmatter: BlogFrontmatter;
};

type SeoMeta = {
  title: string;
  description: string;
  keywords?: string;
  lang?: string;
  url?: string;
  siteName: string;
  ogTitle: string;
  ogDescription: string;
  ogImage?: string;
  ogImageAlt?: string;
  ogType: string;
  twitterCard: string;
  twitterSite?: string;
  twitterCreator?: string;
  twitterTitle: string;
  twitterDescription: string;
  twitterImage?: string;
  twitterImageAlt?: string;
  publishedTime?: string;
  tags?: string[];
};

const markdownModules = import.meta.glob(
  ['../../seo/content/**/*.md'],
  {
    query: '?raw',
    import: 'default',
    eager: true,
  },
) as Record<string, string>;

function parseFrontmatter(markdown: string) {
  if (!markdown.startsWith('---')) {
    return {
      data: {} satisfies BlogFrontmatter,
      content: markdown,
    };
  }

  const frontmatterMatch = markdown.match(/^---\n([\s\S]*?)\n---\n?/);
  if (!frontmatterMatch) {
    return {
      data: {} satisfies BlogFrontmatter,
      content: markdown,
    };
  }

  const rawFrontmatter = frontmatterMatch[1];
  const content = markdown.slice(frontmatterMatch[0].length);

  try {
    const parsed = parseYaml(rawFrontmatter);
    const data = normalizeFrontmatter(parsed);

    return { data, content };
  } catch (error) {
    console.warn(
      'Failed to parse blog frontmatter, falling back to raw content',
      error,
    );

    return {
      data: {} satisfies BlogFrontmatter,
      content: markdown,
    };
  }
}

function normalizeFrontmatter(value: unknown): BlogFrontmatter {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return {};
  }

  const entries = Object.entries(value as Record<string, unknown>).map(
    ([key, entryValue]) => {
      if (Array.isArray(entryValue)) {
        return [
          key,
          entryValue
            .map((item) => String(item).trim())
            .filter(Boolean),
        ] as const;
      }

      if (entryValue === null || typeof entryValue === 'undefined') {
        return [key, undefined] as const;
      }

      return [key, String(entryValue).trim()] as const;
    },
  );

  return Object.fromEntries(entries) as BlogFrontmatter;
}

function titleFromSlug(slug: string) {
  return slug
    .split('-')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

function descriptionFromMarkdown(markdown: string) {
  const plainText = markdown
    .replace(/^#+\s+/gm, '')
    .replace(/```[\s\S]*?```/g, '')
    .replace(/[*_`>#-]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();

  return plainText.slice(0, 160);
}

function normalizeSlug(filePath: string) {
  return filePath
    .replace(/\\/g, '/')
    .replace(/^.*\/seo\/content\//, '')
    .replace(/\/index\.md$/, '')
    .replace(/\.md$/, '');
}

function compareBlogPosts(a: BlogPost, b: BlogPost) {
  const aTime = a.frontmatter.date ? Date.parse(a.frontmatter.date) : NaN;
  const bTime = b.frontmatter.date ? Date.parse(b.frontmatter.date) : NaN;

  if (!Number.isNaN(aTime) && !Number.isNaN(bTime) && aTime !== bTime) {
    return bTime - aTime;
  }

  if (!Number.isNaN(aTime) && Number.isNaN(bTime)) {
    return -1;
  }

  if (Number.isNaN(aTime) && !Number.isNaN(bTime)) {
    return 1;
  }

  return a.slug.localeCompare(b.slug);
}

const blogPosts: BlogPost[] = Object.entries(markdownModules)
  .map(([filePath, rawMarkdown]) => {
    const { data, content } = parseFrontmatter(rawMarkdown);
    const slug = normalizeSlug(filePath);
    const frontmatter = data;
    const title = frontmatter.title || titleFromSlug(slug.split('/').pop() || slug);
    const description = frontmatter.description || descriptionFromMarkdown(content);

    return {
      slug,
      markdown: content,
      title,
      description,
      frontmatter,
    };
  })
  .sort(compareBlogPosts);

function getBlogPost(slug: string) {
  return blogPosts.find((post) => post.slug === slug);
}

function getBlogRoute(slug: string) {
  return `/blog/${slug}/`.replace(/\/+/g, '/');
}

function getSiteDomainUrl() {
  const configuredUrl = import.meta.env.VITE_SITE_URL?.trim();
  return configuredUrl ? configuredUrl.replace(/\/+$/, '') : undefined;
}

function getSiteName() {
  return import.meta.env.VITE_APP_TITLE?.trim() || 'Atoms';
}

function getTwitterSiteHandle() {
  return import.meta.env.VITE_TWITTER_SITE?.trim() || '@atoms';
}

function getTwitterCreatorHandle() {
  return import.meta.env.VITE_TWITTER_CREATOR?.trim() || getTwitterSiteHandle();
}

function getAbsoluteUrl(pathname: string) {
  const siteDomainUrl = getSiteDomainUrl();
  if (!siteDomainUrl) {
    return undefined;
  }

  const normalizedPath = pathname.startsWith('/') ? pathname : `/${pathname}`;
  return `${siteDomainUrl}${normalizedPath}`;
}

function hasBlogPosts() {
  return blogPosts.length > 0;
}

function frontmatterString(
  frontmatter: BlogFrontmatter,
  key: string,
): string | undefined {
  const value = frontmatter[key];
  return typeof value === 'string' ? value : undefined;
}

function frontmatterStringList(
  frontmatter: BlogFrontmatter,
  key: string,
): string[] | undefined {
  const value = frontmatter[key];

  if (Array.isArray(value)) {
    return value.filter((entry): entry is string => typeof entry === 'string');
  }

  if (typeof value === 'string') {
    return value
      .split(',')
      .map((entry) => entry.trim())
      .filter(Boolean);
  }

  return undefined;
}

function getPostSeoMeta(post?: BlogPost | null): SeoMeta {
  const siteName = getSiteName();
  const twitterSiteHandle = getTwitterSiteHandle();
  const twitterCreatorHandle = getTwitterCreatorHandle();
  const fallbackTitle = `Blog | ${siteName}`;
  const fallbackDescription =
    'This is a flexible blog starter that can be filled with Markdown content and prerendered into indexable pages.';

  if (!post) {
    const fallbackUrl = getAbsoluteUrl('/blog/');
    return {
      title: fallbackTitle,
      description: fallbackDescription,
      url: fallbackUrl,
      siteName,
      ogTitle: fallbackTitle,
      ogDescription: fallbackDescription,
      ogImageAlt: siteName,
      ogType: 'website',
      twitterCard: 'summary_large_image',
      twitterSite: twitterSiteHandle,
      twitterCreator: twitterCreatorHandle,
      twitterTitle: fallbackTitle,
      twitterDescription: fallbackDescription,
    };
  }

  const title = `${post.title} | Blog`;
  const description = post.description;
  const url =
    frontmatterString(post.frontmatter, 'og_url') ??
    getAbsoluteUrl(getBlogRoute(post.slug));
  const keywordsList =
    frontmatterStringList(post.frontmatter, 'keywords') ?? post.frontmatter.tags;
  const ogImage =
    frontmatterString(post.frontmatter, 'og_image') ??
    frontmatterString(post.frontmatter, 'hero_image');
  const imageAlt =
    frontmatterString(post.frontmatter, 'og_image_alt') ??
    frontmatterString(post.frontmatter, 'twitter_image_alt') ??
    post.title;
  const twitterImage =
    frontmatterString(post.frontmatter, 'twitter_image') ?? ogImage;

  return {
    title,
    description,
    keywords: keywordsList?.join(', '),
    lang: frontmatterString(post.frontmatter, 'lang'),
    url,
    siteName: frontmatterString(post.frontmatter, 'og_site_name') ?? siteName,
    ogTitle: frontmatterString(post.frontmatter, 'og_title') ?? title,
    ogDescription:
      frontmatterString(post.frontmatter, 'og_description') ?? description,
    ogImage,
    ogImageAlt: imageAlt,
    ogType: frontmatterString(post.frontmatter, 'og_type') ?? 'article',
    twitterCard:
      frontmatterString(post.frontmatter, 'twitter_card') ??
      (twitterImage ? 'summary_large_image' : 'summary'),
    twitterSite:
      frontmatterString(post.frontmatter, 'twitter_site') ?? twitterSiteHandle,
    twitterCreator:
      frontmatterString(post.frontmatter, 'twitter_creator') ??
      twitterCreatorHandle,
    twitterTitle:
      frontmatterString(post.frontmatter, 'twitter_title') ?? title,
    twitterDescription:
      frontmatterString(post.frontmatter, 'twitter_description') ?? description,
    twitterImage,
    twitterImageAlt: imageAlt,
    publishedTime: frontmatterString(post.frontmatter, 'date'),
    tags: post.frontmatter.tags,
  };
}

export {
  blogPosts,
  getBlogPost,
  getBlogRoute,
  getPostSeoMeta,
  hasBlogPosts,
};
export type { BlogFrontmatter, BlogPost, SeoMeta };
