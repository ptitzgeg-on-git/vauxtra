param(
  [ValidateSet("start", "stop", "restart", "status", "logs", "down", "reset")]
  [string]$Action = "status"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ComposeFile = Join-Path $RepoRoot "lab/providers/docker-compose.yml"
$EnvFile = Join-Path $RepoRoot "lab/providers/.env"
$EnvExample = Join-Path $RepoRoot "lab/providers/.env.example"
$StateRoot = Join-Path $RepoRoot "lab/providers/state"
$ProjectName = "vauxtra-providers"

$LabDefaults = [ordered]@{
  TZ = "UTC"
  PIHOLE_WEBPASSWORD = "admin"
  TECHNITIUM_ADMIN_PASSWORD = "admin"
  ADGUARD_ADMIN_USERNAME = "admin"
  ADGUARD_ADMIN_PASSWORD = "adminadmin"
  NPM_INITIAL_EMAIL = "admin@example.com"
  NPM_INITIAL_PASSWORD = "admin"
  TECHNITIUM_DEFAULT_ZONE = "lab.test"
}

if (-not (Test-Path $ComposeFile)) {
  throw "Compose file not found: $ComposeFile"
}

function Read-EnvFile {
  param([string]$Path)

  $values = [ordered]@{}
  if (-not (Test-Path $Path)) {
    return $values
  }

  foreach ($line in Get-Content -Path $Path) {
    if ([string]::IsNullOrWhiteSpace($line) -or $line.TrimStart().StartsWith("#")) {
      continue
    }

    $parts = $line -split "=", 2
    if ($parts.Count -eq 2) {
      $values[$parts[0].Trim()] = $parts[1]
    }
  }

  return $values
}

function Write-EnvFile {
  param(
    [string]$Path,
    [hashtable]$Values
  )

  $content = foreach ($key in $Values.Keys) {
    "$key=$($Values[$key])"
  }
  Set-Content -Path $Path -Value $content
}

function Ensure-LabEnv {
  param([switch]$ForceDefaults)

  $values = [ordered]@{}

  if (Test-Path $EnvFile) {
    foreach ($entry in (Read-EnvFile -Path $EnvFile).GetEnumerator()) {
      $values[$entry.Key] = $entry.Value
    }
  } elseif (Test-Path $EnvExample) {
    foreach ($entry in (Read-EnvFile -Path $EnvExample).GetEnumerator()) {
      $values[$entry.Key] = $entry.Value
    }
  }

  foreach ($entry in $LabDefaults.GetEnumerator()) {
    if ($ForceDefaults -or -not $values.Contains($entry.Key) -or [string]::IsNullOrWhiteSpace($values[$entry.Key])) {
      $values[$entry.Key] = $entry.Value
    }
  }

  Write-EnvFile -Path $EnvFile -Values $values

  if ($ForceDefaults) {
    Write-Host "Reset lab/providers/.env to deterministic test credentials"
  } elseif (-not (Test-Path $EnvFile)) {
    Write-Host "Created lab/providers/.env with default lab credentials"
  }

  return $values
}

$stateDirs = @(
  "lab/providers/state/pihole/etc-pihole",
  "lab/providers/state/pihole/etc-dnsmasq.d",
  "lab/providers/state/adguard/work",
  "lab/providers/state/adguard/conf",
  "lab/providers/state/npm/data",
  "lab/providers/state/npm/letsencrypt",
  "lab/providers/state/technitium/config"
)

foreach ($dir in $stateDirs) {
  $full = Join-Path $RepoRoot $dir
  New-Item -ItemType Directory -Force -Path $full | Out-Null
}

function Reset-LabState {
  if (Test-Path $StateRoot) {
    Remove-Item -Path $StateRoot -Recurse -Force
  }

  foreach ($dir in $stateDirs) {
    $full = Join-Path $RepoRoot $dir
    New-Item -ItemType Directory -Force -Path $full | Out-Null
  }

  Write-Host "Recreated isolated provider lab state under lab/providers/state"
}

function Invoke-Compose {
  param([string[]]$ComposeArgs)
  docker compose --project-name $ProjectName --env-file $EnvFile -f $ComposeFile @ComposeArgs
}

function Test-HttpReady {
  param(
    [string]$Url,
    [int[]]$AllowedStatusCodes = @(200, 302, 400, 401, 403),
    [int]$Attempts = 90
  )

  for ($i = 0; $i -lt $Attempts; $i++) {
    try {
      Invoke-WebRequest -Uri $Url -Method Get -TimeoutSec 2 -MaximumRedirection 0 -UseBasicParsing | Out-Null
      return $true
    } catch {
      if ($_.Exception.Response) {
        $code = [int]$_.Exception.Response.StatusCode.value__
        if ($AllowedStatusCodes -contains $code) {
          return $true
        }
      }
    }

    [System.Threading.Thread]::Sleep(1000)
  }

  return $false
}

function Ensure-PiholePassword {
  param([string]$Password)

  docker exec vauxtra-pihole pihole setpassword $Password | Out-Null
  Write-Host "Pi-hole password forced to the lab default"
}

function Ensure-NpmAdmin {
  param(
    [string]$Email,
    [string]$Password
  )

  if (-not (Test-HttpReady -Url "http://localhost:18082/api/")) {
    Write-Warning "NPM API did not become reachable on http://localhost:18082/api/"
    return
  }

  $health = Invoke-RestMethod -Uri "http://localhost:18082/api/"
  if (-not $health.setup) {
    $payload = @{
      name = "Administrator"
      nickname = "admin"
      email = $Email
      roles = @("admin")
      auth = @{
        type = "password"
        secret = $Password
      }
    } | ConvertTo-Json -Depth 5 -Compress

    try {
      Invoke-RestMethod -Uri "http://localhost:18082/api/users" -Method Post -ContentType "application/json" -Body $payload | Out-Null
      Write-Host "NPM initial admin account created"
    } catch {
      Write-Warning "NPM initial admin bootstrap failed: $($_.Exception.Message)"
    }
  }

  try {
    Invoke-RestMethod -Uri "http://localhost:18082/api/tokens" -Method Post -ContentType "application/json" -Body (@{
      identity = $Email
      secret = $Password
    } | ConvertTo-Json -Compress) | Out-Null
    Write-Host "NPM login verified"
  } catch {
    Write-Warning "NPM login verification failed for $Email"
  }
}

function Ensure-AdGuardInstalled {
  param(
    [string]$Username,
    [string]$Password
  )

  if (-not (Test-HttpReady -Url "http://localhost:13000/control/install/get_addresses" -AllowedStatusCodes @(200, 302, 400))) {
    Write-Warning "AdGuard setup endpoint did not become reachable on http://localhost:13000"
    return
  }

  try {
    $addresses = Invoke-RestMethod -Uri "http://localhost:13000/control/install/get_addresses"
    if ($addresses) {
      $payload = @{
        web = @{ ip = "0.0.0.0"; port = 3000 }
        dns = @{ ip = "0.0.0.0"; port = 53 }
        username = $Username
        password = $Password
      } | ConvertTo-Json -Depth 4 -Compress

      Invoke-RestMethod -Uri "http://localhost:13000/control/install/configure" -Method Post -ContentType "application/json" -Body $payload | Out-Null
      Write-Host "AdGuard first-run install completed with web UI pinned to port 3000"
    }
  } catch {
    if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode.value__ -ne 302) {
      Write-Warning "AdGuard bootstrap failed: $($_.Exception.Message)"
    }
  }
}

function Ensure-TechnitiumZone {
  param(
    [string]$Password,
    [string]$Zone
  )

  if (-not (Test-HttpReady -Url "http://localhost:15389/api/user/login" -AllowedStatusCodes @(200, 401))) {
    Write-Warning "Technitium API did not become reachable on http://localhost:15389"
    return
  }

  try {
    $login = Invoke-RestMethod -Uri "http://localhost:15389/api/user/login" -Method Post -Body @{ user = "admin"; pass = $Password }
    $token = $login.token
    if (-not $token) {
      Write-Warning "Technitium login did not return a token"
      return
    }

    $headers = @{ Authorization = "Bearer $token" }
    $zones = Invoke-RestMethod -Uri ("http://localhost:15389/api/zones/list?token={0}" -f $token) -Headers $headers
    $existing = @($zones.response.zones | ForEach-Object { $_.name })
    if ($existing -notcontains $Zone) {
      Invoke-RestMethod -Uri ("http://localhost:15389/api/zones/create?zone={0}&type=Primary&token={1}" -f $Zone, $token) -Headers $headers | Out-Null
      Write-Host "Technitium primary zone '$Zone' created"
    } else {
      Write-Host "Technitium primary zone '$Zone' already present"
    }
  } catch {
    Write-Warning "Technitium zone bootstrap failed: $($_.Exception.Message)"
  }
}

function Bootstrap-Lab {
  param([hashtable]$EnvValues)

  Ensure-PiholePassword -Password $EnvValues["PIHOLE_WEBPASSWORD"]
  Ensure-NpmAdmin -Email $EnvValues["NPM_INITIAL_EMAIL"] -Password $EnvValues["NPM_INITIAL_PASSWORD"]
  Ensure-AdGuardInstalled -Username $EnvValues["ADGUARD_ADMIN_USERNAME"] -Password $EnvValues["ADGUARD_ADMIN_PASSWORD"]
  Ensure-TechnitiumZone -Password $EnvValues["TECHNITIUM_ADMIN_PASSWORD"] -Zone $EnvValues["TECHNITIUM_DEFAULT_ZONE"]
}

function Show-LabAccess {
  param([hashtable]$EnvValues)

  Write-Host ""
  Write-Host "Providers lab access summary"
  Write-Host "  Pi-hole      : http://localhost:18081/admin  password=$($EnvValues['PIHOLE_WEBPASSWORD'])"
  Write-Host "  AdGuard Home : http://localhost:13000        user=$($EnvValues['ADGUARD_ADMIN_USERNAME']) password=$($EnvValues['ADGUARD_ADMIN_PASSWORD'])"
  Write-Host "  NPM          : http://localhost:18082        user=$($EnvValues['NPM_INITIAL_EMAIL']) password=$($EnvValues['NPM_INITIAL_PASSWORD'])"
  Write-Host "  Technitium   : http://localhost:15389        user=admin password=$($EnvValues['TECHNITIUM_ADMIN_PASSWORD']) zone=$($EnvValues['TECHNITIUM_DEFAULT_ZONE'])"
  Write-Host "  Traefik      : http://localhost:18090"
  Write-Host "  Whoami       : http://localhost:18083"
  Write-Host ""
}

$envValues = $null

switch ($Action) {
  "start" {
    $envValues = Ensure-LabEnv
    Invoke-Compose -ComposeArgs @("up", "-d")
    Bootstrap-Lab -EnvValues $envValues
    Invoke-Compose -ComposeArgs @("ps")
    Show-LabAccess -EnvValues $envValues
  }
  "stop" {
    $envValues = Ensure-LabEnv
    Invoke-Compose -ComposeArgs @("stop")
    Invoke-Compose -ComposeArgs @("ps")
  }
  "restart" {
    $envValues = Ensure-LabEnv
    Invoke-Compose -ComposeArgs @("down")
    Invoke-Compose -ComposeArgs @("up", "-d")
    Bootstrap-Lab -EnvValues $envValues
    Invoke-Compose -ComposeArgs @("ps")
    Show-LabAccess -EnvValues $envValues
  }
  "status" {
    $envValues = Ensure-LabEnv
    Invoke-Compose -ComposeArgs @("ps")
    Show-LabAccess -EnvValues $envValues
  }
  "logs" {
    $envValues = Ensure-LabEnv
    Invoke-Compose -ComposeArgs @("logs", "--tail", "120")
  }
  "down" {
    $envValues = Ensure-LabEnv
    Invoke-Compose -ComposeArgs @("down")
  }
  "reset" {
    $envValues = Ensure-LabEnv -ForceDefaults
    Invoke-Compose -ComposeArgs @("down")
    Reset-LabState
    Invoke-Compose -ComposeArgs @("up", "-d")
    Bootstrap-Lab -EnvValues $envValues
    Invoke-Compose -ComposeArgs @("ps")
    Show-LabAccess -EnvValues $envValues
  }
}
