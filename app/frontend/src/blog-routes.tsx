import { Navigate, Route, Routes } from 'react-router-dom';
// MODULE_BLOG_IMPORTS_START
// MODULE_BLOG_IMPORTS_END
import BlogIndexPage from './pages/blog/BlogIndexPage';
import BlogPostPage from './pages/blog/BlogPostPage';

const BlogRoutes = () => (
  <Routes>
    <Route index element={<BlogIndexPage />} />
    <Route path=":slug" element={<BlogPostPage />} />
    {/* MODULE_BLOG_ROUTES_START */}
    {/* MODULE_BLOG_ROUTES_END */}
    <Route path="*" element={<Navigate to="/blog/" replace />} />
  </Routes>
);

export default BlogRoutes;
