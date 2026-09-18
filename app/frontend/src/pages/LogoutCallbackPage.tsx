import React, { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import LoadingSpinner from '../components/LoadingSpinner';

const LogoutCallbackPage: React.FC = () => {
  const { t } = useTranslation();

  useEffect(() => {
    // The OIDC provider has logged out the user and redirected here
    // We can redirect to the home page or show a logout success message
    setTimeout(() => {
      window.location.href = '/';
    }, 2000);
  }, []);

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="text-center">
        <div className="mx-auto h-12 w-12 flex items-center justify-center rounded-full bg-green-100 mb-4">
          <svg
            className="h-6 w-6 text-green-600"
            fill="none"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="2"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path d="M5 13l4 4L19 7" />
          </svg>
        </div>
        <h2 className="text-2xl font-bold text-gray-900 mb-2">{t('auth.logoutTitle')}</h2>
        <p className="text-gray-600 mb-4">{t('auth.logoutDesc')}</p>
        <p className="text-sm text-gray-500">{t('auth.logoutRedirecting')}</p>
      </div>
    </div>
  );
};

export default LogoutCallbackPage;
