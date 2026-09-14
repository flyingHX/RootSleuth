import { useEffect } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import BlogArticleLayout from '@/components/blog/BlogArticleLayout';
import MarkdownArticle from '@/components/blog/MarkdownArticle';
import { getBlogPost, getPostSeoMeta } from '@/lib/blog';

function getSlugFromPathname(pathname: string) {
  return pathname
    .replace(/^\/blog\/?/, '')
    .replace(/\/+$/, '')
    .replace(/^\/+/, '');
}

function ensureMetaTag(
  attribute: 'name' | 'property',
  value: string,
) {
  let tag = document.head.querySelector(
    `meta[${attribute}="${value}"]`,
  ) as HTMLMetaElement | null;

  if (!tag) {
    tag = document.createElement('meta');
    tag.setAttribute(attribute, value);
    document.head.appendChild(tag);
  }

  return tag;
}

function getCurrentPageUrl(pathname: string) {
  if (typeof window === 'undefined') {
    return undefined;
  }

  return `${window.location.origin}${pathname}`;
}

const BlogPostPage = () => {
  const location = useLocation();
  const slug = getSlugFromPathname(location.pathname);
  const post = slug === '*' ? null : getBlogPost(slug);

  useEffect(() => {
    if (!post) {
      return;
    }

    const seoMeta = getPostSeoMeta(post);
    const resolvedUrl = seoMeta.url ?? getCurrentPageUrl(location.pathname);
    const previousTitle = document.title;
    const previousLang = document.documentElement.lang;

    const metaDefinitions = [
      { attribute: 'name' as const, key: 'description', value: seoMeta.description },
      { attribute: 'name' as const, key: 'keywords', value: seoMeta.keywords },
      { attribute: 'property' as const, key: 'og:url', value: resolvedUrl },
      { attribute: 'property' as const, key: 'og:site_name', value: seoMeta.siteName },
      { attribute: 'property' as const, key: 'og:title', value: seoMeta.ogTitle },
      {
        attribute: 'property' as const,
        key: 'og:description',
        value: seoMeta.ogDescription,
      },
      { attribute: 'property' as const, key: 'og:image', value: seoMeta.ogImage },
      {
        attribute: 'property' as const,
        key: 'og:image:alt',
        value: seoMeta.ogImageAlt,
      },
      { attribute: 'property' as const, key: 'og:type', value: seoMeta.ogType },
      {
        attribute: 'property' as const,
        key: 'article:published_time',
        value: seoMeta.publishedTime,
      },
      { attribute: 'name' as const, key: 'twitter:card', value: seoMeta.twitterCard },
      { attribute: 'name' as const, key: 'twitter:site', value: seoMeta.twitterSite },
      {
        attribute: 'name' as const,
        key: 'twitter:creator',
        value: seoMeta.twitterCreator,
      },
      {
        attribute: 'name' as const,
        key: 'twitter:title',
        value: seoMeta.twitterTitle,
      },
      {
        attribute: 'name' as const,
        key: 'twitter:description',
        value: seoMeta.twitterDescription,
      },
      {
        attribute: 'name' as const,
        key: 'twitter:image',
        value: seoMeta.twitterImage,
      },
      {
        attribute: 'name' as const,
        key: 'twitter:image:alt',
        value: seoMeta.twitterImageAlt,
      },
    ];

    const previousValues = metaDefinitions.map(({ attribute, key, value }) => {
      if (!value) {
        return null;
      }

      const tag = ensureMetaTag(attribute, key);
      const previousContent = tag.content;
      tag.content = value;
      return { tag, previousContent };
    });

    document.title = seoMeta.title;
    if (seoMeta.lang) {
      document.documentElement.lang = seoMeta.lang;
    }

    const articleTagEntries = (seoMeta.tags ?? []).map((tag) => {
      const metaTag = document.createElement('meta');
      metaTag.setAttribute('property', 'article:tag');
      metaTag.content = tag;
      document.head.appendChild(metaTag);
      return metaTag;
    });

    return () => {
      document.title = previousTitle;
      document.documentElement.lang = previousLang;
      articleTagEntries.forEach((tag) => tag.remove());
      previousValues.forEach((entry) => {
        if (!entry) {
          return;
        }
        entry.tag.content = entry.previousContent;
      });
    };
  }, [post, location.pathname]);

  if (slug === '*') {
    return <Navigate to="/blog/" replace />;
  }

  if (!post) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center bg-gradient-to-br from-gray-50 to-blue-50 p-6 text-center">
        <div className="space-y-6 max-w-md">
          <div className="space-y-4">
            <h1 className="text-7xl font-bold text-gray-300">404</h1>
            <h2 className="text-2xl font-bold text-gray-800">Page Not Found</h2>
            <p className="text-base text-muted-foreground">
              Sorry, the blog post you are looking for does not exist or has been removed.
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <BlogArticleLayout title={post.title} description={post.description}>
      <MarkdownArticle markdown={post.markdown} />
    </BlogArticleLayout>
  );
};

export default BlogPostPage;
