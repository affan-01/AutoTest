import { NavLink, Route, Routes } from 'react-router-dom'
import HomePage from './pages/HomePage'
import TicketPage from './pages/TicketPage'
import TracesPage from './pages/TracesPage'
import ConfigPage from './pages/ConfigPage'

function TopNav() {
  const linkClass = ({ isActive }: { isActive: boolean }) => (isActive ? 'active' : '')
  return (
    <nav className="topnav">
      <NavLink to="/" end className={linkClass}>Tickets</NavLink>
      <NavLink to="/traces" className={linkClass}>Traces</NavLink>
      <NavLink to="/config" className={linkClass}>Configure</NavLink>
    </nav>
  )
}

export default function App() {
  return (
    <div className="app-shell">
      <TopNav />
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/tickets/:ticketId" element={<TicketPage />} />
        <Route path="/traces" element={<TracesPage />} />
        <Route path="/config" element={<ConfigPage />} />
      </Routes>
    </div>
  )
}
