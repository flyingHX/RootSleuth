import React from 'react';
import { renderToString } from 'react-dom/server';
import { Route, Routes } from 'react-router-dom';
import { StaticRouter } from 'react-router-dom/server';
import BlogRoutes from '../src/blog-routes';
import { getBlogPost, getPostSeoMeta } from '../src/lib/blog';

function getHeadElements(url) {
  if (!url.startsWith('/blog')) {
    return undefined;
  }

  const slug = url
    .replace(/^\/blog\/?/, '')
    .replace(/\/+$/, '')
    .replace(/^\/+/, '');

  const post = slug ? getBlogPost(slug) : null;
  const seoMeta = getPostSeoMeta(post);
  const elements = [
    {
      type: 'meta',
      props: {
        name: 'prerender-static-page',
        content: 'blog',
      },
    },
    {
      type: 'meta',
      props: {
        name: 'description',
        content: seoMeta.description,
      },
    },
    seoMeta.keywords
      ? {
          type: 'meta',
          props: {
            name: 'keywords',
            content: seoMeta.keywords,
          },
        }
      : null,
    seoMeta.url
      ? {
          type: 'meta',
          props: {
            property: 'og:url',
            content: seoMeta.url,
          },
        }
      : null,
    {
      type: 'meta',
      props: {
        property: 'og:site_name',
        content: seoMeta.siteName,
      },
    },
    seoMeta.ogImage
      ? {
          type: 'meta',
          props: {
            property: 'og:image',
            content: seoMeta.ogImage,
          },
        }
      : null,
    seoMeta.ogImageAlt
      ? {
          type: 'meta',
          props: {
            property: 'og:image:alt',
            content: seoMeta.ogImageAlt,
          },
        }
      : null,
    {
      type: 'meta',
      props: {
        name: 'twitter:card',
        content: seoMeta.twitterCard,
      },
    },
    seoMeta.twitterSite
      ? {
          type: 'meta',
          props: {
            name: 'twitter:site',
            content: seoMeta.twitterSite,
          },
        }
      : null,
    seoMeta.twitterCreator
      ? {
          type: 'meta',
          props: {
            name: 'twitter:creator',
            content: seoMeta.twitterCreator,
          },
        }
      : null,
    {
      type: 'meta',
      props: {
        name: 'twitter:title',
        content: seoMeta.twitterTitle,
      },
    },
    {
      type: 'meta',
      props: {
        name: 'twitter:description',
        content: seoMeta.twitterDescription,
      },
    },
    seoMeta.twitterImage
      ? {
          type: 'meta',
          props: {
            name: 'twitter:image',
            content: seoMeta.twitterImage,
          },
        }
      : null,
    seoMeta.twitterImageAlt
      ? {
          type: 'meta',
          props: {
            name: 'twitter:image:alt',
            content: seoMeta.twitterImageAlt,
          },
        }
      : null,
    seoMeta.publishedTime
      ? {
          type: 'meta',
          props: {
            property: 'article:published_time',
            content: seoMeta.publishedTime,
          },
        }
      : null,
    ...(seoMeta.tags ?? []).map((tag) => ({
      type: 'meta',
      props: {
        property: 'article:tag',
        content: tag,
      },
    })),
  ].filter(Boolean);

  return {
    title: seoMeta.title,
    lang: seoMeta.lang,
    elements: new Set(elements),
  };
}

export async function prerender({ url }) {
  const html = renderToString(
    React.createElement(
      StaticRouter,
      { location: url },
      React.createElement(
        Routes,
        null,
        React.createElement(
          Route,
          { path: '/blog/*', element: React.createElement(BlogRoutes) },
        ),
      ),
    ),
  );

  const slug = url
    .replace(/^\/blog\/?/, '')
    .replace(/\/+$/, '')
    .replace(/^\/+/, '');
  const is404 = slug && !getBlogPost(slug);

  return {
    html,
    head: getHeadElements(url),
    ...(is404 ? { statusCode: 404 } : {}),
  };
}
