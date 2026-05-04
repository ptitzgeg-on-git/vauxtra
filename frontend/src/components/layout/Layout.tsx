import { useState, useEffect } from "react";
import { Outlet, useNavigate } from "react-router-dom";
import { Menu, X, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { Sidebar } from "./Sidebar";

const STORAGE_KEY = "vauxtra_sidebar_collapsed";

export function Layout() {
  const navigate = useNavigate();
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem(STORAGE_KEY) === "true"; } catch { return false; }
  });

  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, String(collapsed)); } catch { /* ignore */ }
  }, [collapsed]);

  useEffect(() => {
    let awaitingSecondKey = false;
    let resetTimer: number | null = null;

    const resetCombo = () => {
      awaitingSecondKey = false;
      if (resetTimer !== null) {
        window.clearTimeout(resetTimer);
        resetTimer = null;
      }
    };

    const isTypingTarget = (el: EventTarget | null) => {
      if (!(el instanceof HTMLElement)) return false;
      const tag = el.tagName.toLowerCase();
      return tag === "input" || tag === "textarea" || tag === "select" || el.isContentEditable;
    };

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      if (isTypingTarget(event.target)) return;

      const key = event.key.toLowerCase();

      if (!awaitingSecondKey) {
        if (key === "g") {
          awaitingSecondKey = true;
          resetTimer = window.setTimeout(resetCombo, 1200);
        }
        return;
      }

      if (key === "d") {
        navigate("/");
        event.preventDefault();
        resetCombo();
        return;
      }
      if (key === "p") {
        navigate("/providers");
        event.preventDefault();
        resetCombo();
        return;
      }
      if (key === "s") {
        navigate("/settings?tab=general");
        event.preventDefault();
        resetCombo();
        return;
      }

      resetCombo();
    };

    window.addEventListener("keydown", onKeyDown);
    return () => {
      resetCombo();
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [navigate]);

  return (
    <div className="flex h-screen bg-background text-foreground font-sans scroll-smooth">
      {/* Desktop Sidebar */}
      <div
        className={`hidden md:block shadow-[1px_0_0_0_rgba(0,0,0,0.05)] z-10 relative transition-all duration-200 ${
          collapsed ? "w-[60px]" : "w-72"
        }`}
      >
        <Sidebar collapsed={collapsed} onToggleCollapse={() => setCollapsed((v) => !v)} />
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
            <Sidebar isMobile onToggleCollapse={() => {}} />
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

      {/* Collapse toggle button — visible on desktop, outside sidebar edge */}
      <button
        onClick={() => setCollapsed((v) => !v)}
        className="hidden md:flex fixed bottom-8 left-0 z-20 items-center justify-center w-5 h-10 bg-card border border-border rounded-r-lg shadow-sm text-muted-foreground hover:text-foreground hover:bg-accent transition-all duration-200"
        style={{ left: collapsed ? "52px" : "276px" }}
        title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
      >
        {collapsed ? <PanelLeftOpen size={13} /> : <PanelLeftClose size={13} />}
      </button>
    </div>
  );
}
