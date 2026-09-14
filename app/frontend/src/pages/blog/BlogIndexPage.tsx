import { Link } from 'react-router-dom';
import { blogPosts, getBlogRoute } from '@/lib/blog';

const BlogIndexPage = () => (
  <main className="min-h-screen bg-[radial-gradient(circle_at_top,_rgba(14,165,233,0.14),_transparent_36%),linear-gradient(180deg,_#f8fafc_0%,_#eff6ff_100%)] text-slate-900">
    <section className="mx-auto max-w-5xl px-6 py-16 sm:py-20">
      <div className="max-w-3xl space-y-5">
        <p className="text-sm font-semibold uppercase tracking-[0.24em] text-sky-700">
          Blog Starter
        </p>
        <h1 className="font-serif text-4xl leading-tight text-slate-950 sm:text-5xl">
          Start with a blog section that is ready to grow with your SEO site
        </h1>
        <p className="text-lg leading-8 text-slate-600">
          This is the starter blog index. Add Markdown files under
          `seo/content/` and the site will automatically generate the list,
          article pages, and prerender routes.
        </p>
      </div>

      <div className="mt-12 grid gap-6">
        {blogPosts.length > 0 ? (
          blogPosts.map((post) => (
            <article
              key={post.slug}
              className="rounded-3xl border border-sky-100 bg-white/90 p-6 shadow-sm shadow-sky-100/60 transition-transform duration-200 hover:-translate-y-1"
            >
              <div className="flex flex-wrap items-center gap-3 text-sm text-slate-500">
                {post.frontmatter.date ? <span>{post.frontmatter.date}</span> : null}
                {post.frontmatter.tags?.map((tag) => (
                  <span
                    key={tag}
                    className="rounded-full bg-sky-50 px-3 py-1 text-sky-700"
                  >
                    {tag}
                  </span>
                ))}
              </div>
              <h2 className="mt-4 font-serif text-2xl text-slate-950">
                <Link className="hover:text-sky-700" to={getBlogRoute(post.slug)}>
                  {post.title}
                </Link>
              </h2>
              <p className="mt-3 text-base leading-7 text-slate-600">
                {post.description}
              </p>
              <Link
                to={getBlogRoute(post.slug)}
                className="mt-5 inline-flex text-sm font-semibold text-sky-700 underline underline-offset-4"
              >
                Read article
              </Link>
            </article>
          ))
        ) : (
          <section className="rounded-[2rem] border border-dashed border-sky-200 bg-white/80 p-8">
            <h2 className="font-serif text-2xl text-slate-950">No articles yet</h2>
            <p className="mt-3 max-w-2xl text-base leading-7 text-slate-600">
              Add Markdown files under `seo/content/` and article cards will
              appear here automatically. This keeps the starter clean by default
              while making it easy to begin publishing content for your own SEO
              strategy.
            </p>
          </section>
        )}
      </div>
    </section>
  </main>
);

export default BlogIndexPage;
