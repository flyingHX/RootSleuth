import { Link } from 'react-router-dom';

type BlogArticleLayoutProps = {
  title: string;
  description?: string;
  children: React.ReactNode;
};

const BlogArticleLayout = ({
  title,
  description,
  children,
}: BlogArticleLayoutProps) => (
  <main className="min-h-screen bg-slate-50 text-slate-900">
    <div className="mx-auto max-w-4xl px-6 pt-8">
      <Link
        to="/blog/"
        className="text-sm text-slate-500 underline-offset-4 hover:text-slate-900 hover:underline"
      >
        Back to blog
      </Link>
    </div>
    <article className="mx-auto max-w-3xl px-6 py-12">
      <header className="border-b border-slate-200 pb-10">
        <p className="text-xs font-semibold uppercase tracking-[0.28em] text-sky-700">
          Blog Article
        </p>
        <h1 className="mt-4 font-serif text-4xl leading-tight text-slate-950 sm:text-5xl">
          {title}
        </h1>
        {description ? (
          <p className="mt-5 max-w-2xl text-lg leading-8 text-slate-600">
            {description}
          </p>
        ) : null}
      </header>

      <div className="mt-10">{children}</div>
    </article>
  </main>
);

export default BlogArticleLayout;
