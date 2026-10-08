-- Tabelas de apoio das rotinas do OpenClaw. Banco: agentes (dono: avops).
-- Idempotente: pode rodar de novo a qualquer momento.
--   psql -d agentes -v ON_ERROR_STOP=1 -f schema.sql

-- Varredura dos repositórios ------------------------------------------------

create table if not exists repo_varreduras (
    id            bigint generated always as identity primary key,
    iniciada_em   timestamptz not null default now(),
    duracao_s     numeric(8,1),
    repos         int not null default 0,
    prs_abertos   int not null default 0,
    ci_vermelho   int not null default 0,
    conflitos     int not null default 0,
    parados       int not null default 0,
    erros         int not null default 0
);

-- Retrato atual: uma linha por repositório, substituída a cada varredura.
create table if not exists repo_estado (
    repo             text primary key,
    remoto           text,
    branch_atual     text,
    arquivos_sujos   int not null default 0,
    ultimo_commit_em timestamptz,
    ci_main          text,
    erro             text,
    varrido_em       timestamptz not null default now()
);

create table if not exists repo_branches (
    repo             text not null,
    branch           text not null,
    agente           text not null,
    ultimo_commit_em timestamptz,
    mesclada         boolean not null default false,
    tem_pr_aberto    boolean not null default false,
    varrido_em       timestamptz not null default now(),
    primary key (repo, branch)
);

create table if not exists repo_prs (
    repo          text not null,
    numero        int  not null,
    titulo        text not null,
    autor         text,
    rascunho      boolean not null default false,
    branch        text,
    mesclavel     text,
    revisao       text,
    ci            text not null,
    so_docs       boolean not null default false,
    atualizado_em timestamptz,
    criado_em     timestamptz,
    url           text,
    varrido_em    timestamptz not null default now(),
    primary key (repo, numero)
);

-- Vigia de saúde ------------------------------------------------------------

create table if not exists saude_medidas (
    id               bigint generated always as identity primary key,
    medida_em        timestamptz not null default now(),
    mem_disp_mb      int,
    swap_usado_pct   int,
    disco_pct        int,
    gateway_ativo    boolean,
    gateway_mem_mb   int,  -- RSS + swap do processo (até 2026-10-06: MemoryCurrent do cgroup)
    gateway_reinicios int,
    gateway_mortes   int,
    cert_dias        int,
    alertas          text[] not null default '{}'
);
create index if not exists saude_medidas_medida_em_idx on saude_medidas (medida_em desc);

-- Chave de deduplicação para tarefas abertas por script: enquanto houver
-- tarefa não encerrada com a mesma chave, o script atualiza em vez de duplicar.
alter table equipe_tarefas add column if not exists chave text;
create unique index if not exists equipe_tarefas_chave_aberta_idx
    on equipe_tarefas (chave)
    where chave is not null and estado not in ('concluida', 'cancelada');

-- Ciclo de roadmap ----------------------------------------------------------
-- Uma linha por item de roadmap que entrou no ciclo (ou por especificação ainda
-- sem item escolhido). O item é identificado pelo hash curto do texto da linha.

create table if not exists roadmap_ciclo (
    id            bigint generated always as identity primary key,
    repo          text not null,
    item_id       text,
    item_texto    text,
    fase          text not null check (fase in
                    ('especificando', 'especificado', 'desenvolvendo', 'concluido', 'pulado', 'cancelado')),
    tarefa_spec   bigint,
    tarefa_dev    bigint,
    artefato      text,
    criado_em     timestamptz not null default now(),
    atualizado_em timestamptz not null default now()
);
create index if not exists roadmap_ciclo_repo_idx on roadmap_ciclo (repo, fase);
