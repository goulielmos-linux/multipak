# Como o MultiPak instala pacotes do Gentoo

*MultiPak 1.1.0.*

---

## 1. Explicação simples

### 1.1 O que é um `.gpkg.tar`?

O Gentoo normalmente compila tudo a partir do código-fonte, mas também tem **pacotes
binários**. O formato atual, **GPKG**, é um TAR sem compressão com a marca `gpkg-1`, os
metadados (`metadata.tar.*`: categoria, nome, versão, dependências, USE) e os arquivos
do programa (`image.tar.*`), cada parte com assinatura opcional.

### 1.2 O que o MultiPak faz?

1. **Lê sem executar:** só os metadados. Nada é extraído.
2. **Cria (ou reaproveita) o contêiner** `MultiPak-Gentoo-Linux`, da imagem oficial
   `docker.io/gentoo/stage3:amd64-openrc`.
3. **Prepara o Gentoo:** baixa a árvore de pacotes (Portage) com assinatura verificada,
   muda para o perfil de desktop (é para ele que o Gentoo publica binários de programas
   gráficos), liga os pacotes binários oficiais e instala sudo, dados de teclado, fontes
   e o idioma do sistema.
4. **Instala com o emerge**, que pega as dependências em binário do servidor oficial.
   Na prática, quase nada é compilado.
5. **Atalho no menu.**

Pacotes sem assinatura, como os gerados localmente com `quickpkg`, também são aceitos.

### 1.3 O contêiner

- Um ambiente só, `gentoo-rolling` ("Gentoo Linux (rolling release)").
- O Distrobox conhece o emerge, mas compilaria as suas ferramentas e não liga os
  binários. O MultiPak prepara tudo antes e marca a preparação básica como feita.
- A árvore de pacotes é atualizada no máximo uma vez por dia, antes de uma instalação.
- O GTK 3 segue o tema claro ou escuro do computador.

### 1.4 Atualizar

O comando comum do Distrobox para atualizar contêineres (`distrobox upgrade`) não serve
no Gentoo: ele só baixa a lista de pacotes nova (a árvore do Portage) e não atualiza
nenhum programa. Por isso, **Gerenciador → Atualizar** (o ícone de qualquer aplicativo
do contêiner, ou Atualizar tudo) roda o script do MultiPak, que baixa a lista nova e
atualiza todos os programas do contêiner (`emerge -uDN @world`) com os pacotes binários.

**Versões novas**: para cada aplicativo, o Gerenciador pergunta ao Portage qual é a
versão mais nova disponível na lista de pacotes e compara com a instalada. Assim mostra
cada um em **Com atualização**, **Em dia** ou **Sem fonte de atualização** (instalado de
um arquivo que nenhum repositório oferece). Ao abrir, usa a lista que o contêiner já
tem, sem baixar nada; **Verificar atualizações** antes roda `emerge-webrsync`.

### 1.5 Remover

Ao desinstalar um aplicativo, o MultiPak remove o pacote dele com o `emerge --unmerge`.
O emerge não confere se outro aplicativo precisa desse pacote, então o MultiPak confere
antes: se precisar, a desinstalação é recusada e nada é apagado.

### 1.6 O que (ainda) não funciona

- Um pacote compilado com USE muito diferentes do perfil de desktop pode puxar
  dependências que não existem em binário (essas seriam compiladas, demorando muito).
- A primeira instalação é longa (alguns minutos, com contêiner novo).
- Só x86-64 (a imagem `amd64`).

---

## 2. Explicação técnica

Caminhos de código são relativos a `src/multipak`.

### 2.1 Visão geral dos módulos

| Módulo | Papel |
| --- | --- |
| `gentoo.py` | leitor, `is_candidate`, `dependency_lines` |
| `metadata.py` | detecção depois do Arch, antes do Slackware |
| `models.py` (`GENTOO_ENVIRONMENT`) | `gentoo-rolling`, `MultiPak-Gentoo-Linux` |
| `engine.py` (`gentoo_setup_hook`, `_install_gentoo`) | hook e instalação |
| `assets/gentoo-setup.sh` | hook, `--sync`, `--install`, `--upgrade` |
| `guest.py` (`gentoo_entries`, `gentoo_files`, `gentoo_dependents`) | banco `/var/db/pkg` |
| `container_progress.py` | `>>> Emerging [binary] (N of M)` → porcentagem |
| `updates.py` (`SCRIPTS["gentoo"]`) | atualização |
| `assets/multipak-gentoo.xml` | MIME `application/x-gentoo-binary-package` (`*.gpkg.tar`) |

### 2.2 Leitura e detecção

`gentoo.is_candidate`: TAR sem compressão chamado `.gpkg.tar` ou com membro `gpkg-1`. O
`metadata.tar.*` é descomprimido em memória (zstd pelo `python-zstandard` do Flatpak,
xz, gzip…). As assinaturas `.sig` não são verificadas na leitura.
`dependency_lines` junta `|| ( a b )` como "a ou b" e ignora condições de USE e
bloqueios `!`. Dica `gentoo-rolling`, confiança **Alta**.

### 2.3 Pre-init hook (`gentoo-setup.sh` sem opção)

1. `emerge-webrsync` (HTTPS; assinatura do snapshot verificada).
2. `eselect profile set default/linux/amd64/23.0/desktop` se o perfil não for desktop.
3. `FEATURES="${FEATURES} getbinpkg"` no `make.conf` e `getuto` (chaves do binhost). O
   stage3 já traz `binrepos.conf` com o binhost oficial e `verify-signature = true`.
   **Não** se usa `binpkg-request-signature`, que recusaria pacotes do usuário sem
   assinatura.
4. `emerge --usepkg --with-bdeps=n --noreplace` de sudo, bash-completion, lsof, bc,
   wget, pinentry, xkeyboard-config, dejavu.
5. Idioma do host em `/etc/locale.gen` + `locale-gen`; `gtk_preference`.
6. Cópia em `/usr/libexec/multipak-gentoo-setup.sh` e `/.containersetupdone` (o
   Distrobox pula a etapa de pacotes; o Python 3 já vem no stage3).

`--binpkg-respect-use=n` foi descartado: puxava binários com `abi_x86_32` e exigia
mudar USE.

### 2.4 Instalação (`engine._install_gentoo`)

O `emerge` só aceita um arquivo **dentro do PKGDIR, no caminho que ele mesmo daria**.

1. `_install_gentoo` relê categoria e PF do arquivo copiado e copia o script para a
   pasta do guest (funciona em contêineres Gentoo que o MultiPak não criou).
2. `--sync`: tema e `emerge-webrsync` se a última sincronização tem mais de 24 h.
3. `--install ARQ CAT PF`: valida os nomes, copia para
   `/var/cache/multipak/binpkgs/<cat>/<PF>.gpkg.tar`, `PKGDIR=…
   FEATURES=-binpkg-multi-instance`, `emaint binhost --fix`, `emerge --usepkg
   --with-bdeps=n <arquivo>` e limpa o cache de binários. O nome original do arquivo não
   importa.

### 2.5 No contêiner (`guest.py`)

`/var/db/pkg/<cat>/<PF>/`: `CONTENTS` (`obj CAMINHO MD5 MTIME`; o caminho pode ter
espaços) para os arquivos; `RDEPEND`/`PDEPEND` já avaliados para os dependentes. Sem
dependentes: `emerge --ask=n --quiet --unmerge =cat/PF` para cada slot instalado.

### 2.6 Atualização

`--upgrade`: tema, `emerge-webrsync`, `emerge --usepkg --update --deep --newuse @world`,
limpeza do cache.

### 2.7 Diferenças em relação ao uso normal

| Assunto | Gentoo de verdade | MultiPak |
| --- | --- | --- |
| Compilação | regra | exceção: binários oficiais do perfil desktop |
| Instalar arquivo | colocar no PKGDIR à mão | automático |
| `--unmerge` com dependentes | remove | recusa |
| Árvore | `emerge --sync` quando quiser | `emerge-webrsync` no máximo 1×/dia antes de instalar |

### 2.8 Onde olhar quando algo dá errado

- `~/.local/share/multipak/installations/<id>/installation.log`.
- `distrobox enter MultiPak-Gentoo-Linux -- eselect profile show`.
- Ver se algo foi compilado: `/var/log/emerge.log` no contêiner.
