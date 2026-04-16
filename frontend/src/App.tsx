import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "@/lib/auth";
import Layout from "@/components/Layout";
import DealList from "@/pages/DealList";
import DealDetail from "@/pages/DealDetail";
import Upload from "@/pages/Upload";
import Criteria from "@/pages/Criteria";
import Login from "@/pages/Login";

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="min-h-screen flex items-center justify-center text-muted-foreground">Loading...</div>;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function PublicOnlyRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (user) return <Navigate to="/" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<PublicOnlyRoute><Login /></PublicOnlyRoute>} />
          <Route element={<ProtectedRoute><Layout /></ProtectedRoute>}>
            <Route path="/" element={<DealList />} />
            <Route path="/deals/:id" element={<DealDetail />} />
            <Route path="/upload" element={<Upload />} />
            <Route path="/criteria" element={<Criteria />} />
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
