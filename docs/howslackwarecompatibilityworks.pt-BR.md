# Como o MultiPak instala pacotes do Slackware

*MultiPak 1.1.0.*

---

## 1. Explicação simples

### 1.1 O que é um pacote do Slackware?

O Slackware é a distribuição Linux mais antiga ainda ativa. Um pacote é só um TAR
comprimido (`.txz`, `.tgz`, `.tar.xz` ou `.tar.gz`) com os arquivos do programa e uma
pasta `install/` com a descrição (`slack-desc`) e, às vezes, um script (`doinst.sh`).
O nome do arquivo tem uma forma fixa: `nome-versão-arquitetura-build.txz`.

O Slackware **não registra dependências**: um pacote não diz de quais outros pacotes
precisa para funcionar. Normalmente, cabe a quem instala descobrir e instalar esses
pacotes; no MultiPak, ele mesmo faz isso (veja o passo 4 abaixo).

### 1.2 O que o MultiPak faz?

1. **Reconhece sem executar:** o arquivo só é aceito se o nome tiver a forma
   `nome-versão-arch-build` e se `install/slack-desc` descrever esse mesmo pacote.
   Dois cliques abrem com o MultiPak só `.txz` e `.tgz` com nome de pacote; outros
   arquivos compactados continuam com o programa de costume.
2. **Escolhe o ambiente** entre dois, feitos da mesma imagem pequena
   (`docker.io/aclemons/slackware:15.0`):
   - **leve** (padrão, contêiner `MultiPak-Slackware-15.0`): só o que o aplicativo
     precisa;
   - **completo** (contêiner `MultiPak-Slackware-15.0-full`): também as séries de base,
     bibliotecas, rede e X11 do Slackware (cerca de 1,2 GB a mais para baixar, uns 6 GB
     no disco).
3. **Confia na chave do Slackware:** a imagem não traz a chave de assinatura, e sem
   ela o slackpkg recusa tudo. O MultiPak importa uma cópia da chave oficial antes do
   primeiro pacote.
4. **Instala com o `installpkg`** e procura as bibliotecas que faltam: roda `ldd` nos
   programas instalados, acha nas listas de arquivos do Slackware qual pacote oficial
   tem cada biblioteca e instala com o `slackpkg`, repetindo até não faltar nada.
   Acrescenta também dados que o `ldd` não vê (dados de teclado, fontes, ícones), sem
   os quais o GTK nem abre.
5. **Atalho no menu.** Se alguma biblioteca não foi achada, o MultiPak avisa e sugere
   o ambiente completo.

### 1.3 Atualizar e remover

- **Atualizar**: `distrobox upgrade` (o Distrobox conhece o slackpkg), em **Gerenciador
  → Atualizar**.
- **Versões novas**: para cada aplicativo, o Gerenciador compara a versão instalada com
  a mais nova da lista de pacotes do Slackware; uma correção publicada depois do
  lançamento (série `patches`) conta como a mais nova. Assim mostra cada um em **Com
  atualização**, **Em dia** ou **Sem fonte de atualização** (instalado de um arquivo que
  nenhum repositório oferece). Ao abrir, usa a lista que o contêiner já tem, sem baixar
  nada; **Verificar atualizações** antes roda `slackpkg update`.
- **Remover**: `removepkg`. Como o Slackware não registra dependências, só o pacote do
  aplicativo é removido; as bibliotecas que o MultiPak acrescentou ficam.

### 1.4 O que (ainda) não funciona

- Pacotes feitos para outras versões do Slackware (como a 14.2 ou a *-current*): o
  MultiPak só oferece o 15.0, a estável atual. Como o pacote não diz para qual versão
  foi feito, todos vão para o 15.0 e esses podem não funcionar.
- A imagem do contêiner não é oficial, porque o Slackware não publica uma. O MultiPak
  usa a de `aclemons`, que continua sendo atualizada; a que o Distrobox indica (de
  `vbatts`) não é atualizada desde 2023.
- Programas que precisam de uma biblioteca que o Slackware oficial não tem (por
  exemplo, uma que só existe no SlackBuilds.org, um repositório da comunidade): o
  MultiPak não acha essa biblioteca e avisa.

---

## 2. Explicação técnica

Caminhos de código são relativos a `src/multipak`.

### 2.1 Visão geral dos módulos

| Módulo | Papel |
| --- | --- |
| `slackware.py` | nome do arquivo, `slack-desc`, `slack-required`, `install_name` |
| `metadata.py` | detecção depois do Arch, do Puppy e do Gentoo |
| `models.py` (`FULL_VARIANT`, `FULL_LABEL`, ambientes `slackware-15.0[-full]`) | ambientes |
| `engine.py` (`slackware_key_hook`, `_install_slackware`) | chave e instalação |
| `guest.py` (`slackware_dependencies`, `missing_libraries`, `slackware_providers`) | ldd + filelists |
| `assets/slackware-gpg-key.asc` | chave pública oficial (EC56 49DA 401E 22AB FA67 36EF 6A44 63C0 4010 2233) |
| `assets/multipak-slackware.xml` | MIME para `.txz`/`.tgz` com nome de pacote |
| `kirigami/EnvironmentPage.qml`, `DependenciesDialog.qml` | escolha leve/completo e dependências |

### 2.2 Leitura

`slackware.is_candidate` exige compressão conhecida e nome `nome-versão-arch-build`.
`read_info` descomprime com limite até achar `install/slack-desc` (o `makepkg` ordena o
arquivo, então as entradas de `install/` ficam juntas) e confere que a descrição é do
mesmo pacote. `slack-required` (de repositórios de terceiros) é lido se existir:
alternativas e limites de versão são descartados. Arquiteturas aceitas: a da máquina,
`noarch` e `fw` (firmware). Dica `slackware-15.0`, confiança **Média** (o pacote não diz
a release).

### 2.3 Criação do contêiner

`slackware_key_hook` grava a chave na home do contêiner e a importa no chaveiro da home
(usado pelo setup do Distrobox) e no de root (usado depois pelo `sudo slackpkg`). O
Slackware não tem `/usr/lib/os-release`; `read_os_release` cai para `/etc/os-release`.
O ambiente completo recebe o rótulo `FULL_LABEL`. O Python do Slackware 15.0 é 3.9:
`guest.py` usa `from __future__ import annotations`.

### 2.4 Instalação (`engine._install_slackware`)

1. `slackpkg -batch=on -default_answer=y update`.
2. `installpkg <arquivo>` — o `installpkg` exige o nome original, por isso a cópia usa
   `Transaction.package_file` (`install_name`).
3. `guest.py --slackware-dependencies light|full <nome> [slack-required…]`:
   - `GRAPHICAL_DATA` (xkeyboard-config, dejavu-fonts-ttf, hicolor/adwaita icon theme,
     gsettings-desktop-schemas, shared-mime-info, dconf) e `slack-required`;
   - no completo, as séries `a ap l n x xap` sem kernel e carregadores de boot;
   - até 10 rodadas: `ldd` em até 400 ELF do pacote (`=> not found`), procura nas
     `*-filelist.gz` do slackpkg (séries patches, slackware64, extra, pasture), instala
     os provedores. A lista é calculada antes porque o `slackpkg install` sai com 20
     quando não há nada a instalar.
4. Bibliotecas não achadas → aviso para usar o ambiente completo.

### 2.5 No contêiner (`guest.py`)

`/var/log/packages/<nome>-<versão>-<arch>-<build>` para estado e arquivos. Versões
novas: compara `/var/log/packages` com `/var/lib/slackpkg/pkglist`. Remoção:
`/sbin/removepkg <nome>` (antes, o `puninstall.sh` de um `.pet` convertido, se houver).

### 2.6 Diferenças em relação ao uso normal

| Assunto | Slackware de verdade | MultiPak |
| --- | --- | --- |
| Dependências | por conta do usuário | ldd + listas de arquivos oficiais |
| Chave GPG | `slackpkg update gpg` | cópia embutida, importada no hook |
| Sistema | instalação completa | leve (só o necessário) ou completo |

### 2.7 Onde olhar quando algo dá errado

- `~/.local/share/multipak/installations/<id>/installation.log` (pacotes adicionados e
  bibliotecas faltando).
- `distrobox enter MultiPak-Slackware-15.0 -- ldd /usr/bin/<programa> | grep 'not
  found'`.
- Se faltar algo, reinstale no ambiente completo.
