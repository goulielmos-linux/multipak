# Como o MultiPak instala pacotes do Solus

*MultiPak 1.1.0.*

---

## 1. Explicação simples

### 1.1 O que é um pacote `.eopkg`?

O Solus é uma distribuição independente, de atualização contínua. O gerenciador de
pacotes dele, o **eopkg**, nasceu do pisi, o gerenciador do Pardus e do Pisi Linux, e
mantém o mesmo formato: um arquivo ZIP com uma ficha `metadata.xml` (nome, versão,
dependências, distribuição), a lista `files.xml` e os arquivos do programa em
`install.tar.xz`.

### 1.2 O que o MultiPak faz?

1. **Lê sem executar:** abre o ZIP e lê só `metadata.xml`. Nada é extraído e nenhum
   script roda.
2. **Distingue Solus de Pisi Linux:** pelo campo *Distribution* da ficha, não pela
   extensão. Um `.pisi` com ficha do Solus é tratado como Solus, e vice-versa.
3. **Recusa o que não instala:** pacotes *delta* (`.delta.eopkg`, que só atualizam
   uma versão anterior já instalada) e arquiteturas diferentes de x86-64.
4. **Prepara a imagem:** o Solus não publica imagem de contêiner. Na primeira vez, o
   MultiPak baixa uma imagem **não oficial** (`docker.io/silkeh/solus:base`), montada em
   CI público a partir do repositório oficial do Solus só com o sistema base, e completa
   essa imagem localmente com uma camada pequena.
5. **Cria (ou reaproveita) o contêiner** `MultiPak-Solus-Linux` e instala com
   `eopkg install`, que resolve as dependências pelo repositório do Solus.
6. **Atalho no menu.**

### 1.3 O contêiner

- Um ambiente só, `solus-rolling` ("Solus (rolling release)").
- O Distrobox não conhece o eopkg. O MultiPak prepara o contêiner antes (Python 3,
  sudo, bibliotecas gráficas, ferramentas que o Distrobox usa) e marca a preparação
  básica como feita.
- Antes de cada instalação: atualiza a lista de pacotes, adiciona tema de ícones,
  o leitor de SVG e o tema Breeze do GTK, e segue o tema claro ou escuro do computador,
  inclusive em programas GTK 2.
- Os locales já vêm na imagem: o aplicativo abre no idioma do sistema.
- O aviso "Failed handler 'tmpfiles'" do usysconf depois de instalar ou remover é
  inofensivo (o contêiner sem root não muda o dono de `/dev/lp*`).

### 1.4 Atualizar

O `distrobox upgrade` não conhece o eopkg. **Gerenciador → Atualizar** (o ícone de
qualquer aplicativo do contêiner, ou Atualizar tudo) roda o script do MultiPak: `eopkg
update-repo` e `eopkg upgrade`.

**Versões novas**: o Gerenciador consulta `eopkg list-upgrades` e `eopkg info` para
saber quais aplicativos têm versão nova e mostra cada um em **Com atualização**, **Em
dia** ou **Sem fonte de atualização** (instalado de um arquivo que nenhum repositório
oferece). Ao abrir, usa a lista que o contêiner já tem, sem baixar nada; **Verificar
atualizações** antes roda `eopkg update-repo`.

### 1.5 Remover

Ao desinstalar um aplicativo, o MultiPak remove o pacote dele com o `eopkg remove`. O
eopkg apagaria junto qualquer outro aplicativo que dependa desse pacote, então o
MultiPak confere antes: se outro aplicativo precisa dele, a desinstalação é recusada e
nada é apagado.

### 1.6 O que (ainda) não funciona

- Pacotes delta e arquiteturas fora de x86-64.
- A imagem não é oficial (o próprio Solus não publica uma). O código fica num
  repositório público (gitlab.com/silkeh/docker-solus) e os pacotes vêm do
  repositório oficial.

---

## 2. Explicação técnica

Caminhos de código são relativos a `src/multipak`.

### 2.1 Visão geral dos módulos

| Módulo | Papel |
| --- | --- |
| `pisi.py` | lê Pisi Linux **e** Solus (`Info.family` = `pisilinux` ou `solus`) |
| `metadata.py` | ZIP → `pisi.read_info` → tipo `eopkg` ou `pisi`; Solus só x86_64 |
| `models.py` (`SOLUS_BASE_IMAGE`, `SOLUS_ENVIRONMENT`) | imagem base e imagem local `localhost/multipak-solus:base` |
| `solus_image.py` | `podman pull` da base + `podman build` da camada local |
| `engine.py` (`solus_setup_hook`, `_install_eopkg`) | pre-init hook e instalação |
| `assets/solus-setup.sh` | hook, `--prepare` e `--upgrade` |
| `guest.py` (`EOPKG_DATABASE`, `pisi_dependents`) | estado, arquivos e recusa de remoção |
| `updates.py` (`SCRIPTS["solus"]`) | atualização pelo script |
| `assets/multipak-solus.xml` | MIME `application/x-eopkg-package` (`*.eopkg`, subclasse de ZIP) |

### 2.2 Leitura, sem executar

`pisi.read_info` abre o ZIP, lê `metadata.xml` com limite de tamanho e recusa DOCTYPE.
Aceita `PackageFormat` 1.2. A família vem da *Distribution* (`Solus` ou `Pisi Linux`).
As mensagens de erro usam `{0}` = "Pisi Linux" ou "Solus". Pacotes delta são recusados
com explicação.

### 2.3 Detecção e ambiente

O cabeçalho `PK\x03\x04` com sufixo `.apk` é um app de Android (mensagem própria);
qualquer outro ZIP vai para `pisi.read_info`. Família `solus` → tipo `eopkg`, dica
`solus-rolling` com origem `metadata`, confiança **Alta**, texto que avisa da imagem
não oficial. A lista de arquiteturas aceitas é só `{x86_64}`.

### 2.4 Imagem local (`solus_image.ensure_image`)

A imagem base pula os scripts de configuração dos pacotes, então falta o que o
`baselayout` criaria: `/etc/passwd` (sem ele o Podman não inicia contêiner como root),
`group`, `shadow`, `gshadow` e os links do os-release. Um `Containerfile` copia esses
modelos de `/usr/share/baselayout` e cria os links. Build com `--pull=never
--network=none`, numa pasta temporária em `~/.local/share/multipak/solus-image`. Se a
imagem `localhost/multipak-solus:base` já existe, nada é feito.

### 2.5 Pre-init hook (`solus-setup.sh` sem opção)

Recria os links do os-release; se `/.containersetupdone` não existe: `eopkg
update-repo` e instala python3, bash, sudo, shadow, util-linux, procps-ng, curl,
rsync, xauth, xkeyboard-config, dejavu-fonts-ttf, mesalib, vulkan e outras ferramentas
do Distrobox; garante `@includedir /etc/sudoers.d` se existir `/etc/sudoers`; cria
`/.containersetupdone`. `/etc/sudoers` não é criado: o sudo do Solus é *stateless* e lê
`/usr/share/defaults/etc/sudo/sudoers`, que já inclui `/etc/sudoers.d`.

### 2.6 Instalação (`engine._install_eopkg`)

1. Copia `solus-setup.sh` para a pasta temporária do guest e roda `sudo sh … --prepare`:
   os-release, `eopkg update-repo`, `adwaita-icon-theme`, `librsvg`,
   `breeze-gtk-theme` (cada um à parte, não fatal), GTK 3 `prefer-dark` e GTK 2 por
   `include` do gtkrc do Breeze ou Breeze-Dark em `/etc/gtk-2.0/gtkrc` (o XSETTINGS do
   host manda nome de tema vazio, que anularia um nome de tema no GTK 2).
2. `sudo eopkg install --yes-all --no-color <arquivo>`.

Funciona também em contêineres Solus que o MultiPak não criou (o script é copiado a
cada instalação).

### 2.7 No contêiner (`guest.py`)

As funções do Pisi Linux com o banco `/var/lib/eopkg/package`: estado e arquivos pelo
`metadata.xml`/`files.xml` instalados; `pisi_dependents` lê as `RuntimeDependencies`
de todos os pacotes instalados e recusa a remoção se algum depende do alvo; depois
`eopkg remove --no-color --yes-all`.

### 2.8 Atualização

`updates.SCRIPTS["solus"]` copia o script para `/usr/lib/multipak-solus-setup.sh` (o
Solus não tem `/usr/libexec`; `/usr/lib` aponta para `lib64`) e roda `--upgrade`:
`eopkg update-repo`, `eopkg upgrade --yes-all` e a preferência de tema.

### 2.9 Diferenças em relação ao uso normal

| Assunto | Solus de verdade | MultiPak |
| --- | --- | --- |
| Sistema | instalação completa | contêiner de imagem não oficial + camada local |
| Remover com dependentes | remove os dependentes junto | recusa |
| Delta | atualiza a versão instalada | recusado |
| Tema | Budgie/GNOME/Plasma | Breeze GTK claro ou escuro, como o host |

### 2.10 Onde olhar quando algo dá errado

- `~/.local/share/multipak/installations/<id>/installation.log`.
- Log do eopkg: `distrobox enter MultiPak-Solus-Linux -- cat /var/log/eopkg.log`.
- Refazer a imagem local: `podman rmi localhost/multipak-solus:base` (é recriada na
  próxima instalação).
