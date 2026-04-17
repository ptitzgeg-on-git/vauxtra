import { ArrowLeft, ArrowRight, Container, Plus, Trash2, Loader2 } from 'lucide-react';
import { useDockerEndpoints } from '@/hooks/useDockerEndpoints';

interface DockerStepProps {
  onBack: () => void;
  onContinue: () => void;
}

export function DockerStep({ onBack, onContinue }: DockerStepProps) {
  const {
    endpoints, name, setName, host, setHost, canSubmit, addEndpoint, deleteEndpoint,
  } = useDockerEndpoints();

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-primary/10 text-primary grid place-items-center">
          <Container size={20} />
        </div>
        <div>
          <h2 className="text-xl font-bold text-foreground">Docker Endpoints</h2>
          <p className="text-sm text-muted-foreground">Connect Docker hosts for automatic container discovery.</p>
        </div>
      </div>

      <div className="bg-card border border-border rounded-xl p-6 space-y-5">
        <div className="bg-muted/50 border border-border rounded-lg p-4 space-y-2">
          <p className="text-sm text-muted-foreground">
            Add Docker endpoints to discover running containers and import them as services.
            Three schemes are supported:
          </p>
          <ul className="text-xs text-muted-foreground space-y-1.5">
            <li>
              <span className="font-semibold text-foreground">Local socket</span> —
              <code className="ml-1 bg-muted px-1.5 py-0.5 rounded font-mono">unix:///var/run/docker.sock</code>
              <span className="ml-1">(Vauxtra container must mount the socket; already done in the default <code className="bg-muted px-1 rounded font-mono">docker-compose.yml</code>).</span>
            </li>
            <li>
              <span className="font-semibold text-foreground">TCP</span> —
              <code className="ml-1 bg-muted px-1.5 py-0.5 rounded font-mono">tcp://192.168.1.10:2375</code>
              <span className="ml-1">(plaintext, LAN only) or <code className="bg-muted px-1 rounded font-mono">tcp://host:2376</code> for TLS. Remote host must expose the Docker API via <code className="bg-muted px-1 rounded font-mono">dockerd -H tcp://...</code>.</span>
            </li>
            <li>
              <span className="font-semibold text-foreground">SSH</span> —
              <code className="ml-1 bg-muted px-1.5 py-0.5 rounded font-mono">ssh://user@host</code>
              <span className="ml-1">(requires a passwordless key in Vauxtra's <code className="bg-muted px-1 rounded font-mono">~/.ssh/</code>, and <code className="bg-muted px-1 rounded font-mono">user</code> in the remote <code className="bg-muted px-1 rounded font-mono">docker</code> group).</span>
            </li>
          </ul>
        </div>

        {endpoints.length > 0 && (
          <div className="space-y-2">
            {endpoints.map((ep) => (
              <div key={ep.id} className="flex items-center gap-3 px-4 py-3 rounded-xl bg-background border border-border">
                <Container size={16} className="text-primary shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium">{ep.name}</p>
                  <p className="text-xs text-muted-foreground font-mono truncate">{ep.docker_host}</p>
                </div>
                <button onClick={() => deleteEndpoint.mutate(ep.id)} className="text-muted-foreground hover:text-destructive transition-colors shrink-0">
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Name (e.g. Local Docker)"
              className="bg-background border border-input rounded-lg px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            />
            <input
              value={host}
              onChange={(e) => setHost(e.target.value)}
              placeholder="unix:///var/run/docker.sock"
              className="bg-background border border-input rounded-lg px-4 py-3 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
          <button
            onClick={() => addEndpoint.mutate()}
            disabled={addEndpoint.isPending || !canSubmit}
            className="w-full flex items-center justify-center gap-2 border-2 border-dashed border-border rounded-xl py-3 text-sm font-medium text-muted-foreground hover:text-foreground hover:border-primary/40 hover:bg-primary/5 transition-all disabled:opacity-50"
          >
            {addEndpoint.isPending ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />}
            Add Docker endpoint
          </button>
        </div>
      </div>

      <div className="flex justify-between">
        <button onClick={onBack} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors">
          <ArrowLeft size={14} /> Back
        </button>
        <button onClick={onContinue} className="inline-flex items-center gap-2 bg-primary text-primary-foreground hover:opacity-90 px-6 py-2.5 rounded-xl text-sm font-semibold transition-all">
          Continue <ArrowRight size={16} />
        </button>
      </div>
    </div>
  );
}
