import Markdown from 'markdown-to-jsx';

type MarkdownArticleProps = {
  markdown: string;
};

const MarkdownArticle = ({ markdown }: MarkdownArticleProps) => (
  <div className="prose prose-slate prose-lg max-w-none prose-headings:font-serif prose-headings:text-slate-950 prose-h1:mt-0 prose-h1:text-4xl prose-h1:leading-tight prose-h2:mt-12 prose-h2:border-t prose-h2:border-slate-200 prose-h2:pt-8 prose-h2:text-3xl prose-h2:leading-snug prose-h3:mt-10 prose-h3:text-2xl prose-h3:leading-snug prose-p:text-[1.06rem] prose-p:leading-8 prose-li:leading-8 prose-strong:text-slate-950 prose-code:rounded prose-code:bg-slate-100 prose-code:px-1.5 prose-code:py-0.5 prose-code:text-[0.9em] prose-code:font-medium prose-pre:rounded-3xl prose-pre:bg-slate-950 prose-pre:p-5 prose-pre:text-slate-50 prose-a:text-sky-700 prose-a:decoration-sky-300 prose-a:underline-offset-4 hover:prose-a:text-sky-800">
    <Markdown
      options={{
        forceBlock: true,
        overrides: {
          a: {
            props: {
              className: 'font-medium',
            },
          },
          code: {
            props: {
              className: '',
            },
          },
          pre: {
            props: {
              className: 'overflow-x-auto',
            },
          },
        },
      }}
    >
      {markdown}
    </Markdown>
  </div>
);

export default MarkdownArticle;
