# Como o MultiPak instala pacotes do SliTaz

*MultiPak 1.2.0.*

---

## 1. Explicação simples

### 1.1 O que é um `.tazpkg`?

O SliTaz é uma distribuição minúscula (a ISO Base tem uns 30 MB). O gerenciador de
pacotes é o **tazpkg**, e um pacote é um arquivo CPIO com uma ficha chamada
**receipt** (um script de shell com nome, versão, descrição e dependências), a lista de
arquivos e os arquivos do programa comprimidos (`fs.cpio.lzma`).

O SliTaz mantém repositórios separados para **64 bits** (x86_64) e **32 bits** (i486). A
linha usada é a *cooking*, de desenvolvimento contínuo.

### 1.2 O que o MultiPak faz?

1. **Lê sem executar:** o receipt é um script, mas o MultiPak só lê as atribuições
   simples (`PACKAGE="…"`), sem rodá-lo. A arquitetura vem dos próprios programas do
   pacote (o cabeçalho do primeiro ELF), não do nome do arquivo.
2. **Escolhe o ambiente da arquitetura:** `slitaz-cooking` (64 bits, contêiner
   `MultiPak-SliTaz-cooking`) ou `slitaz-cooking-i486` (32 bits, contêiner
   `MultiPak-SliTaz-cooking-i486`). Pacotes `any` podem ir para os dois.
3. **Monta a imagem na primeira vez:** o SliTaz não publica imagem de contêiner atual (a
   do Docker Hub é de 2016). O MultiPak baixa a ISO Base oficial, confere o md5, tira de
   dentro o sistema de arquivos e o importa no Podman. **Nada da ISO é executado.**
4. **Prepara o contêiner:** o Distrobox não conhece o tazpkg. O MultiPak instala
   Python 3, sudo, bash, dados de teclado, fontes, drivers gráficos e o idioma do
   sistema.
5. **Instala com o tazpkg** e, se o SliTaz tiver, a tradução (`<nome>-lang`).
6. **Atalho no menu.**

### 1.3 Segurança dos downloads

O `wget` do busybox (o que vem no SliTaz) **não verifica certificados HTTPS**. Por isso
a imagem montada pelo MultiPak já leva o GNU Wget e os certificados, baixados por HTTPS
verificado e conferidos com o `packages.md5` do repositório. Eles são instalados
primeiro, antes de qualquer outro download dentro do contêiner.

### 1.4 Atualizar

O `distrobox upgrade` não conhece o tazpkg. **Gerenciador → Atualizar** (o ícone de
qualquer aplicativo do contêiner, ou Atualizar tudo) roda o script do MultiPak: atualiza
as listas e faz `tazpkg up --auto`.

**Versões novas**: o Gerenciador pergunta ao tazpkg (compara `installed.info` com o
`packages.info` do espelho) quais aplicativos têm versão nova e mostra cada um em **Com
atualização**, **Em dia** ou **Sem fonte de atualização** (instalado de um arquivo que
nenhum repositório oferece). Ao abrir, usa a lista que o contêiner já tem, sem baixar
nada; **Verificar atualizações** antes roda `tazpkg recharge`.

### 1.5 Remover

Ao desinstalar um aplicativo, o MultiPak remove o pacote dele com o `tazpkg remove`.
Sem terminal, o tazpkg apagaria o pacote mesmo que outro aplicativo precise dele,
deixando esse aplicativo quebrado. Por isso o MultiPak confere antes: se outro
aplicativo precisa dele, a desinstalação é recusada e nada é apagado.

### 1.6 O que (ainda) não funciona

- Pacotes feitos para o SliTaz 5.0, a versão estável: o MultiPak só oferece a linha
  *cooking*, porque a 5.0 é antiga. Esses pacotes vão para a *cooking* e podem não
  funcionar.
- O GTK 3.22 do SliTaz não usa o tema GTK do computador: fica no Adwaita, claro ou
  escuro conforme o computador.

---

## 2. Explicação técnica

Caminhos de código são relativos a `src/multipak`.

### 2.1 Visão geral dos módulos

| Módulo | Papel |
| --- | --- |
| `slitaz.py` | leitor do CPIO newc, receipt sem executar, arquitetura pelo ELF |
| `slitaz_image.py` | ISO9660 → `rootfs.gz` → CPIO → TAR → `podman import` |
| `models.py` (`SLITAZ_ENVIRONMENTS`, `slitaz_environment`) | dois ambientes, imagens `localhost/multipak-slitaz:cooking[-i486]` |
| `engine.py` (`slitaz_setup_hook`, `SLITAZ_SCRIPT`, `_install_slitaz`) | hook e instalação |
| `assets/slitaz-setup.sh` | hook, `--recharge`, `--upgrade` |
| `guest.py` (`slitaz_entry`, `slitaz_files`, `slitaz_dependents`) | banco `/var/lib/tazpkg` |
| `kirigami/live.py` | só oferece o ambiente da arquitetura do pacote |
| `assets/multipak-slitaz.xml` | MIME `application/x-tazpkg` (`*.tazpkg`) |

### 2.2 Leitura

`metadata.py` reconhece a marca inicial (*magic number*) `07070`, os primeiros bytes de
todo arquivo cpio. `slitaz.read_info` percorre o CPIO com limites
(membros, nome, tamanho do receipt), lê o receipt por expressões regulares (só valores
literais, sem expansão de shell) e procura o primeiro ELF dentro de `fs.cpio.lzma`/`.gz`
para a arquitetura (`EM_386` → i486, `EM_X86_64` → x86_64). Num computador x86_64, i486
e `any` também são aceitos. Dica do ambiente pela arquitetura, confiança **Alta**.

### 2.3 Imagem local (`slitaz_image.ensure_image`)

Se a imagem já existe, nada é feito. Senão, numa pasta em
`~/.local/share/multipak/slitaz-image`:

1. Baixa `slitaz-base-<arch>-cooking.iso` e o `.md5` de `https://mirror.slitaz.org`,
   conferindo o md5 (limite de 256 MB).
2. Lê a ISO9660 em Python e acha `boot/rootfs.gz`; descomprime (LZMA ou gzip, vários
   fluxos).
3. Converte o CPIO em TAR, sem `boot`, `lib/modules`, `lib/firmware`, `/init` e
   dispositivos; acrescenta o os-release (`ID=slitaz`, `VERSION_ID=cooking` ou
   `cooking-i486`), o espelho em HTTPS e os pacotes de *bootstrap* (GNU Wget,
   certificados e dependências), conferidos pelo `packages.md5` (o md5 do
   `packages.info` não é o do arquivo).
4. `podman import`, com um rótulo que guarda o md5 da ISO.

### 2.4 Pre-init hook (`slitaz-setup.sh` sem opção)

Instala os pacotes de *bootstrap* primeiro; `tazpkg recharge` e baixa
`packages.{info,list,md5,equiv}` direto com o GNU Wget (o `bundle.tar.lzma` do espelho
pode estar defasado, e o tazpkg rejeitaria pacotes novos com "Checksum error"); instala
sudo, bash, python3, xorg-xkeyboard-config, ttf-dejavu, mesa-dri, glibc-locale; gera o
locale do host com `localedef`; cria um `getent` em shell (o busybox não tem, e o
Distrobox usa); escreve a preferência de tema; copia-se para
`/usr/libexec/multipak-slitaz-setup.sh`; cria `/.containersetupdone`.

### 2.5 Instalação (`engine._install_slitaz`)

1. `sudo sh /usr/libexec/multipak-slitaz-setup.sh --recharge` (listas + tema).
2. `sudo tazpkg install <arquivo>`.
3. `sudo tazpkg get-install <nome>-lang` (falha ignorada se não existir).

### 2.6 No contêiner (`guest.py`)

`/var/lib/tazpkg/installed/<nome>/` (receipt e `files.list`) para estado e arquivos;
`installed.info` (oitavo campo = dependências) para `slitaz_dependents`. Sem
dependentes: `tazpkg remove` com a entrada padrão fechada.

### 2.7 Atualização

`updates.SCRIPTS["slitaz"]` copia o script e roda `--upgrade`: `--recharge` e
`tazpkg up --auto`.

### 2.8 Diferenças em relação ao uso normal

| Assunto | SliTaz de verdade | MultiPak |
| --- | --- | --- |
| Sistema | live CD/instalação da ISO | contêiner importado da mesma ISO |
| Downloads | busybox wget sem checar TLS | GNU Wget com certificados |
| Listas | `bundle.tar.lzma` | listas baixadas direto |
| Remover com dependentes | remove e quebra | recusa |

### 2.9 Onde olhar quando algo dá errado

- `~/.local/share/multipak/installations/<id>/installation.log`.
- `distrobox enter MultiPak-SliTaz-cooking -- tazpkg list`.
- Refazer a imagem: `podman rmi localhost/multipak-slitaz:cooking`.
- Não rode o MultiPak com `XDG_DATA_HOME` trocado: o Podman passaria a usar outro
  armazenamento e refaria a imagem.
