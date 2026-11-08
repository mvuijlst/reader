<#
.SYNOPSIS
  One-time setup for deploy-on-push, from Windows PowerShell or pwsh.
  (deploy/setup-github-deploy.sh does the same from macOS, Linux or Git Bash.)

.DESCRIPTION
  Creates a fresh ed25519 deploy key, stores the private half and the server's
  host key as GitHub Actions secrets, deletes the private key locally, and prints
  the line to add to the server's ~/.ssh/authorized_keys (it's also copied to the
  clipboard). That line pins the key to deploy/update.sh, so it can deploy and do
  nothing else.

  Needs Windows' built-in OpenSSH client (ssh-keygen, ssh-keyscan) and a logged-in
  GitHub CLI (gh auth login).

  The host can be an alias from your ~/.ssh/config (whatever works for `ssh <host>`):
  it is resolved to the real address, which is what GitHub Actions needs.

.EXAMPLE
  .\deploy\setup-github-deploy.ps1 myvps 2708

.EXAMPLE
  .\deploy\setup-github-deploy.ps1 -VpsHost 203.0.113.10 -Port 2708 -DryRun
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)][string]$VpsHost,
    # SSH port; 0 = whatever your SSH config says for this host (else 22).
    [Parameter(Position = 1)][int]$Port = 0,
    [Parameter(Position = 2)][string]$User = 'django',
    [string]$Repo = 'mvuijlst/reader',
    [string]$AppDir,
    # Do everything except storing the secrets.
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
if (-not $AppDir) { $AppDir = "/home/$User/reader-yusupov/app" }

# Run a native program. Windows PowerShell 5.1 turns anything a program writes to
# stderr into an error record, which 'Stop' would make fatal even when redirected;
# exit codes ($LASTEXITCODE) are what we check instead.
function Native([scriptblock]$Command) {
    $ErrorActionPreference = 'Continue'
    & $Command
}

foreach ($tool in 'ssh', 'ssh-keygen', 'gh') {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "$tool not found. ssh/ssh-keygen come with Windows' OpenSSH Client (Settings > Optional features); gh from https://cli.github.com."
    }
}
if (-not $DryRun) {
    Native { gh auth status *> $null }
    if ($LASTEXITCODE) { throw "gh isn't logged in. Run 'gh auth login' first." }
}

# Resolve SSH config aliases to the real address and port, exactly as `ssh` would.
$sshArgs = @('-G') + $(if ($Port) { @('-p', "$Port") } else { @() }) + @($VpsHost)
$config = Native { ssh @sshArgs 2>$null }
$address = ($config | Where-Object { $_ -like 'hostname *' } | Select-Object -First 1) -replace '^hostname ', ''
$configPort = ($config | Where-Object { $_ -like 'port *' } | Select-Object -First 1) -replace '^port ', ''
$Port = if ($configPort) { [int]$configPort } elseif ($Port) { $Port } else { 22 }
if (-not $address) { $address = $VpsHost }
if ($address -ne $VpsHost) { Write-Host "$VpsHost is $address in your SSH config." }

# The server's host key. Windows' bundled ssh-keyscan can't talk to current
# OpenSSH servers ("unsupported KEX method"), so prefer Git for Windows' copy.
Write-Host "Fetching the server's host key from ${address}:$Port ..."
$scanners = @("$env:ProgramFiles\Git\usr\bin\ssh-keyscan.exe") +
    @(Get-Command ssh-keyscan -All -ErrorAction SilentlyContinue | ForEach-Object Source)
$known = ''
foreach ($scanner in ($scanners | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique)) {
    $known = ((Native { & $scanner -p $Port -t ed25519 $address 2>$null }) -join "`n").Trim()
    if ($known) { break }
}
if (-not $known) {
    # Last resort: the key your own known_hosts already trusts.
    $lookup = if ($Port -eq 22) { $address } else { "[$address]:$Port" }
    $known = ((Native { ssh-keygen -F $lookup 2>$null } | Where-Object { $_ -and $_ -notlike '#*' }) -join "`n").Trim()
}
if (-not $known) {
    throw "Couldn't get the host key of ${address}:$Port. Check the host and port (try: ssh -p $Port $address)."
}

$tmp = Join-Path ([IO.Path]::GetTempPath()) ("reader-deploy-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    $key = Join-Path $tmp 'key'
    # An empty passphrase: PowerShell 7.3+ passes '' through to native programs,
    # older versions drop it unless it's spelled '""'.
    $noPassphrase = if ($PSVersionTable.PSVersion -ge [version]'7.3') { '' } else { '""' }
    Native { ssh-keygen -q -t ed25519 -N $noPassphrase -C 'reader-deploy@github-actions' -f $key }
    if ($LASTEXITCODE -or -not (Test-Path "$key.pub")) { throw 'ssh-keygen failed.' }

    $values = [ordered]@{
        DEPLOY_KNOWN_HOSTS = $known
        DEPLOY_HOST        = $address
        DEPLOY_PORT        = "$Port"
        DEPLOY_USER        = $User
    }
    if ($DryRun) {
        Write-Host "Dry run: would set DEPLOY_SSH_KEY, $($values.Keys -join ', ') in $Repo."
    } else {
        # The private key goes in over stdin, not as an argument, so it never
        # shows up in a process list.
        Native { Get-Content -Raw $key | gh secret set DEPLOY_SSH_KEY --repo $Repo }
        if ($LASTEXITCODE) { throw 'Storing DEPLOY_SSH_KEY failed.' }
        foreach ($name in $values.Keys) {
            Native { gh secret set $name --repo $Repo --body $values[$name] }
            if ($LASTEXITCODE) { throw "Storing $name failed." }
        }
        Write-Host "Stored DEPLOY_SSH_KEY, $($values.Keys -join ', ') in $Repo."
    }
    $line = "command=`"$AppDir/deploy/update.sh`",restrict " + (Get-Content -Raw "$key.pub").Trim()
}
finally {
    Remove-Item -Recurse -Force $tmp  # the private key now lives only in GitHub
}

Write-Host ''
Write-Host "On the server, as $User, append this line to ~/.ssh/authorized_keys:" -ForegroundColor Green
Write-Host ''
Write-Host $line
Write-Host ''
if (-not $DryRun) {
    try {
        Set-Clipboard -Value $line
        Write-Host '(It is on your clipboard too.)'
    } catch {
        # No clipboard (e.g. over SSH): the line above is all you need.
    }
}
Write-Host "Then push a commit, or use Actions > 'Test and deploy' > Run workflow."
