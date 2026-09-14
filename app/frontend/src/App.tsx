import { Toaster } from '@/components/ui/sonner';
import { TooltipProvider } from '@/components/ui/tooltip';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { AuthProvider } from './contexts/AuthContext';
import ConsoleLayout from './components/console/ConsoleLayout';
import Index from './pages/Index';
import EventsPage from './pages/console/EventsPage';
import AgentsPage from './pages/console/AgentsPage';
import KbPage from './pages/console/KbPage';
import ApprovalsPage from './pages/console/ApprovalsPage';
import RulesPage from './pages/console/RulesPage';
import OpsPage from './pages/console/OpsPage';
import UsersPage from './pages/console/UsersPage';
import HelpPage from './pages/HelpPage';
import AuthCallback from './pages/AuthCallback';
import AuthError from './pages/AuthError';
import LogoutCallbackPage from './pages/LogoutCallbackPage';

const queryClient = new QueryClient();

const AppRoutes = () => (
  <Routes>
    <Route path="/auth/callback" element={<AuthCallback />} />
    <Route path="/auth/error" element={<AuthError />} />
    <Route path="/logout" element={<LogoutCallbackPage />} />
    <Route element={<ConsoleLayout />}>
      <Route path="/" element={<Index />} />
      <Route path="/events" element={<EventsPage />} />
      <Route path="/agents" element={<AgentsPage />} />
      <Route path="/kb" element={<KbPage />} />
      <Route path="/approvals" element={<ApprovalsPage />} />
      <Route path="/rules" element={<RulesPage />} />
      <Route path="/ops" element={<OpsPage />} />
      <Route path="/users" element={<UsersPage />} />
      <Route path="/help" element={<HelpPage />} />
    </Route>
  </Routes>
);

const App = () => (
  <QueryClientProvider client={queryClient}>
    <AuthProvider>
      <TooltipProvider>
        <Toaster />
        <BrowserRouter>
          <AppRoutes />
        </BrowserRouter>
      </TooltipProvider>
    </AuthProvider>
  </QueryClientProvider>
);

export default App;
export { AppRoutes };
