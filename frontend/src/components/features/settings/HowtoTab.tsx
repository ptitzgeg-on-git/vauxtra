export function HowtoTab() {
  return (
    <div className="space-y-4">
      {/* Concepts */}
      <div className="bg-card border border-border rounded-xl p-6 shadow-sm space-y-4">
        <h3 className="font-semibold text-lg">Key Concepts</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
          <div className="space-y-1">
            <p className="font-semibold text-foreground">Drift detection</p>
            <p className="text-muted-foreground">Compares the expected state stored in Vauxtra with the live state in the provider. If they diverge (e.g. someone edited NPM directly), drift is reported.</p>
          </div>
          <div className="space-y-1">
            <p className="font-semibold text-foreground">Reconcile</p>
            <p className="text-muted-foreground">Re-pushes the expected configuration to a provider to fix drift. Vauxtra's stored state wins.</p>
          </div>
          <div className="space-y-1">
            <p className="font-semibold text-foreground">DNS public target</p>
            <p className="text-muted-foreground">The IP or hostname that the DNS A/CNAME record will point to. This is your reverse proxy's public or LAN IP — not the internal app host. For internet-facing services, this is typically your WAN IP.</p>
          </div>
          <div className="space-y-1">
            <p className="font-semibold text-foreground">Tunnel mode</p>
            <p className="text-muted-foreground">Uses Cloudflare Tunnel to route traffic without opening any port on your router. Vauxtra manages ingress rules inside the tunnel. Requires a Cloudflare Tunnel provider to be configured first.</p>
          </div>
        </div>
      </div>

      {/* Provider setup guides */}
      <div className="bg-card border border-border rounded-xl p-6 shadow-sm space-y-4">
        <h3 className="font-semibold text-lg">Provider Setup Guides</h3>
        {[
          { name: 'Cloudflare Tunnel', color: 'bg-orange-500', steps: [
            'Go to <code>dash.cloudflare.com → Zero Trust → Networks → Tunnels</code> → Create a tunnel (Cloudflared connector).',
            'Vauxtra manages tunnel ingress routes — it does not run cloudflared. Install the cloudflared daemon separately using the token shown by Cloudflare.',
            'Copy the Tunnel ID (UUID) from the tunnel overview page.',
            'Create an API Token: <code>My Profile → API Tokens → Create Token</code>. Required: <code>Account → Cloudflare Tunnel → Edit</code> + <code>Zone → DNS → Edit</code>.',
            'Copy your Account ID from the right sidebar of any Cloudflare dashboard page.',
          ]},
          { name: 'Cloudflare DNS', color: 'bg-orange-500', steps: [
            'Open the Cloudflare zone you want Vauxtra to manage.',
            'Create an API Token with <code>Zone → DNS → Edit</code> on your zone.',
            'Optional: copy the Zone ID from the zone overview right sidebar.',
          ]},
          { name: 'Nginx Proxy Manager', color: 'bg-green-500', steps: [
            'In NPM: Users → Add User. Create a dedicated user with "Manage Proxy Hosts" permission.',
            'URL = <code>http://npm:81</code>. Enter the user\'s email and password.',
          ]},
          { name: 'Traefik', color: 'bg-blue-500', steps: [
            '<strong>Read-only:</strong> Vauxtra reads Traefik config but does not write it.',
            'Expose the Traefik API at <code>http://traefik:8080</code> (<code>--api.insecure=true</code> or a dedicated router).',
            'Credentials are optional unless you configured BasicAuth on the dashboard.',
          ]},
          { name: 'Pi-hole', color: 'bg-red-500', steps: [
            'In Pi-hole: Settings → API / Web interface → Show API token. Or use your admin password.',
            'URL is typically <code>http://pihole/admin</code>.',
          ]},
          { name: 'AdGuard Home', color: 'bg-teal-500', steps: [
            'AdGuard uses Basic Auth — same credentials as the web admin panel.',
            'URL is typically <code>http://adguard:3000</code>.',
          ]},
        ].map(({ name, color, steps }) => (
          <details key={name} className="group border border-border rounded-lg overflow-hidden">
            <summary className="flex items-center justify-between px-4 py-3 cursor-pointer bg-muted/30 hover:bg-muted/60 font-semibold text-sm select-none">
              <span className="flex items-center gap-2"><span className={`w-2 h-2 rounded-full ${color}`} />{name}</span>
              <span className="text-muted-foreground text-xs">▼</span>
            </summary>
            <div className="px-4 py-3 space-y-2 text-sm text-muted-foreground border-t border-border">
              {steps.map((step, i) => (
                <p key={i} dangerouslySetInnerHTML={{ __html: `<strong class="text-foreground">${i + 1}.</strong> ${step}` }} />
              ))}
            </div>
          </details>
        ))}
      </div>

      {/* Docker & Traefik tips */}
      <div className="bg-card border border-border rounded-xl p-6 shadow-sm space-y-3">
        <h3 className="font-semibold text-lg">Docker &amp; Traefik Tips</h3>
        <div className="space-y-2 text-sm text-foreground">
          <p><strong>Multiple Docker hosts:</strong> add each host as a Docker endpoint in Integrations, set one as default, then discover per endpoint.</p>
          <p><strong>No sidecar needed:</strong> Vauxtra connects directly to Docker daemon endpoints (socket, tcp, or ssh).</p>
          <p><strong>Traefik with compose:</strong> Traefik stays in your docker compose stack; Vauxtra connects to the Traefik API as a read-only provider.</p>
          <p><strong>Several Traefik instances:</strong> add one provider entry per Traefik instance in Integrations.</p>
        </div>
      </div>

      {/* MCP */}
      <div className="bg-card border border-border rounded-xl p-6 shadow-sm space-y-3">
        <h3 className="font-semibold text-lg">MCP Server (AI Integration)</h3>
        <p className="text-sm text-muted-foreground">
          Vauxtra ships a built-in MCP server exposing all operations as tools for AI assistants (Claude Desktop, Cursor, etc.).
        </p>
        <div className="space-y-1 text-sm text-foreground">
          <p><strong>1.</strong> Create an API key in Settings → API Keys → New Key.</p>
          <p><strong>2.</strong> Set <code className="bg-muted px-1 rounded">VAUXTRA_URL</code> and <code className="bg-muted px-1 rounded">VAUXTRA_API_KEY</code> environment variables.</p>
          <p><strong>3.</strong> Claude Desktop config:</p>
        </div>
        <pre className="bg-muted rounded-lg p-3 text-xs font-mono overflow-x-auto whitespace-pre">{`{
  "mcpServers": {
    "vauxtra": {
      "command": "python",
      "args": ["-m", "vauxtra_mcp.server"],
      "cwd": "/path/to/vauxtra",
      "env": {
        "VAUXTRA_URL": "http://localhost:8888",
        "VAUXTRA_API_KEY": "vx_your_key_here"
      }
    }
  }
}`}</pre>
      </div>

      {/* API endpoints */}
      <div className="bg-card border border-border rounded-xl p-6 shadow-sm space-y-3">
        <h3 className="font-semibold text-lg">API Endpoints Quick Map</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-sm">
          {['GET /api/health', 'GET /api/services', 'POST /api/services', 'PUT /api/services/:id',
            'POST /api/services/:id/push', 'POST /api/services/:id/reconcile', 'GET /api/providers',
            'POST /api/providers', 'GET /api/docker/endpoints', 'GET /api/docker/containers',
            'POST /api/docker/import', 'GET /api/logs', 'GET /api/backup',
          ].map(ep => <p key={ep} className="bg-muted rounded-md px-3 py-2">{ep}</p>)}
        </div>
        <p className="text-xs text-muted-foreground">Full interactive docs at <code className="bg-muted px-1 rounded">/api/docs</code> when <code className="bg-muted px-1 rounded">DEBUG=true</code>.</p>
      </div>
    </div>
  );
}
