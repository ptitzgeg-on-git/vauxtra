import { Zap, Upload } from 'lucide-react';

export function WelcomeStep({ onFreshInstall, onRestore }: { onFreshInstall: () => void; onRestore: () => void }) {
  return (
    <div className="text-center space-y-6 animate-in fade-in duration-500">
      <div className="w-20 h-20 rounded-2xl bg-primary text-primary-foreground grid place-items-center font-extrabold text-3xl shadow-lg mx-auto">
        VX
      </div>
      <div>
        <h1 className="text-3xl font-extrabold tracking-tight text-foreground">Welcome to Vauxtra</h1>
        <p className="text-muted-foreground mt-2 text-lg">
          DNS and reverse proxy control plane for your homelab.
        </p>
      </div>
      
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 max-w-lg mx-auto">
        <button
          onClick={onFreshInstall}
          className="flex flex-col items-center gap-3 p-6 rounded-xl border-2 border-transparent bg-card hover:border-primary hover:bg-primary/5 transition-all text-center"
        >
          <div className="p-3 rounded-xl bg-primary/10 text-primary">
            <Zap className="w-6 h-6" />
          </div>
          <div>
            <p className="font-semibold text-foreground">Fresh Install</p>
            <p className="text-xs text-muted-foreground mt-1">
              Set up Vauxtra from scratch
            </p>
          </div>
        </button>
        
        <button
          onClick={onRestore}
          className="flex flex-col items-center gap-3 p-6 rounded-xl border-2 border-transparent bg-card hover:border-primary hover:bg-primary/5 transition-all text-center"
        >
          <div className="p-3 rounded-xl bg-muted text-muted-foreground">
            <Upload className="w-6 h-6" />
          </div>
          <div>
            <p className="font-semibold text-foreground">Restore Backup</p>
            <p className="text-xs text-muted-foreground mt-1">
              Restore from a previous export
            </p>
          </div>
        </button>
      </div>
      
      <p className="text-xs text-muted-foreground max-w-md mx-auto">
        You can export a backup from Settings → Backup at any time.
      </p>
    </div>
  );
}
