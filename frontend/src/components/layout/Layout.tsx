import { useState } from "react";
import { Outlet } from "react-router-dom";
import { Menu, X } from "lucide-react";
import { Sidebar } from "./Sidebar";

export function Layout() {
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);

  return (
    <div className="flex h-screen bg-background text-foreground font-sans scroll-smooth">
      {/* Desktop Sidebar */}
      <div className="hidden md:block shadow-[1px_0_0_0_rgba(0,0,0,0.05)] z-10 relative">
        <Sidebar />
      </div>

      {/* Mobile Header / Hamburger */}
      <div className="md:hidden fixed top-0 w-full z-50 bg-card/85 backdrop-blur-md border-b border-border shadow-sm flex items-center justify-between p-4 h-14">
        <div className="flex items-center gap-2">
           <div className="w-7 h-7 bg-primary rounded-md flex items-center justify-center text-primary-foreground font-bold text-sm tracking-tighter">
             VX
           </div>
           <h1 className="text-sm font-bold text-foreground tracking-tight">Vauxtra</h1>
        </div>
        <button
          onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
          className="text-muted-foreground hover:text-foreground focus:outline-none p-1 rounded-md hover:bg-accent transition-colors"
        >
          {isMobileMenuOpen ? <X size={20} /> : <Menu size={20} />}
        </button>
      </div>

      {/* Mobile Sidebar Overlay */}
      {isMobileMenuOpen && (
        <div className="md:hidden fixed inset-0 z-40 bg-background/70 backdrop-blur-sm transition-opacity">
          <div className="fixed inset-y-0 left-0 w-72 bg-card shadow-2xl animate-in slide-in-from-left duration-200">
            <Sidebar isMobile />
          </div>
          {/* Invisible click-away zone */}
          <div className="fixed inset-y-0 right-0 w-[calc(100%-18rem)]" onClick={() => setIsMobileMenuOpen(false)}></div>
        </div>
      )}

      {/* Main Content Area */}
      <main className="flex-1 overflow-x-hidden overflow-y-auto scroll-smooth">
        <div className="mt-14 md:mt-0 p-4 sm:p-6 lg:p-8 min-h-[calc(100vh)]">
           <Outlet />
        </div>
      </main>
    </div>
  );
}
