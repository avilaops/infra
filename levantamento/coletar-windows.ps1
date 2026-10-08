<#
.SYNOPSIS
  Levantamento somente-leitura da maquina Windows local, saida em JSON.
.DESCRIPTION
  Espelha as mesmas secoes de coletar-linux.sh para que os relatorios sejam
  comparaveis. Nunca imprime o VALOR de um segredo: apenas arquivo, linha e tipo.
.PARAMETER Rapido
  Pula as medicoes de peso que percorrem arvores grandes (leva ~10s em vez de ~3min).
.PARAMETER Raizes
  Pastas de projeto a inspecionar. Padrao: todas as pastas de primeiro nivel dos discos fixos.
#>
[CmdletBinding()]
param(
  [switch] $Rapido,
  [string[]] $Raizes = @()
)

$ErrorActionPreference = 'SilentlyContinue'
$ProgressPreference    = 'SilentlyContinue'

# ------------------------------------------------------------------ helpers

# Pastas que nunca valem a pena percorrer. Sem podar aqui, uma varredura de
# D:\ leva dezenas de minutos: sao milhoes de arquivos so em node_modules.
$script:Podar = @(
  'node_modules', '.next', '.git', '.turbo', '.venv', 'venv', '__pycache__',
  'dist', 'build', 'out', '.cache', 'vendor', '.pnpm-store', '.yarn',
  'coverage', '.nuxt', '.svelte-kit', 'target', 'bin', 'obj', '$RECYCLE.BIN',
  'System Volume Information'
)

# Busca com poda: o padrao vai para o sistema de arquivos (rapido) e as pastas
# da lista acima nunca sao abertas.
function Buscar-Podado {
  param(
    [string[]] $Raizes,
    [string[]] $Padroes = @('*'),
    [int]      $ProfundidadeMax = 8,
    [switch]   $Diretorios
  )
  # Percorre a arvore UMA vez e aplica todos os padroes em cada pasta visitada.
  # A travessia de diretorio e o custo dominante; repeti-la por padrao e o que
  # fazia a coleta levar minutos.
  $achados = [System.Collections.Generic.List[string]]::new()
  $pilha   = [System.Collections.Generic.Stack[object]]::new()
  foreach ($r in $Raizes) {
    if (Test-Path -LiteralPath $r) { $pilha.Push([pscustomobject]@{ P = $r; D = 0 }) }
  }
  while ($pilha.Count -gt 0) {
    $no = $pilha.Pop()
    foreach ($pad in $Padroes) {
      try {
        if ($Diretorios) {
          foreach ($x in [System.IO.Directory]::EnumerateDirectories($no.P, $pad)) { $achados.Add($x) }
        } else {
          foreach ($x in [System.IO.Directory]::EnumerateFiles($no.P, $pad)) { $achados.Add($x) }
        }
      } catch { }
    }
    if ($no.D -ge $ProfundidadeMax) { continue }
    try {
      foreach ($sub in [System.IO.Directory]::EnumerateDirectories($no.P)) {
        $nome = [System.IO.Path]::GetFileName($sub)
        if ($script:Podar -notcontains $nome) {
          $pilha.Push([pscustomobject]@{ P = $sub; D = $no.D + 1 })
        }
      }
    } catch { }
  }
  $achados
}

function Peso-Bytes {
  param([string] $Caminho)
  try {
    (Get-ChildItem -LiteralPath $Caminho -Recurse -File -Force -ErrorAction SilentlyContinue |
      Measure-Object -Property Length -Sum).Sum
  } catch { 0 }
}

function GB { param($b) [math]::Round(([double]($b | ForEach-Object { $_ })) / 1GB, 2) }

$r = [ordered]@{
  coletado_em    = (Get-Date).ToString('o')
  coletor_versao = '1.0.0'
  plataforma     = 'windows'
  modo           = $(if ($Rapido) { 'rapido' } else { 'completo' })
}

# ------------------------------------------------------------- 1. identidade
$cs  = Get-CimInstance Win32_ComputerSystem
$os  = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1

$r.identidade = [ordered]@{
  hostname      = $cs.Name
  fabricante    = "$($cs.Manufacturer) $($cs.Model)"
  so            = "$($os.Caption) build $($os.BuildNumber)"
  kernel        = $os.Version
  arquitetura   = $env:PROCESSOR_ARCHITECTURE
  instalado_em  = $os.InstallDate.ToString('yyyy-MM-dd')
  ligado_ha     = '{0}d {1}h {2}m' -f ((Get-Date) - $os.LastBootUpTime).Days,
                                       ((Get-Date) - $os.LastBootUpTime).Hours,
                                       ((Get-Date) - $os.LastBootUpTime).Minutes
  cpus          = $cpu.NumberOfLogicalProcessors
  cpu_modelo    = $cpu.Name.Trim()
  ram_mb        = [math]::Round($cs.TotalPhysicalMemory / 1MB, 0)
  ram_livre_mb  = [math]::Round($os.FreePhysicalMemory / 1KB, 0)
}

# ---------------------------------------------------------- 2. armazenamento
$r.armazenamento = [ordered]@{
  sistemas_de_arquivos = @(
    Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | ForEach-Object {
      '{0}|{1}|{2:N1} GB|{3:N1} GB|{4:N1}%|{5}' -f $_.DeviceID, $_.VolumeName,
        ($_.Size / 1GB), ($_.FreeSpace / 1GB), ((1 - $_.FreeSpace / $_.Size) * 100), $_.FileSystem
    })
  blocos = @(
    Get-PhysicalDisk | ForEach-Object {
      '{0}|{1:N1} GB|{2}|{3}' -f $_.FriendlyName, ($_.Size / 1GB), $_.MediaType, $_.HealthStatus
    })
}

if ($Raizes.Count -eq 0) {
  $Raizes = @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' |
    ForEach-Object { Get-ChildItem "$($_.DeviceID)\" -Directory -Force -ErrorAction SilentlyContinue } |
    Where-Object { $_.Name -notmatch '^(Windows|Program Files|Program Files \(x86\)|ProgramData|\$RECYCLE\.BIN|System Volume Information|PerfLogs|Recovery)$' } |
    Select-Object -ExpandProperty FullName)
}

# ------------------------------------------------------------------- 3. peso
$peso = [ordered]@{ pastas_raiz = @(); appdata = @(); discos_virtuais = @(); caches = @() }
if (-not $Rapido) {
  $peso.pastas_raiz = @(
    $Raizes | ForEach-Object {
      [pscustomobject]@{ N = $_; GB = (GB (Peso-Bytes $_)) }
    } | Sort-Object GB -Descending | ForEach-Object { '{0}|{1} GB' -f $_.N, $_.GB })

  $peso.appdata = @(
    Get-ChildItem $env:LOCALAPPDATA -Directory -Force -ErrorAction SilentlyContinue | ForEach-Object {
      [pscustomobject]@{ N = $_.Name; GB = (GB (Peso-Bytes $_.FullName)) }
    } | Sort-Object GB -Descending | Select-Object -First 15 |
      ForEach-Object { '{0}|{1} GB' -f $_.N, $_.GB })

  # VHDX do WSL/Docker: costuma ser o maior arquivo isolado da maquina
  $peso.discos_virtuais = @(
    Get-ChildItem $env:LOCALAPPDATA -Recurse -Filter '*.vhdx' -Force -ErrorAction SilentlyContinue |
      Sort-Object Length -Descending |
      ForEach-Object { '{0}|{1} GB|ultimo uso {2:yyyy-MM-dd}' -f $_.FullName, (GB $_.Length), $_.LastWriteTime })

  $cacheAlvos = @(
    "$env:LOCALAPPDATA\npm-cache", "$env:LOCALAPPDATA\Yarn", "$env:LOCALAPPDATA\pnpm-cache",
    "$env:LOCALAPPDATA\Temp", "$env:USERPROFILE\.gradle", "$env:USERPROFILE\.cargo",
    "$env:USERPROFILE\.cache", "$env:USERPROFILE\.m2", "$env:USERPROFILE\.nuget"
  ) + @($Raizes | ForEach-Object { Join-Path (Split-Path $_ -Qualifier) '\.pnpm-store' } | Select-Object -Unique)

  $peso.caches = @(
    $cacheAlvos | Where-Object { Test-Path $_ } | ForEach-Object {
      [pscustomobject]@{ N = $_; GB = (GB (Peso-Bytes $_)) }
    } | Sort-Object GB -Descending | ForEach-Object { '{0}|{1} GB' -f $_.N, $_.GB })
}
$r.peso = $peso

# ---------------------------------------------------------------- 4. docker
$docker = [ordered]@{ disponivel = 'nao'; versao = ''; resumo_uso = @(); imagens = @(); containers = @(); wsl = @() }
if (Get-Command docker -ErrorAction SilentlyContinue) {
  $null = docker info 2>&1
  if ($LASTEXITCODE -eq 0) {
    $docker.disponivel = 'sim'
    $docker.versao     = (docker version --format '{{.Server.Version}}' 2>$null)
    $docker.resumo_uso = @(docker system df --format '{{.Type}}|{{.TotalCount}}|{{.Size}}|{{.Reclaimable}}' 2>$null)
    $docker.imagens    = @(docker images --format '{{.Repository}}:{{.Tag}}|{{.Size}}' 2>$null)
    $docker.containers = @(docker ps -a --format '{{.Names}}|{{.Image}}|{{.Status}}' 2>$null)
  } else {
    $docker.disponivel = 'CLI instalado, daemon parado'
  }
}
$docker.wsl = @((wsl.exe -l -v 2>$null) -replace "`0", '' | Where-Object { $_ -match '\S' } | ForEach-Object { $_.Trim() })
$r.docker = $docker

# ------------------------------------------------------------------- 5. env
$envs = @(Buscar-Podado -Raizes $Raizes -Padroes '.env*')

$r.env = [ordered]@{
  total       = $envs.Count
  por_nome    = @($envs | ForEach-Object { [System.IO.Path]::GetFileName($_) } |
                  Group-Object | Sort-Object Count -Descending |
                  ForEach-Object { '{0}|{1}' -f $_.Name, $_.Count })
  por_projeto = @($envs | ForEach-Object { $_ -replace '^([A-Za-z]:\\[^\\]+\\[^\\]+).*', '$1' } |
                  Group-Object | Sort-Object Count -Descending | Select-Object -First 20 |
                  ForEach-Object { '{0}|{1}' -f $_.Name, $_.Count })
  sobras_de_backup = @($envs | Where-Object {
      [System.IO.Path]::GetFileName($_) -match '\.(bak|old|save|orig|anterior|quebrado)|\.bak-|\.backup-|\.before-|~$|\.\d{9,}$'
    })
  # no Windows nao ha modo POSIX; o equivalente e checar quem herdou escrita
  gravavel_por_todos = @($envs | ForEach-Object {
      $acl = Get-Acl -LiteralPath $_ -ErrorAction SilentlyContinue
      if ($acl -and ($acl.Access | Where-Object {
            $_.IdentityReference -match 'Everyone|Todos|BUILTIN\\Users' -and
            $_.FileSystemRights -match 'Write|Modify|FullControl' })) { $_ }
    })
}

# -------------------------------------------------------------- 6. segredos
$padroes = @{
  'stripe-secreta-PRODUCAO'  = 'sk_live_[A-Za-z0-9]{16,}'
  'stripe-restrita-PRODUCAO' = 'rk_live_[A-Za-z0-9]{16,}'
  'stripe-teste'             = 'sk_test_[A-Za-z0-9]{16,}'
  # re_<id de 8+>_<segredo de 10+>: exigir os dois trechos evita casar com
  # nomes tipo re_cpu_waiting_seconds_total
  'resend'                   = 're_[A-Za-z0-9]{8,}_[A-Za-z0-9]{10,}'
  'aws'                      = 'AKIA[0-9A-Z]{16}'
  'github'                   = '(ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})'
  'slack'                    = 'xox[baprs]-[A-Za-z0-9-]{10,}'
  'google-api'               = 'AIza[0-9A-Za-z_\-]{35}'
  'sendgrid'                 = 'SG\.[A-Za-z0-9_\-]{20,}'
  'chave-privada'            = 'BEGIN [A-Z ]*PRIVATE KEY'
}
# Uma unica varredura da arvore e uma unica passada de regex: percorrer 5 vezes
# e rodar 10 padroes separados levava ~5 min, contra ~40s assim.
$alvosSegredo = @(Buscar-Podado -Raizes $Raizes -Padroes '.env*', '*.sh', '*.yml', '*.yaml', '*.conf')
$alvosSegredo = @($alvosSegredo | Where-Object {
    $i = Get-Item -LiteralPath $_ -ErrorAction SilentlyContinue
    $i -and $i.Length -lt 2MB
  } | Sort-Object -Unique)

$ocorrencias = @()
if ($alvosSegredo.Count -gt 0) {
  $combinado = ($padroes.Values | ForEach-Object { "(?:$_)" }) -join '|'
  $ocorrencias = Select-String -LiteralPath $alvosSegredo -Pattern $combinado -ErrorAction SilentlyContinue |
    ForEach-Object {
      $linha = $_.Line
      $tipo = 'desconhecido'
      foreach ($k in $padroes.Keys) { if ($linha -match $padroes[$k]) { $tipo = $k; break } }
      '{0}|linha {1}|{2}' -f $_.Path, $_.LineNumber, $tipo
    }
}

$alvosEnv = @($alvosSegredo | Where-Object { [System.IO.Path]::GetFileName($_) -like '.env*' })
$conexoes = @()
if ($alvosEnv.Count -gt 0) {
  $conexoes = Select-String -LiteralPath $alvosEnv `
      -Pattern '(postgres(ql)?|mysql|mongodb(\+srv)?|redis|amqp)://[^:@\s"]+:[^@\s"]+@[^"\s/]+/?[^"\s;]*' `
      -AllMatches -ErrorAction SilentlyContinue |
    ForEach-Object { $_.Matches.Value } |
    ForEach-Object { $_ -replace '(//[^:]+:)[^@]+@', '$1SENHA-OMITIDA@' } |
    Sort-Object -Unique
}

$r.segredos = [ordered]@{
  ocorrencias        = @($ocorrencias | Sort-Object -Unique)
  conexoes_com_senha = @($conexoes)
}

# ---------------------------------------------------------------- 7. bancos
$bancos = [ordered]@{
  postgres_host_versao = ''; postgres_host_bancos = @(); postgres_host_roles = @()
  postgres_host_escuta = ''; servico = ''
}
$svc = Get-Service -Name 'postgresql*' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($svc) { $bancos.servico = "$($svc.Name) = $($svc.Status)" }
if (Get-Command psql -ErrorAction SilentlyContinue) {
  $bancos.postgres_host_versao = (& psql -U postgres -h 127.0.0.1 -tAc 'show server_version;' 2>$null | Select-Object -First 1)
  $bancos.postgres_host_bancos = @(& psql -U postgres -h 127.0.0.1 -tAc "select datname || '|' || pg_size_pretty(pg_database_size(datname)) || '|' || pg_get_userbyid(datdba) from pg_database where datistemplate = false order by pg_database_size(datname) desc;" 2>$null | Where-Object { $_ })
  $bancos.postgres_host_roles = @(& psql -U postgres -h 127.0.0.1 -tAc "select rolname || '|' || case when rolsuper then 'SUPERUSUARIO' else 'comum' end from pg_roles where rolname not like 'pg\_%' order by rolsuper desc, rolname;" 2>$null | Where-Object { $_ })
  $bancos.postgres_host_escuta = @(Get-NetTCPConnection -State Listen -LocalPort 5432 -ErrorAction SilentlyContinue |
    ForEach-Object { "$($_.LocalAddress):$($_.LocalPort)" } | Sort-Object -Unique) -join ' '
}
$r.bancos = $bancos

# Secoes que so existem no Linux. Emitidas vazias para que os dois coletores
# produzam o MESMO formato e o relatorio possa comparar host a host.
$r.dominios = [ordered]@{
  total_hostnames = 0; hostnames = @(); dominios_raiz = @(); sem_dns = @(); certificados = @()
}
$r.backups = [ordered]@{
  cron_root = @(); cron_d = @(); timers = @(); tamanho = ''; mais_recentes = @(); ultimo_log = @()
}

# ------------------------------------------------------------------- 8. git
$repos = @(Buscar-Podado -Raizes $Raizes -Padroes '.git' -Diretorios -ProfundidadeMax 5 |
  ForEach-Object { Split-Path $_ -Parent })

$r.git = [ordered]@{
  repositorios = @($repos | ForEach-Object {
    $b = (git -C $_ rev-parse --abbrev-ref HEAD 2>$null)
    $s = @(git -C $_ status --porcelain 2>$null).Count
    $a = (git -C $_ rev-list --count '@{u}..HEAD' 2>$null)
    if (-not $a) { $a = 'sem-upstream' }
    $o = (git -C $_ remote get-url origin 2>$null)
    if ($o) { $o = $o -replace '.*[:/]([^/]+/[^/]+)$', '$1' -replace '\.git$', '' } else { $o = 'SEM-REMOTO' }
    '{0}|{1}|sujo={2}|ahead={3}|{4}' -f $_, $b, $s, $a, $o
  })
}

# ------------------------------------------------------------------ 9. rede
$r.rede = [ordered]@{
  portas_escutando = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -lt 30000 } |
    ForEach-Object {
      '{0}:{1}|{2}' -f $_.LocalAddress, $_.LocalPort, (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName
    } | Sort-Object -Unique)
  portas_publicas = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalAddress -in @('0.0.0.0', '::') -and $_.LocalPort -lt 30000 } |
    ForEach-Object {
      '{0}:{1}|{2}' -f $_.LocalAddress, $_.LocalPort, (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName
    } | Sort-Object -Unique)
  interfaces = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -ne '127.0.0.1' } |
    ForEach-Object { '{0}|{1}/{2}' -f $_.InterfaceAlias, $_.IPAddress, $_.PrefixLength })
}

# ----------------------------------------------------------- 10. ferramentas
$r.ferramentas = @(
  foreach ($t in 'node', 'npm', 'pnpm', 'yarn', 'bun', 'deno', 'git', 'gh', 'docker',
                 'python', 'psql', 'go', 'rustc', 'java', 'dotnet', 'terraform', 'kubectl') {
    if (Get-Command $t -ErrorAction SilentlyContinue) {
      $v = (& $t --version 2>&1 | Select-Object -First 1)
      '{0}|{1}' -f $t, ($v -replace '\s+', ' ').Trim()
    }
  })

# ------------------------------------------------------------- 11. seguranca
$r.seguranca = [ordered]@{
  firewall = @(Get-NetFirewallProfile -ErrorAction SilentlyContinue |
    ForEach-Object { '{0}|{1}' -f $_.Name, $(if ($_.Enabled) { 'ativo' } else { 'DESLIGADO' }) })
  antivirus = @(Get-MpComputerStatus -ErrorAction SilentlyContinue |
    ForEach-Object { 'tempo-real|{0}|assinaturas de {1:yyyy-MM-dd}' -f $_.RealTimeProtectionEnabled, $_.AntivirusSignatureLastUpdated })
  bitlocker = @(Get-BitLockerVolume -ErrorAction SilentlyContinue |
    ForEach-Object { '{0}|{1}' -f $_.MountPoint, $_.ProtectionStatus })
  acesso_remoto = @(Get-Process -ErrorAction SilentlyContinue |
    Where-Object { $_.ProcessName -match 'AnyDesk|TeamViewer|rustdesk|vnc|RemotePC' } |
    Select-Object -ExpandProperty ProcessName -Unique)
}

# -------------------------------------------------------------- 12. servicos
$r.servicos = [ordered]@{
  systemd_falhos = @()
  top_swap       = @()
  top_ram = @(Get-Process | Group-Object ProcessName | ForEach-Object {
      [pscustomobject]@{
        N  = $_.Name
        MB = [math]::Round((($_.Group | Measure-Object WorkingSet64 -Sum).Sum) / 1MB, 0)
        Q  = $_.Count
      }
    } | Sort-Object MB -Descending | Select-Object -First 12 |
      ForEach-Object { '{0}|{1} MB|{2} proc' -f $_.N, $_.MB, $_.Q })
  servicos_automaticos_parados = @(Get-Service -ErrorAction SilentlyContinue |
    Where-Object { $_.StartType -eq 'Automatic' -and $_.Status -ne 'Running' } |
    ForEach-Object { '{0}|{1}' -f $_.Name, $_.Status })
}

$r.fim = $true
$r | ConvertTo-Json -Depth 8
