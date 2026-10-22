import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider, useAuth } from '@/lib/auth'
import { Layout } from '@/components/Layout'
import { Loading } from '@/components/primitives'
import { LoginPage } from '@/pages/Login'
import { OverviewPage } from '@/pages/Overview'
import { RepositoriesPage } from '@/pages/Repositories'
import { RepositoryDetailPage } from '@/pages/RepositoryDetail'
import { PipelinesPage } from '@/pages/Pipelines'
import { PipelineDetailPage } from '@/pages/PipelineDetail'
import { DeploymentsPage } from '@/pages/Deployments'
import { DeploymentDetailPage } from '@/pages/DeploymentDetail'
import { EnvironmentsPage } from '@/pages/Environments'
import { MetricsPage } from '@/pages/Metrics'
import { SecurityPage } from '@/pages/Security'
import { AuditPage } from '@/pages/Audit'
import { SettingsPage } from '@/pages/Settings'
import { NotFoundPage } from '@/pages/NotFound'

function RequireAuth() {
  const { isAuthenticated, isLoading } = useAuth()
  if (isLoading) return <Loading label="Checking your session…" />
  if (!isAuthenticated) return <Navigate to="/login" replace />
  return <Layout />
}

export function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<RequireAuth />}>
            <Route index element={<OverviewPage />} />
            <Route path="repositories" element={<RepositoriesPage />} />
            <Route path="repositories/:id" element={<RepositoryDetailPage />} />
            <Route path="pipelines" element={<PipelinesPage />} />
            <Route path="pipelines/:id" element={<PipelineDetailPage />} />
            <Route path="deployments" element={<DeploymentsPage />} />
            <Route path="deployments/:id" element={<DeploymentDetailPage />} />
            <Route path="environments" element={<EnvironmentsPage />} />
            <Route path="metrics" element={<MetricsPage />} />
            <Route path="security" element={<SecurityPage />} />
            <Route path="audit" element={<AuditPage />} />
            <Route path="settings" element={<SettingsPage />} />
          </Route>
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
