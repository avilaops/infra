<#
.SYNOPSIS
  Orquestra o levantamento da maquina local e dos servidores, e gera um relatorio unico.
.DESCRIPTION
  1. Roda coletar-windows.ps1 aqui.
  2. Envia coletar-linux.sh por SSH para cada servidor de hosts.json e coleta o JSON.
  3. Grava um JSON por host em saida/<carimbo>/ e renderiza RELATORIO.md.
  Tudo somente-leitura. Nenhum valor de segredo entra na saida.
.EXAMPLE
  .\levantar.ps1
  .\levantar.ps1 -Rapido
  .\levantar.ps1 -SomenteServidores
#>
[CmdletBinding()]
param(
  [switch] $Rapido,
  [switch] $SomenteLocal,
  [switch] $SomenteServidores,
  [string] $Config = "$PSScriptRoot\hosts.json"
)

$ErrorActionPreference = 'Stop'
$inicio = Get-Date
$carimbo = $inicio.ToString('yyyy-MM-dd_HHmm')
$destino = Join-Path $PSScriptRoot "saida\$carimbo"
New-Item -ItemType Directory -Path $destino -Force | Out-Null

$cfg = Get-Content $Config -Raw | ConvertFrom-Json
$limites = $cfg.limites

Write-Host "levantamento $carimbo -> $destino" -ForegroundColor Cyan

# ------------------------------------------------------------------- coleta
$coletados = [ordered]@{}

if (-not $SomenteServidores) {
  Write-Host '  [local] coletando...' -NoNewline
  $args = @{}
  if ($Rapido) { $args.Rapido = $true }
  try {
    $json = & "$PSScriptRoot\coletar-windows.ps1" @args
    $arq = Join-Path $destino 'local.json'
    $json | Set-Content -Path $arq -Encoding UTF8
    $coletados['local'] = $json | ConvertFrom-Json
    Write-Host ' ok' -ForegroundColor Green
  } catch {
    Write-Host " FALHOU: $_" -ForegroundColor Red
  }
}

if (-not $SomenteLocal) {
  $chave = $cfg.chave_ssh -replace '^~', $env:USERPROFILE
  $script = Get-Content "$PSScriptRoot\coletar-linux.sh" -Raw
  $modo = if ($Rapido) { '--rapido' } else { '' }

  foreach ($s in $cfg.servidores) {
    Write-Host "  [$($s.nome)] $($s.host) coletando..." -NoNewline
    try {
      # o "--" e obrigatorio: sem ele o bash interpreta --rapido como opcao dele
      $json = $script | & ssh -i $chave -o StrictHostKeyChecking=accept-new `
                            -o ConnectTimeout=20 -o BatchMode=yes `
                            "$($s.usuario)@$($s.host)" "bash -s -- $modo" 2>$null
      $texto = $json -join "`n"
      if (-not $texto.Trim().StartsWith('{')) { throw 'resposta nao e JSON' }
      $arq = Join-Path $destino "$($s.nome).json"
      $texto | Set-Content -Path $arq -Encoding UTF8
      $obj = $texto | ConvertFrom-Json
      $obj | Add-Member -NotePropertyName descricao -NotePropertyValue $s.descricao -Force
      $obj | Add-Member -NotePropertyName endereco  -NotePropertyValue $s.host      -Force
      $coletados[$s.nome] = $obj
      Write-Host ' ok' -ForegroundColor Green
    } catch {
      Write-Host " FALHOU: $_" -ForegroundColor Red
    }
  }
}

if ($coletados.Count -eq 0) { Write-Error 'nenhum host coletado'; exit 1 }

# ---------------------------------------------------------------- alertas
function Get-Alertas {
  param($nome, $d)
  $a = @()

  foreach ($fs in $d.armazenamento.sistemas_de_arquivos) {
    $p = $fs -split '\|'
    $pct = 0
    foreach ($c in $p) { if ($c -match '^\s*([\d,\.]+)\s*%') { $pct = [double]($Matches[1] -replace ',', '.') } }
    if ($pct -ge $limites.disco_alerta_pct) {
      $a += "DISCO  $nome : $($p[0]) em $pct% ocupado"
    }
  }

  if ($d.identidade.ram_mb -gt 0) {
    $livre = [math]::Round(100 * $d.identidade.ram_livre_mb / $d.identidade.ram_mb, 0)
    if ($livre -le $limites.ram_livre_alerta_pct) { $a += "RAM    $nome : apenas $livre% livre ($($d.identidade.ram_livre_mb) MB de $($d.identidade.ram_mb) MB)" }
  }

  foreach ($o in $d.segredos.ocorrencias) {
    if ($o -match 'PRODUCAO|chave-privada|aws|github') { $a += "SEGREDO $nome : $o" }
  }

  $n = @($d.env.permissao_insegura).Count + @($d.env.gravavel_por_todos).Count
  if ($n -gt 0) { $a += "ENV    $nome : $n arquivos .env legiveis ou gravaveis alem do dono" }

  foreach ($c in $d.dominios.certificados) {
    if ($c -match '\|(-?\d+) dias' -and [int]$Matches[1] -le $limites.certificado_alerta_dias) {
      $a += "CERT   $nome : $c"
    }
  }
  foreach ($x in $d.dominios.sem_dns) { $a += "DNS    $nome : $x esta no Caddy mas nao resolve" }

  foreach ($r in $d.git.repositorios) {
    if ($r -match 'ahead=(\d+)' -and [int]$Matches[1] -gt 0) { $a += "GIT    $nome : $($Matches[1]) commits nao enviados em $(($r -split '\|')[0])" }
    if ($r -match 'SEM-REMOTO') { $a += "GIT    $nome : sem remoto em $(($r -split '\|')[0])" }
  }

  foreach ($c in $d.docker.reiniciando) { $a += "DOCKER $nome : container reiniciando -> $c" }
  foreach ($r in $d.bancos.postgres_host_roles) {
    if ($r -match 'SUPERUSUARIO' -and $r -notmatch '^postgres\|') { $a += "BANCO  $nome : role superusuaria extra -> $(($r -split '\|')[0])" }
  }
  if ($d.seguranca.fail2ban_qtd -ne $null -and $d.seguranca.fail2ban_qtd -le 1 -and $d.docker.disponivel -eq 'sim') {
    $a += "SEG    $nome : fail2ban com $($d.seguranca.fail2ban_qtd) jail (so SSH protegido)"
  }
  if ($d.seguranca.reinicio_necessario -eq 'sim') { $a += "SEG    $nome : reinicio pendente do kernel" }
  foreach ($f in $d.servicos.systemd_falhos) { $a += "SVC    $nome : unidade falha -> $f" }

  return $a
}

# --------------------------------------------------------------- relatorio
$md = New-Object System.Text.StringBuilder
function W { param($t = '') [void]$md.AppendLine($t) }
function Tem {
  # @($null).Count vale 1, nao 0: sem isto toda secao vazia imprime cabecalho.
  param($x)
  return (@($x) | Where-Object { $_ -ne $null -and "$_" -ne '' }).Count -gt 0
}

function Tabela {
  param([string[]] $linhas, [string[]] $cabecalho)
  if (-not $linhas -or $linhas.Count -eq 0) { W '_nada encontrado_'; W; return }
  W ('| ' + ($cabecalho -join ' | ') + ' |')
  W ('|' + (($cabecalho | ForEach-Object { '---' }) -join '|') + '|')
  foreach ($l in $linhas) {
    $c = $l -split '\|'
    while ($c.Count -lt $cabecalho.Count) { $c += '' }
    W ('| ' + (($c[0..($cabecalho.Count - 1)] | ForEach-Object { $_ -replace '\|', '\|' }) -join ' | ') + ' |')
  }
  W
}

W "# Levantamento de infraestrutura"
W
W "Gerado em $($inicio.ToString('dd/MM/yyyy HH:mm')) | modo $(if ($Rapido) { 'rapido' } else { 'completo' }) | $($coletados.Count) hosts"
W
W '> Nenhum valor de segredo aparece neste documento: apenas o arquivo, a linha e o tipo.'
W

# alertas primeiro
$todos = @()
foreach ($k in $coletados.Keys) { $todos += Get-Alertas $k $coletados[$k] }
W '## Alertas'
W
if ($todos.Count -eq 0) {
  W 'Nenhum alerta.'
} else {
  W "$($todos.Count) itens exigindo atencao."
  W
  foreach ($g in ($todos | Group-Object { ($_ -split '\s+')[0] } | Sort-Object Name)) {
    W "**$($g.Name)** — $($g.Count)"
    W
    foreach ($i in $g.Group) { W "- $($i -replace '^\S+\s+', '')" }
    W
  }
}

# resumo comparativo
W '## Resumo por host'
W
$resumo = foreach ($k in $coletados.Keys) {
  $d = $coletados[$k]
  '{0}|{1}|{2}|{3} MB|{4}|{5}|{6}|{7}' -f $k, $d.identidade.hostname, $d.identidade.so,
    $d.identidade.ram_mb, $d.env.total,
    $(if ($d.dominios.total_hostnames) { $d.dominios.total_hostnames } else { 0 }),
    (@($d.bancos.postgres_host_bancos).Count + @($d.bancos.postgres_containers).Count),
    @($d.git.repositorios).Count
}
Tabela $resumo @('Host', 'Nome', 'SO', 'RAM', '.env', 'Dominios', 'Bancos', 'Repos')

# detalhe por host
foreach ($k in $coletados.Keys) {
  $d = $coletados[$k]
  W "---"
  W
  W "## $k"
  W
  if ($d.descricao) { W "_$($d.descricao)_"; W }
  W "``$($d.identidade.hostname)`` · $($d.identidade.so) · $($d.identidade.cpu_modelo) · ligado ha $($d.identidade.ligado_ha)"
  W

  W '### Armazenamento'
  W
  Tabela $d.armazenamento.sistemas_de_arquivos @('Dispositivo', 'Rotulo/Tam', 'Usado', 'Livre', 'Uso', 'Ponto')

  if (Tem $d.peso.diretorios_opt) {
    W '### Peso em /opt (MB)'; W
    Tabela $d.peso.diretorios_opt @('Diretorio', 'MB')
  }
  if (Tem $d.peso.pastas_raiz) {
    W '### Peso das pastas de projeto'; W
    Tabela $d.peso.pastas_raiz @('Pasta', 'Tamanho')
  }
  if (Tem $d.peso.appdata) {
    W '### AppData\Local'; W
    Tabela $d.peso.appdata @('Pasta', 'Tamanho')
  }
  if (Tem $d.peso.discos_virtuais) {
    W '### Discos virtuais (WSL / Docker)'; W
    Tabela $d.peso.discos_virtuais @('Arquivo', 'Tamanho', 'Ultimo uso')
  }
  if (Tem $d.peso.caches) {
    W '### Caches descartaveis'; W
    Tabela $d.peso.caches @('Cache', 'Tamanho')
  }

  W '### Docker'
  W
  if ($d.docker.disponivel -eq 'sim') {
    Tabela $d.docker.resumo_uso @('Tipo', 'Qtd', 'Tamanho', 'Recuperavel')
    W "Imagens: $(@($d.docker.imagens).Count) · containers: $(@($d.docker.containers).Count) · volumes: $(@($d.docker.volumes).Count)"
    W
    if (Tem $d.docker.imagens) {
      W '<details><summary>Imagens</summary>'; W
      Tabela (@($d.docker.imagens) | Select-Object -First 25) @('Imagem', 'Tamanho', 'Criada')
      W '</details>'; W
    }
    if (Tem $d.docker.sem_healthcheck) {
      W "Sem healthcheck: $((@($d.docker.sem_healthcheck)) -join ', ')"; W
    }
    if (Tem $d.docker.tag_latest) {
      W "Rodando com tag ``:latest`` (nao reproduzivel): $((@($d.docker.tag_latest)) -join ', ')"; W
    }
  } else {
    W "Indisponivel: $($d.docker.disponivel)"; W
  }
  if (Tem $d.docker.wsl) { W '```'; foreach ($l in $d.docker.wsl) { W $l }; W '```'; W }

  W '### Arquivos .env'
  W
  W "Total: **$($d.env.total)**"
  W
  Tabela (@($d.env.por_nome) | Select-Object -First 15) @('Nome', 'Qtd')
  if (Tem $d.env.por_projeto) {
    W '<details><summary>Por projeto</summary>'; W
    Tabela $d.env.por_projeto @('Projeto', 'Qtd')
    W '</details>'; W
  }
  $inseg = @($d.env.permissao_insegura) + @($d.env.gravavel_por_todos)
  if ($inseg.Count -gt 0) {
    W "**Permissao insegura ($($inseg.Count))**"; W
    W '<details><summary>ver lista</summary>'; W; W '```'
    foreach ($l in $inseg) { W $l }
    W '```'; W '</details>'; W
  }
  if (Tem $d.env.sobras_de_backup) {
    W "**Sobras de backup ($(@($d.env.sobras_de_backup).Count))** — candidatas a remocao"; W
    W '```'; foreach ($l in $d.env.sobras_de_backup) { W $l }; W '```'; W
  }

  W '### Segredos em texto claro'
  W
  Tabela $d.segredos.ocorrencias @('Arquivo', 'Linha', 'Tipo')
  if (Tem $d.segredos.conexoes_com_senha) {
    W '<details><summary>Strings de conexao (senha omitida)</summary>'; W; W '```'
    foreach ($l in $d.segredos.conexoes_com_senha) { W $l }
    W '```'; W '</details>'; W
  }

  if ($d.dominios.total_hostnames -gt 0) {
    W '### Dominios'
    W
    W "Hostnames: **$($d.dominios.total_hostnames)** · dominios registraveis: **$(@($d.dominios.dominios_raiz).Count)**"
    W
    W '<details><summary>Hostnames</summary>'; W; W '```'
    foreach ($l in $d.dominios.hostnames) { W $l }
    W '```'; W '</details>'; W
    W "Registraveis: $((@($d.dominios.dominios_raiz)) -join ', ')"; W
    if (Tem $d.dominios.sem_dns) {
      W "**Sem DNS (bloco morto):** $((@($d.dominios.sem_dns)) -join ', ')"; W
    }
    W '<details><summary>Certificados</summary>'; W
    Tabela $d.dominios.certificados @('Certificado', 'Validade')
    W '</details>'; W
  }

  W '### Bancos de dados'
  W
  if ($d.bancos.servico) { W "Servico: $($d.bancos.servico)" ; W }
  if ($d.bancos.postgres_host_versao) {
    W "PostgreSQL do host: **$($d.bancos.postgres_host_versao)** escutando em ``$($d.bancos.postgres_host_escuta)``"
    W
    Tabela $d.bancos.postgres_host_bancos @('Banco', 'Tamanho', 'Dono')
    W '<details><summary>Roles</summary>'; W
    Tabela $d.bancos.postgres_host_roles @('Role', 'Tipo', 'CreateDB')
    W '</details>'; W
  }
  if (Tem $d.bancos.postgres_containers) {
    W 'Em container:'; W
    Tabela $d.bancos.postgres_containers @('Container', 'Banco', 'Tamanho')
  }
  if (Tem $d.bancos.outros_bancos) {
    W "Outros: $((@($d.bancos.outros_bancos)) -join ', ')"; W
  }

  W '### Repositorios git'
  W
  Tabela $d.git.repositorios @('Caminho', 'Branch', 'Sujo', 'Ahead', 'Remoto')

  W '### Rede'
  W
  if ($d.identidade.ips -or $d.rede.ips) { W "IPs: $($d.rede.ips)"; W }
  Tabela (@($d.rede.portas_escutando) | Select-Object -First 30) @('Endereco', 'Processo')
  if (Tem $d.rede.portas_publicas) {
    W "**Expostas em todas as interfaces:** $((@($d.rede.portas_publicas)) -join ', ')"; W
  }

  W '### Seguranca'
  W
  if ($d.seguranca.fail2ban_jails) { W "fail2ban: $($d.seguranca.fail2ban_qtd) jail(s) — $($d.seguranca.fail2ban_jails)" }
  if ($d.seguranca.firewall)       { W "firewall: $($d.seguranca.firewall)" }
  if ($d.seguranca.ssh_root_permitido)  { W "ssh root: $($d.seguranca.ssh_root_permitido) · senha: $($d.seguranca.ssh_senha_permitida) · $($d.seguranca.chaves_root) chave(s)" }
  if ($d.seguranca.atualizacoes_pendentes) { W "atualizacoes pendentes: $($d.seguranca.atualizacoes_pendentes) · reinicio: $($d.seguranca.reinicio_necessario)" }
  foreach ($x in @($d.seguranca.firewall_regras))  { W "- $x" }
  foreach ($x in @($d.seguranca.antivirus))        { W "- antivirus $x" }
  foreach ($x in @($d.seguranca.bitlocker))        { W "- bitlocker $x" }
  if (Tem $d.seguranca.acesso_remoto)   { W "- acesso remoto ativo: $((@($d.seguranca.acesso_remoto)) -join ', ')" }
  W

  if (Tem $d.backups.cron_root -or Tem $d.backups.cron_d) {
    W '### Backups'
    W
    if ($d.backups.tamanho) { W "Tamanho de /opt/backups: **$($d.backups.tamanho)**"; W }
    W '<details><summary>Agendamentos</summary>'; W; W '```'
    foreach ($l in @($d.backups.cron_root)) { W "crontab: $l" }
    foreach ($l in @($d.backups.cron_d))    { W "cron.d:  $l" }
    W '```'; W '</details>'; W
    if (Tem $d.backups.mais_recentes) {
      Tabela (@($d.backups.mais_recentes) | Select-Object -First 10) @('Data', 'Bytes', 'Arquivo')
    }
    if (Tem $d.backups.ultimo_log) {
      W '```'; foreach ($l in $d.backups.ultimo_log) { W $l }; W '```'; W
    }
  }

  W '### Processos'
  W
  Tabela $d.servicos.top_ram @('Processo', 'RAM', 'Qtd')
  if (Tem $d.servicos.top_swap) {
    W 'Em swap:'; W
    Tabela $d.servicos.top_swap @('Processo', 'Swap')
  }
  if (Tem $d.servicos.systemd_ativos) {
    W "Servicos ativos fora do Docker: $((@($d.servicos.systemd_ativos)) -join ', ')"; W
  }
  if (Tem $d.ferramentas) {
    W '<details><summary>Ferramentas instaladas</summary>'; W
    Tabela $d.ferramentas @('Ferramenta', 'Versao')
    W '</details>'; W
  }
}

$relatorio = Join-Path $destino 'RELATORIO.md'
$md.ToString() | Set-Content -Path $relatorio -Encoding UTF8

# atalho para o mais recente
Copy-Item $relatorio (Join-Path $PSScriptRoot 'ULTIMO-RELATORIO.md') -Force

# ------------------------------------------------------- comparar com anterior
$anteriores = Get-ChildItem (Join-Path $PSScriptRoot 'saida') -Directory |
  Where-Object { $_.Name -ne $carimbo } | Sort-Object Name -Descending
if ($anteriores) {
  $ant = $anteriores[0]
  Write-Host ''
  Write-Host "mudancas desde $($ant.Name):" -ForegroundColor Cyan
  foreach ($k in $coletados.Keys) {
    $arqAnt = Join-Path $ant.FullName "$k.json"
    if (-not (Test-Path $arqAnt)) { continue }
    $a = Get-Content $arqAnt -Raw | ConvertFrom-Json
    $n = $coletados[$k]
    foreach ($m in @(
      @{ n = '.env';      v = { param($x) $x.env.total } },
      @{ n = 'dominios';  v = { param($x) $x.dominios.total_hostnames } },
      @{ n = 'segredos';  v = { param($x) @($x.segredos.ocorrencias).Count } },
      @{ n = 'imagens';   v = { param($x) @($x.docker.imagens).Count } }
    )) {
      $va = & $m.v $a; $vn = & $m.v $n
      if ($va -ne $vn) { Write-Host ("  {0,-10} {1,-10} {2} -> {3}" -f $k, $m.n, $va, $vn) }
    }
  }
}

Write-Host ''
Write-Host "relatorio: $relatorio" -ForegroundColor Green
Write-Host "alertas:   $($todos.Count)" -ForegroundColor $(if ($todos.Count -gt 0) { 'Yellow' } else { 'Green' })
Write-Host "duracao:   $([math]::Round(((Get-Date) - $inicio).TotalSeconds, 0))s"
