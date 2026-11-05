# Preparação de imagem sem ativação

O dispatcher aceita `prepare <aplicacao> <imagem@sha256:...>` somente quando
`ALLOW_IMAGE_PREPARE=true` está no arquivo root-owned da aplicação. O Lojas é
o primeiro consumidor. A chave SSH continua vinculada à aplicação; domínio,
repositório e digest têm as mesmas restrições do comando `deploy`.

`prepare` valida o destino, obtém a trava da aplicação, verifica espaço e baixa
a imagem com o token temporário recebido pela entrada padrão. Não aplica
migrações, grava override de imagem ou recria o serviço. Falha de download
reprova a preparação e preserva o serviço atual. As credenciais temporárias
são removidas inclusive quando login ou download falham.

O job seguinte executa `deploy` com o mesmo digest. Esse comando permanece
completo e compatível com consumidores antigos: confere a imagem no registry,
faz backup quando houver migração, migra, ativa e verifica saúde e imagem,
com rollback em falha. A preparação elimina o download frio dessa janela.

Instalação: após CI e merge, instalar `scripts/deploy-container.sh` como
`/usr/local/sbin/avila-deploy`, root:root, modo 0755, preservando cópia do
dispatcher anterior. Habilitar apenas `ALLOW_IMAGE_PREPARE=true` no arquivo
`/etc/avilaops/deploy/lojas.avilaops.com.conf`, sem substituir seus demais
campos. Registrar hashes antes/depois e manter as duas etapas do Lojas com
limite de um minuto e proteção contra publicação de run antigo.
