import { Link, Outlet, useLocation } from "react-router-dom";
import { BarChart3, Upload, Settings, FileText } from "lucide-react";
import { cn } from "@/lib/utils";

const navItems = [
  { to: "/", label: "Deals", icon: FileText },
  { to: "/upload", label: "Upload", icon: Upload },
  { to: "/criteria", label: "Criteria", icon: Settings },
];

export default function Layout() {
  const location = useLocation();

  return (
    <div className="min-h-screen bg-muted/30">
      <header className="border-b bg-background sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-6 flex items-center h-14 gap-8">
          <Link to="/" className="flex items-center gap-2 font-semibold text-lg">
            <BarChart3 className="h-5 w-5 text-primary" />
            Deal Screener
          </Link>
          <nav className="flex gap-1">
            {navItems.map(({ to, label, icon: Icon }) => (
              <Link
                key={to}
                to={to}
                className={cn(
                  "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-colors",
                  location.pathname === to
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                )}
              >
                <Icon className="h-4 w-4" />
                {label}
              </Link>
            ))}
          </nav>
        </div>
      </header>
      <main className="max-w-7xl mx-auto px-6 py-6">
        <Outlet />
      </main>
    </div>
  );
}
