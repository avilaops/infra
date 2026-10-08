# Publica um commit sem o GitHub Actions: build no apps-noclient, transferencia
# direta entre os servidores e troca do container no servidor de aplicacoes.
#
# Uso, na maquina de quem publica (PowerShell 7, com os hosts `apps-noclient` e
# `applications` no ~/.ssh/config):
#
#   .\scripts\publicar-manual.ps1 -Repositorio D:\Projetos\auth.avilaops.com -Aplicacao auth.avilaops.com
#
# So publica o que esta na branch principal do GitHub (origin/main): codigo
# local nao commitado ou nao mesclado nao entra, de proposito.
#
# Os tres passos sao os scripts deste repositorio instalados nos servidores:
#   apps-noclient: /usr/local/sbin/avila-build        (scripts/build-noclient.sh)
#   applications:  /usr/local/sbin/avila-deploy-local (scripts/deploy-container-local.sh)
param(
  [Parameter(Mandatory)] [string] $Repositorio,
  [Parameter(Mandatory)] [string] $Aplicacao,
  [string] $Branch = 'main',
  [string] $Dockerfile = 'Dockerfile',
  [string] $ServidorBuild = 'apps-noclient',
  [string] $ServidorDestino = 'applications',
  # Endereco pelo qual o servidor de build alcanca o de destino, e o IP de
  # origem que o destino aceita para a chave temporaria.
  [string] $EnderecoDestino = '178.105.82.48',
  [string] $IpBuild = '204.168.249.111'
)
$ErrorActionPreference = 'Stop'

function Falhar([string] $mensagem) { Write-Error $mensagem; exit 1 }

git -C $Repositorio fetch --quiet origin
$sha = (git -C $Repositorio rev-parse "origin/$Branch").Trim()
if ($sha -notmatch '^[a-f0-9]{40}$') { Falhar "Nao achei origin/$Branch em $Repositorio." }
$remoto = (git -C $Repositorio remote get-url origin).Trim()
if ($remoto -notmatch 'avilaops/([A-Za-z0-9._-]+?)(\.git)?$') { Falhar "Remoto inesperado: $remoto" }
$nome = $Matches[1].ToLower()
Write-Host "==> $nome @ $sha"

# 1. Build. O codigo vai por tar: o servidor de build nao guarda credencial do GitHub.
$saida = git -C $Repositorio archive --format=tar $sha |
  ssh -o BatchMode=yes -o ServerAliveInterval=30 $ServidorBuild "avila-build $nome $sha $Dockerfile"
if ($LASTEXITCODE -ne 0) { Falhar 'Build falhou.' }
$artefato = ($saida | Where-Object { $_ -like 'ARTEFATO=*' }) -replace '^ARTEFATO=', ''
$soma = ($saida | Where-Object { $_ -like 'SHA256=*' }) -replace '^SHA256=', ''
if (-not $artefato -or $soma -notmatch '^[a-f0-9]{64}$') { Falhar 'O build nao devolveu artefato e checksum.' }
$arquivo = Split-Path $artefato -Leaf

# 2. Transferencia direta entre servidores. Pela maquina de quem publica a
#    copia depende do upload de casa (mediu-se ~60 KB/s para 140 MB). A chave
#    e criada para esta copia, so vale vinda do IP do build e sai em seguida.
$marca = "transf-temp-$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())"
$publica = ssh -o BatchMode=yes $ServidorBuild "find /root/.ssh -name 'transf-temp*' -delete; ssh-keygen -q -t ed25519 -N '' -C $marca -f /root/.ssh/transf-temp && cat /root/.ssh/transf-temp.pub"
if ($LASTEXITCODE -ne 0 -or -not $publica) { Falhar 'Nao consegui criar a chave temporaria.' }
try {
  ("from=`"$IpBuild`",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding " + $publica.Trim()) |
    ssh -o BatchMode=yes $ServidorDestino 'install -d -m 700 /var/lib/avilaops/entrada; cat >> /root/.ssh/authorized_keys'
  ssh -o BatchMode=yes $ServidorBuild "scp -q -i /root/.ssh/transf-temp -o BatchMode=yes '$artefato' root@${EnderecoDestino}:/var/lib/avilaops/entrada/"
  if ($LASTEXITCODE -ne 0) { Falhar 'Transferencia falhou.' }
} finally {
  ssh -o BatchMode=yes $ServidorDestino "sed -i '/$marca/d' /root/.ssh/authorized_keys"
  ssh -o BatchMode=yes $ServidorBuild "find /root/.ssh -name 'transf-temp*' -delete; find /var/lib/avilaops/build/artefatos -name '$arquivo' -delete"
}

# 3. Troca do container, com verificacao de saude e volta automatica.
ssh -o BatchMode=yes $ServidorDestino "avila-deploy-local $Aplicacao /var/lib/avilaops/entrada/$arquivo $soma"
if ($LASTEXITCODE -ne 0) { Falhar 'Deploy falhou; o script do servidor ja tentou voltar a imagem anterior.' }
Write-Host "==> publicado: $Aplicacao em sha-$sha"
