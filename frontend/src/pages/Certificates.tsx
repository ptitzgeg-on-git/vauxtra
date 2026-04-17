import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { Shield, CalendarClock, ShieldCheck, AlertTriangle, Clock, CheckCircle2, WifiOff, RefreshCw } from "lucide-react";
import type { Certificate } from "@/types/api";

function getDaysUntilExpiry(expiresOn: string | null | undefined): number | null {
  if (!expiresOn) return null;
  const expiry = new Date(expiresOn);
  if (isNaN(expiry.getTime())) return null;
  const now = new Date();
  return Math.ceil((expiry.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));
}

function getExpiryStatus(days: number | null): { color: string; label: string; bgColor: string } {
  if (days === null) return { color: 'text-muted-foreground', label: 'Unknown', bgColor: 'bg-muted' };
  if (days < 0) return { color: 'text-destructive', label: 'Expired', bgColor: 'bg-destructive/10' };
  if (days <= 7) return { color: 'text-destructive', label: 'Critical', bgColor: 'bg-destructive/10' };
  if (days <= 30) return { color: 'text-yellow-600 dark:text-yellow-400', label: 'Expiring soon', bgColor: 'bg-yellow-500/10' };
  return { color: 'text-emerald-600 dark:text-emerald-400', label: 'Valid', bgColor: 'bg-emerald-500/10' };
}

export function Certificates() {
  const { data: certificates, isLoading, isError, refetch } = useQuery<Certificate[]>({
    queryKey: ['certificates'],
    queryFn: () => api.get<Certificate[]>('/certificates'),
  });

  if (isLoading) {
    return (
      <div className="space-y-6 max-w-7xl mx-auto">
        <div className="h-8 w-64 bg-muted rounded animate-pulse" />
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {[1, 2, 3].map(i => <div key={i} className="h-24 bg-card rounded-xl border border-border animate-pulse" />)}
        </div>
        <div className="h-64 bg-card rounded-xl border border-border animate-pulse" />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="space-y-6 max-w-7xl mx-auto">
        <h1 className="text-2xl font-bold tracking-tight text-foreground">Certificates</h1>
        <div className="flex items-center gap-3 px-4 py-3 rounded-lg border border-destructive/30 bg-destructive/5 text-destructive text-sm">
          <WifiOff className="w-4 h-4 shrink-0" />
          <span className="font-medium">Unable to load certificates from the backend.</span>
          <button onClick={() => refetch()} className="ml-auto flex items-center gap-1.5 text-xs font-semibold hover:underline">
            <RefreshCw className="w-3.5 h-3.5" /> Retry
          </button>
        </div>
      </div>
    );
  }

  const items: Certificate[] = Array.isArray(certificates) ? certificates : [];
  
  // Calculate stats
  const validCerts = items.filter(c => {
    const days = getDaysUntilExpiry(c.expires_on);
    return days !== null && days > 30;
  }).length;
  const expiringSoon = items.filter(c => {
    const days = getDaysUntilExpiry(c.expires_on);
    return days !== null && days > 0 && days <= 30;
  }).length;
  const expired = items.filter(c => {
    const days = getDaysUntilExpiry(c.expires_on);
    return days !== null && days <= 0;
  }).length;

  return (
    <div className="space-y-4 pb-8 animate-in fade-in duration-200">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-baseline gap-3">
          <h1 className="text-2xl font-bold tracking-tight text-foreground">Certificates</h1>
          <span className="text-sm text-muted-foreground font-medium">{items.length} total</span>
        </div>
      </div>

      {/* Stats cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div className="bg-card p-4 rounded-lg border border-border flex items-center gap-3">
          <div className="p-2 rounded-lg bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
            <CheckCircle2 className="w-5 h-5" />
          </div>
          <div>
            <p className="text-xs font-medium text-muted-foreground">Valid</p>
            <p className="text-xl font-bold text-foreground">{validCerts}</p>
          </div>
        </div>

        <div className="bg-card p-4 rounded-lg border border-border flex items-center gap-3">
          <div className="p-2 rounded-lg bg-yellow-500/10 text-yellow-600 dark:text-yellow-400">
            <Clock className="w-5 h-5" />
          </div>
          <div>
            <p className="text-xs font-medium text-muted-foreground">Expiring soon</p>
            <p className="text-xl font-bold text-foreground">{expiringSoon}</p>
          </div>
        </div>

        <div className="bg-card p-4 rounded-lg border border-border flex items-center gap-3">
          <div className="p-2 rounded-lg bg-destructive/10 text-destructive">
            <AlertTriangle className="w-5 h-5" />
          </div>
          <div>
            <p className="text-xs font-medium text-muted-foreground">Expired</p>
            <p className="text-xl font-bold text-foreground">{expired}</p>
          </div>
        </div>
      </div>

      {/* Certificate list */}
      <section className="bg-card border border-border rounded-lg overflow-hidden">
        <div className="px-4 py-3 border-b border-border flex items-center justify-between">
          <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
            <Shield className="w-4 h-4" />
            All Certificates
          </h3>
        </div>

        {items.length === 0 ? (
          <div className="p-12 flex flex-col items-center justify-center">
            <Shield className="w-12 h-12 text-muted-foreground opacity-50 mb-4" />
            <h3 className="text-lg font-semibold text-foreground">No Certificates Found</h3>
            <p className="text-sm text-muted-foreground mt-1 text-center max-w-sm">
              Certificates are synced from your reverse proxy providers. Connect NPM or Traefik to see certificates here.
            </p>
          </div>
        ) : (
          <div className="divide-y divide-border/70">
            {items.map((cert) => {
              const daysLeft = getDaysUntilExpiry(cert.expires_on);
              const status = getExpiryStatus(daysLeft);
              const domains = cert.domain_names?.join(', ') || cert.nice_name || 'Certificate';
              const isWildcard = domains.includes('*.');
              
              return (
                <div key={cert.id} className="px-5 py-4 flex items-center justify-between gap-4 hover:bg-muted/30 transition-colors">
                  <div className="flex items-center gap-4 min-w-0">
                    <div className={`p-2.5 rounded-lg ${status.bgColor}`}>
                      <ShieldCheck className={`w-5 h-5 ${status.color}`} />
                    </div>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <h3 className="font-semibold text-foreground truncate">{domains}</h3>
                        {isWildcard && (
                          <span className="text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-600 dark:text-purple-400 border border-purple-500/20">
                            Wildcard
                          </span>
                        )}
                        {cert.provider && (
                          <span className="text-[10px] font-semibold px-2 py-0.5 rounded-md bg-primary/10 text-primary border border-primary/20">
                            via {cert.provider}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center text-xs text-muted-foreground gap-3 mt-1">
                        <span className="flex items-center gap-1">
                          <CalendarClock className="w-3 h-3" />
                          {cert.expires_on ? `Expires ${cert.expires_on}` : 'No expiry date'}
                        </span>
                        {cert.issuer && (
                          <span className="hidden sm:inline">Issuer: {cert.issuer}</span>
                        )}
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-3 shrink-0">
                    <div className="text-right hidden sm:block">
                      {daysLeft !== null && (
                        <p className={`text-sm font-semibold ${status.color}`}>
                          {daysLeft < 0 ? `${Math.abs(daysLeft)}d overdue` : `${daysLeft}d left`}
                        </p>
                      )}
                    </div>
                    <span className={`inline-flex items-center px-2.5 py-1 rounded-md text-xs font-semibold ${status.bgColor} ${status.color} border border-current/20`}>
                      {status.label}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
