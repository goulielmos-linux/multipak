# Como o MultiPak instala pacotes do Void Linux

*MultiPak 1.2.0.*

---

## 1. Explicação simples

### 1.1 O que é um pacote `.xbps`?

O Void Linux é uma distribuição independente, de atualização contínua (*rolling
release*). O gerenciador de pacotes dele se chama **XBPS**, e um pacote é um arquivo
`.xbps`: um arquivo TAR comprimido (normalmente com zstd) com os arquivos do programa e
duas fichas, `props.plist` (nome, versão, arquitetura, dependências) e `files.plist`
(lista de arquivos).

O Void existe em duas variantes: com **glibc** (a biblioteca C da maioria das
distribuições) e com **musl** (outra biblioteca C, mais enxuta). Os pacotes de uma não
servem na outra. A variante glibc também tem pacotes de 32 bits (`i686`).

### 1.2 O que o MultiPak faz?

1. **Lê sem executar:** abre o TAR por fora e lê só as fichas `props.plist` e
   `files.plist`. Os scripts de instalação do pacote (`INSTALL`, `REMOVE`) não são
   executados nem extraídos.
2. **Escolhe a variante pela arquitetura:** `x86_64` vai para o contêiner glibc de 64
   bits, `x86_64-musl` para o musl e `i686` para o glibc de 32 bits. `noarch` serve em
   qualquer um. Outras arquiteturas são recusadas com uma mensagem clara.
3. **Cria (ou reaproveita) o contêiner Void dessa variante,** feito das imagens oficiais
   `ghcr.io/void-linux/void-glibc-full` (64 e 32 bits) e
   `ghcr.io/void-linux/void-musl-full`.
4. **Instala pelo XBPS:** o XBPS não instala um arquivo solto como o `dnf` ou o `apt`.
   O MultiPak monta um repositório local de um pacote só, aponta o XBPS para ele e pede
   a versão exata. As dependências vêm dos repositórios oficiais do Void.
5. **Atalho no menu:** os atalhos `.desktop` do aplicativo são exportados para o menu.

O XBPS instala o arquivo por um repositório local, que não é assinado, então **a
assinatura do pacote não é verificada**. A instalação segue mesmo assim, **com um
aviso** no resultado. Instale só pacotes de fontes confiáveis.

### 1.3 O contêiner

- Um ambiente para cada variante, com o seu contêiner:

  | Ambiente | Contêiner | Pacotes |
  | --- | --- | --- |
  | Void Linux (rolling release) | `MultiPak-Void-Linux` | `x86_64` |
  | Void Linux musl (rolling release) | `MultiPak-Void-Linux-musl` | `x86_64-musl` |
  | Void Linux 32-bit (rolling release) | `MultiPak-Void-Linux-32-bits` | `i686` |

- Um pacote `noarch` serve em qualquer um dos três; o MultiPak sugere o glibc de 64
  bits.
- O Distrobox conhece o XBPS e prepara o contêiner sozinho (locales, sudo, ferramentas).
- Antes de cada instalação, o MultiPak ajusta o GTK 3 para seguir o tema claro ou
  escuro do computador (sem isso, os ícones da barra podiam ficar invisíveis).
- O Void já traz as traduções dentro de cada pacote: o aplicativo abre no idioma do
  sistema sem pacote extra.

### 1.4 Atualizar

**Gerenciador → Atualizar** (o ícone de qualquer aplicativo do contêiner, ou Atualizar
tudo) roda o script do MultiPak, que primeiro atualiza o próprio XBPS e depois o resto
do sistema. O `distrobox upgrade` comum não serve no Void: ele pergunta "confirmar?", e
sem terminal o XBPS responde "Aborting!" e **termina com sucesso sem ter atualizado
nada**.

**Versões novas**: o Gerenciador consulta `xbps-install -un` e `xbps-query -R` para
saber quais aplicativos têm versão nova e mostra cada um em **Com atualização**, **Em
dia** ou **Sem fonte de atualização** (instalado de um arquivo que nenhum repositório
oferece). Ao abrir, usa a lista que o contêiner já tem, sem baixar nada; **Verificar
atualizações** antes roda `xbps-install -S`.

### 1.5 Remover

Ao desinstalar um aplicativo, o MultiPak remove o pacote dele com o `xbps-remove`, que
já confere as dependências: se outro aplicativo precisa do pacote, o XBPS recusa e nada
é apagado. Dependências que o aplicativo trouxe não são removidas junto (regra geral do
MultiPak).

### 1.6 O que (ainda) não funciona

- Verificação de assinatura do arquivo local.
- Compressão LZ4 (rara): o MultiPak avisa que não lê.

---

## 2. Explicação técnica

Caminhos de código são relativos a `src/multipak`.

### 2.1 Visão geral dos módulos

| Módulo | Papel |
| --- | --- |
| `xbps.py` | lê `props.plist`/`files.plist` com `plistlib` e valida nome, `pkgver` e arquitetura |
| `metadata.py` (`_xbps_info`, `read_xbps_info`) | descompressão (zstd, xz, gzip, bzip2, tar puro) e detecção |
| `models.py` (`VOID_ENVIRONMENTS`, `void_environment`, `void_variant`) | ambientes `void-rolling`, `void-musl` e `void-i686`, e a variante de um contêiner pela imagem |
| `void_image.py` | imagem de 32 bits, baixada pelo digest e guardada com nome local |
| `engine.py` (`_install_xbps`, `_finish_install`) | checagem da arquitetura do contêiner, aviso e instalação |
| `assets/void-setup.sh` | `--install FILE PKGVER ARCH` e `--upgrade` |
| `guest.py` | estado (`xbps-query`), lista de arquivos e remoção |
| `updates.py` (`SCRIPTS["void"]`) | atualização pelo script em vez do `distrobox upgrade` |
| `file_associations.py`, `assets/` | tipo MIME para `*.xbps` |

### 2.2 Leitura, sem executar

- **Descompressão**: zstd pelo módulo `compression.zstd` do Python ou pelo
  `python-zstandard` que já vem no Flatpak; xz, gzip, bzip2 e TAR sem compressão pela
  biblioteca padrão. LZ4 tem mensagem própria.
- O TAR é percorrido em modo *stream*, com limite de leitura; só `props.plist` e
  `files.plist` são lidos (em memória). `INSTALL`, `REMOVE` e `*.msg` são pulados.
- `props.plist` é lido com `plistlib` (formato XML). Dependências exibidas: só
  `run_depends` (o `xbps-src` já põe ali o pacote de cada biblioteca); `shlib-requires`
  só se `run_depends` estiver vazio.

### 2.3 Detecção

Em `metadata._package_metadata`, o Void é testado **antes do Arch** (os dois são TAR
comprimido). O arquivo vira `xbps` quando o conteúdo tem as fichas do XBPS; com a
extensão `.xbps`, a falta delas é erro (em vez de cair no Arch). As arquiteturas
aceitas para `xbps` são `x86_64`, `x86_64-musl`, `i686` e `noarch`; `i686-musl` e as
outras são recusadas em `inspect_package`.

### 2.4 Escolha do ambiente

A sugestão vem da arquitetura do pacote (`void-rolling`, `void-musl` ou `void-i686`),
com confiança **Alta** (a ficha identifica o Void). Para um pacote que não é `noarch`, o
assistente só oferece o ambiente da arquitetura dele. Um contêiner existente só é
reaproveitado se o os-release disser `ID=void`; como o os-release é igual nas três
variantes, a variante vem da imagem do contêiner (`void_variant`). Antes de instalar,
`_finish_install` roda `xbps-uhelper arch` no contêiner e recusa o contêiner de outra
arquitetura (um `noarch` passa em qualquer um).

O contêiner de 32 bits usa a imagem `linux/386` da `void-glibc-full`: o kernel de 64
bits roda programas de 32 bits sem emulação. Essa imagem tem a mesma etiqueta (`latest`)
da de 64 bits, então `void_image.ensure_image` acha o digest dela com `podman manifest
inspect`, baixa por ele (a etiqueta continua na de 64 bits) e guarda a imagem como
`localhost/multipak-void:i686`. Dentro dela, `xbps-uhelper arch` responde `i686`.

### 2.5 Instalação (`void-setup.sh --install`)

1. `xbps-install -Suy xbps` — o XBPS recusa qualquer transação enquanto houver xbps
   novo.
2. `gtk_preference` — escreve `/etc/xdg/gtk-3.0/settings.ini` com
   `gtk-application-prefer-dark-theme` conforme o `settings.ini` do usuário do host
   (achado pelo `SUDO_UID`). Não é fatal.
3. Repositório temporário (`mktemp -d`, apagado por `trap`): o arquivo é copiado com o
   nome canônico `<pkgver>.<arch>.xbps` e indexado com `xbps-rindex -a`.
4. `xbps-install -Sy -R <repo> -c <repo>/cache -- <pkgver>` — `-R` põe o repositório
   local na frente e mantém os oficiais (assinados) para as dependências; o cache
   privado evita reaproveitar outro arquivo com o mesmo nome e versão.

`engine._install_xbps` relê o arquivo copiado (`read_xbps_info`) e confere nome e
versão antes de rodar o script, e acrescenta o aviso de assinatura à transação.

### 2.6 No contêiner (`guest.py`)

- Estado: `xbps-query -p pkgver` e `-p state` (precisa ser `installed`).
- Arquivos: `xbps-query -f`; os links aparecem como `caminho -> alvo`, e o alvo é
  cortado. Os `.desktop` encontrados viram atalhos.
- Remoção: `xbps-remove -y -- nome`, sem `-R` e sem `--force-revdeps`.

### 2.7 Atualização

`updates.SCRIPTS["void"]` copia o script para `/usr/libexec/multipak-void-setup.sh`
(`podman cp`) e roda `sudo sh … --upgrade`: `xbps-install -Suy xbps` e depois
`xbps-install -uy`.

### 2.8 Diferenças em relação ao uso normal

| Assunto | Void de verdade | MultiPak |
| --- | --- | --- |
| Instalar arquivo local | `xbps-rindex` + `xbps-install -R` à mão | igual, automático, com cache privado |
| Assinatura do arquivo | só repositórios assinados | não verificada (aviso) |
| musl e 32 bits | variante própria | um contêiner para cada |
| Atualização | `xbps-install -Su` com confirmação | `-Suy xbps` e depois `-uy`, sem terminal |

### 2.9 Onde olhar quando algo dá errado

- Registro da instalação: `~/.local/share/multipak/installations/<id>/installation.log`.
- Dentro do contêiner: `distrobox enter MultiPak-Void-Linux -- xbps-query -l`.
- Atualização manual: `distrobox enter MultiPak-Void-Linux -- sudo xbps-install -Su`.
- Nos contêineres musl e de 32 bits, troque o nome por `MultiPak-Void-Linux-musl` ou
  `MultiPak-Void-Linux-32-bits`.
