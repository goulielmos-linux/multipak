# Como o MultiPak instala pacotes DEB (Debian e Ubuntu)

*MultiPak 1.1.0.*

---

## 1. Explicação simples

### 1.1 O que é um pacote `.deb`?

DEB é o formato de pacote do Debian, do Ubuntu e das distribuições baseadas neles. Um
arquivo `.deb` é um arquivo `ar` com três partes: a marca de versão (`debian-binary`),
os metadados (`control.tar.*`: nome, versão, arquitetura, dependências e scripts de
instalação) e os arquivos do programa (`data.tar.*`).

O formato é o mesmo no Debian e no Ubuntu, mas as bibliotecas de cada um, e de cada
versão, são diferentes: um pacote feito para o Ubuntu 24.04 pode não funcionar no
Debian 12, e vice-versa.

### 1.2 O que o MultiPak faz?

1. **Lê sem executar:** abre o `.deb` por fora e lê só o arquivo `control`. Nada é
   extraído e nenhum script roda.
2. **Sugere o ambiente:** o pacote não diz para qual distribuição foi feito, então o
   MultiPak sugere o Ubuntu LTS mais novo, com confiança **Baixa** e uma explicação.
   Você pode escolher outro ambiente nas opções avançadas.
3. **Cria (ou reaproveita) o contêiner** da distribuição escolhida, feito da imagem
   oficial (`docker.io/library/ubuntu` ou `docker.io/library/debian`).
4. **Instala com o apt**, que baixa as dependências dos repositórios da distribuição.
5. **Atalho no menu.**

### 1.3 Os ambientes

| Distribuição | Versões oferecidas (nov. 2026) | Contêiner |
| --- | --- | --- |
| Ubuntu | a versão padrão do catálogo, 24.04 LTS e 22.04 LTS | `MultiPak-Ubuntu-24.04` |
| Debian | a estável, 13 e 12 | `MultiPak-Debian-13` |

As versões vêm da lista de imagens testadas pelo Distrobox, e versões novas aparecem
sozinhas quando entram nela. No Debian, o MultiPak confere no próprio repositório qual é
a versão estável do dia. A tabela acima é a reserva sem rede.

### 1.4 Atualizar e remover

- **Atualizar**: em **Gerenciador → Atualizar**, o MultiPak atualiza o contêiner inteiro
  com o comando do próprio Distrobox (`distrobox upgrade`), que usa o apt.
- **Versões novas**: para cada aplicativo, o Gerenciador pergunta ao apt
  (`apt-cache policy`) qual é a versão mais nova nos repositórios e compara com a
  instalada. Assim mostra cada um em **Com atualização**, **Em dia** ou **Sem fonte de
  atualização** (instalado de um arquivo que nenhum repositório oferece). Ao abrir, usa
  a lista que o contêiner já tem, sem baixar nada; **Verificar atualizações** antes roda
  `apt-get update`.
- **Remover**: ao desinstalar um aplicativo, o MultiPak remove o pacote dele com o
  `dpkg --purge`, que recusa a remoção se outro pacote depende dele. Dependências que o
  aplicativo trouxe não são removidas junto (regra geral do MultiPak).

### 1.5 O que (ainda) não funciona

- O MultiPak não sabe para qual distribuição o pacote foi feito. Se faltar uma
  biblioteca, escolha outro ambiente nas opções avançadas.
- Pacotes feitos para outra arquitetura: o MultiPak só aceita pacotes para a arquitetura
  do computador (como `amd64`) ou para qualquer uma (`all`).

---

## 2. Explicação técnica

Caminhos de código são relativos a `src/multipak`.

### 2.1 Visão geral dos módulos

| Módulo | Papel |
| --- | --- |
| `metadata.py` (`_deb_members`, `_deb_fields`, `parse_control`) | leitura do `ar` e do `control` |
| `models.py` (ambientes `ubuntu-*` e `debian-*`) | ambientes de reserva |
| `catalog.py` (`parse_catalog`, `debian_stable_version`) | versões do dia |
| `suggestions.py` | confiança Baixa e texto da sugestão |
| `engine.py` (`_install_deb`) | instalação com o apt |
| `package_check.py` (`check_apt`, `parse_apt_policy`) | versões novas |
| `guest.py` | estado (`dpkg-query`), arquivos e remoção |
| `file_associations.py` | tipos MIME do sistema para `.deb` |

### 2.2 Leitura, sem executar

`_deb_members` exige a marca `!<arch>` e um primeiro membro `debian-binary` com `2.0`;
cabeçalhos inválidos são recusados. `_deb_fields` lê o `control.tar.*` (gzip, xz, bzip2
ou zstd) com limite de tamanho e pega só o `control`. São obrigatórios `Package`,
`Version` e `Architecture`. Dependências exibidas: `Pre-Depends` e `Depends`.
Arquiteturas aceitas: a da máquina (`amd64`/`x86_64`), `all` e `noarch`.

### 2.3 Escolha do ambiente

O `.deb` não traz pista de origem, então não há dica: a sugestão é o ambiente padrão do
Ubuntu, com confiança **Baixa** e um texto que explica a escolha. O catálogo lê as
versões da lista de imagens do Distrobox (Ubuntu `NN.NN`, Debian por número) e resolve
`debian-stable` lendo `https://deb.debian.org/debian/dists/stable/Release`. As versões
flutuantes (`latest`, `stable`) são resolvidas para o número real antes de nomear o
contêiner.

### 2.4 Instalação (`engine._install_deb`)

1. Num contêiner que só esta instalação usa, `dpkg --force-confold --configure -a`
   termina uma configuração interrompida antes (falha ignorada).
2. `apt-get -y -o Dpkg::Use-Pty=0 -o Dpkg::Options::=--force-confold [--fix-broken]
   install <arquivo>`, com `DEBIAN_FRONTEND=noninteractive`. `--fix-broken` só entra
   quando o contêiner não é compartilhado.
3. Se falhar por índices velhos (pacote não encontrado, falha de download), roda
   `apt-get update` e tenta de novo uma vez. Uma falha do dpkg ou de um script do pacote
   **nunca** é repetida automaticamente.

### 2.5 No contêiner (`guest.py`)

Estado: `dpkg-query -W -f=${Status}` (precisa ser `install ok installed`). Arquivos:
`dpkg-query -L`; os `.desktop` encontrados viram atalhos. Remoção: `dpkg --purge nome`,
com as checagens de dependência do dpkg ligadas.

### 2.6 Versões novas (`package_check.check_apt`)

`apt-cache policy` mostra, para cada pacote, a versão instalada e a candidata. Se a
única origem da versão instalada é o próprio registro do dpkg (`/var/lib/dpkg/status`),
nenhum repositório oferece o pacote: **Sem fonte de atualização**.

### 2.7 Diferenças em relação ao uso normal

| Assunto | Debian/Ubuntu de verdade | MultiPak |
| --- | --- | --- |
| Instalar arquivo local | `apt install ./arquivo.deb` | igual, sem terminal |
| Escolha da distribuição | o próprio sistema | o Ubuntu LTS mais novo, ou o que você escolher |
| Remover | `apt remove` pode levar dependências | `dpkg --purge`, só o pacote |

### 2.8 Onde olhar quando algo dá errado

- `~/.local/share/multipak/installations/<id>/installation.log`.
- `distrobox enter MultiPak-Ubuntu-24.04 -- dpkg -s <nome>`.
- Biblioteca faltando: reinstale escolhendo outra distribuição ou versão nas opções
  avançadas.
