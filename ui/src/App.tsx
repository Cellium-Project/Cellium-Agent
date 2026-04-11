import React, { useEffect } from 'react';
import { Sidebar } from './components/Sidebar';
import { ChatView } from './components/ChatView';
import { SettingsPage } from './components/SettingsPage';
import { useAppStore } from './stores/appStore';
import { API, fetchJSON } from './utils/api';

const App: React.FC = () => {
  const {
    setStatusOnline,
    setCurrentSessionId,
    fetchMessages,
    fetchSessions,
    showSettingsPage,
  } = useAppStore();

  // Health check
  useEffect(() => {
    const checkHealth = async () => {
      try {
        const data = await fetchJSON<{ status: string }>(API.health);
        setStatusOnline(data.status === 'ok');
      } catch {
        setStatusOnline(false);
      }
    };

    checkHealth();
    const interval = setInterval(checkHealth, 30000);
    return () => clearInterval(interval);
  }, [setStatusOnline]);

  // Initialize session
  useEffect(() => {
    const initSession = async () => {
      try {
        // Get last active session
        const data = await fetchJSON<{ session_id: string | null; exists: boolean }>(
          API.sessionLast
        );

        if (data.session_id && data.exists) {
          setCurrentSessionId(data.session_id);
          await fetchMessages(data.session_id, 0);
        } else {
          // No existing session, create new one
          const createData = await fetchJSON<{ session_id: string }>(
            API.sessionCreate,
            { method: 'POST' }
          );
          setCurrentSessionId(createData.session_id);
        }

        fetchSessions();
      } catch (error) {
        console.error('Failed to initialize session:', error);
      }
    };

    initSession();
  }, [setCurrentSessionId, fetchMessages, fetchSessions]);

  return (
    <div className="app-layout">
      <Sidebar />
      <div className="main-content">
        <div style={{ display: showSettingsPage ? 'none' : 'block', flex: 1 }}>
          <ChatView />
        </div>
        {showSettingsPage && <SettingsPage />}
      </div>
    </div>
  );
};

export default App;
