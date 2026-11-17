# Como o MultiPak instala pacotes do Pisi Linux

*MultiPak 1.2.0.*

---

## 1. Explicação simples

### 1.1 O que é um pacote `.pisi`?

O Pisi Linux continua o Pardus, uma distribuição turca. O gerenciador de pacotes,
**pisi**, é escrito em Python (ainda Python 2.7). Um pacote `.pisi` é um ZIP com a
ficha `metadata.xml` (nome, versão, dependências, distribuição), a lista `files.xml` e
os arquivos do programa em `install.tar.xz`. O Solus usa o mesmo formato com outro nome
(`.eopkg`; ver `howsoluscompatibilityworks.pt-BR.md`).

### 1.2 O que o MultiPak faz?

1. **Lê sem executar:** só a ficha `metadata.xml`. Aceita pacotes do Pisi Linux na
   versão 1.2 do formato; a ficha diz a distribuição, seja qual for o nome do arquivo.
2. **Cria (ou reaproveita) o contêiner** `MultiPak-Pisi-Linux`, da imagem oficial
   `docker.io/pisilinux/chroot:base`.
3. **Prepara o contêiner:** o Distrobox não conhece o pisi. O MultiPak adiciona o
   repositório, atualiza a imagem e instala Python 3, sudo, dados de teclado, fontes e
   o idioma do sistema (se o Pisi Linux não tiver esse idioma, os aplicativos
   abrem em inglês).
4. **Instala com o pisi**, que baixa as dependências do repositório oficial.
5. **Atalho no menu.**

### 1.3 O contêiner

- Um ambiente só, `pisilinux-rolling` ("Pisi Linux (rolling release)").
- O pisi roda os scripts dos pacotes por um serviço (COMAR) que precisa do **D-Bus do
  sistema**. O Distrobox não compartilha o do computador, então o MultiPak liga um
  D-Bus próprio dentro do contêiner quando precisa.
- O repositório binário é `https://beta.pisilinux.org/pisi-index.xml.xz` (o mesmo que a
  imagem usa). Ele não é assinado: a confiança vem do HTTPS.

### 1.4 Atualizar

O `distrobox upgrade` não conhece o pisi. **Gerenciador → Atualizar** (o ícone de
qualquer aplicativo do contêiner, ou Atualizar tudo) roda o script do MultiPak: liga o
D-Bus, `pisi update-repo` e `pisi upgrade`.

**Versões novas**: o Gerenciador consulta `pisi list-upgrades` e `pisi info` para saber
quais aplicativos têm versão nova e mostra cada um em **Com atualização**, **Em dia** ou
**Sem fonte de atualização** (instalado de um arquivo que nenhum repositório oferece).
Ao abrir, usa a lista que o contêiner já tem, sem baixar nada; **Verificar
atualizações** antes roda `pisi update-repo`.

### 1.5 Remover

Ao desinstalar um aplicativo, o MultiPak remove o pacote dele com o `pisi remove`. O
pisi apagaria junto qualquer outro aplicativo que dependa desse pacote, então o MultiPak
confere antes: se outro aplicativo precisa dele, a desinstalação é recusada e nada é
apagado.

### 1.6 O que (ainda) não funciona

- Pacotes `.pisi` de outras distribuições, como o antigo Pardus: o MultiPak só aceita os
  do Pisi Linux e os do Solus.
- O repositório tem ciclos e conflitos de arquivo conhecidos; o preparo contorna esses
  problemas, mas um repositório quebrado pode impedir a instalação.

---

## 2. Explicação técnica

Caminhos de código são relativos a `src/multipak`.

### 2.1 Visão geral dos módulos

| Módulo | Papel |
| --- | --- |
| `pisi.py` | leitor de `metadata.xml` (Pisi Linux e Solus; recusa DOCTYPE, delta e formato ≠ 1.2) |
| `metadata.py` | ZIP (`PK\x03\x04`) → `pisi.read_info` → tipo `pisi` |
| `models.py` (`PISI_ENVIRONMENT`, `ROLLING_FAMILIES`) | `pisilinux-rolling`, `MultiPak-Pisi-Linux` |
| `engine.py` (`pisi_setup_hook`, `PISI_INSTALL`, `_install_pisi`) | hook e instalação |
| `assets/pisi-setup.sh` | pre-init hook e `--upgrade` |
| `guest.py` (`pisi_entry`, `pisi_files`, `pisi_dependents`, `pisi_bus`) | estado, arquivos, D-Bus, remoção |
| `container_progress.py` | linhas "Downloading/Installing N / M" → porcentagem |
| `updates.py` (`SCRIPTS["pisilinux"]`) | atualização |
| `assets/multipak-pisi.xml` | MIME `application/x-pisi-package` (`*.pisi`) |

### 2.2 Detecção e ambiente

Pacotes do Pisi Linux são ZIP, como os aplicativos Android. Um ZIP com extensão `.apk`
segue para o Android; os outros vão para `pisi.read_info`. *Distribution* "Pisi Linux" →
`pisi`; "Solus" → `eopkg`. Dica `pisilinux-rolling`, confiança **Alta**.
`ROLLING_FAMILIES` faz o contêiner aceitar qualquer `VERSION_ID` (o os-release diz
`ID=pisilinux`, `VERSION_ID=2.4.4`, que muda a cada atualização).

### 2.3 Pre-init hook (`pisi-setup.sh`)

Roda antes da checagem de gerenciador do Distrobox (que sairia com 127):

1. Sobe `dbus-daemon --system --fork`, apagando antes um socket/pid velho de um start
   anterior (ele recusaria conexões).
2. `pisi add-repo beta …` se faltar.
3. `pisi upgrade --ignore-dependency` (o repositório tem o ciclo `libutil-linux` →
   `python3`), depois `python3` sozinho, depois python3, sudo, util-linux, shadow,
   mesa, vulkan, xkeyboard-config, dejavu-fonts, glibc-locales-en e outros. Opções
   `--ignore-safety` (senão o pisi quer o `system.base` inteiro) e
   `--ignore-file-conflicts` (conflitos falsos de diretório, como
   `/usr/share/man/pt_BR/man5`).
4. Idioma do host (`/run/host/etc/locale.conf`): `glibc-locales-<idioma>` e `localedef`
   (sem isso o GTK cai no C).
5. `@includedir /etc/sudoers.d` no `/etc/sudoers` (o do Pisi Linux não lê essa pasta,
   onde o Distrobox libera o usuário).
6. `/.containersetupdone`, para o Distrobox pular a etapa de pacotes.

### 2.4 Instalação (`engine._install_pisi`)

1. `guest.py --pisi-bus` (sobe o D-Bus se não estiver no ar).
2. `pisi update-repo --no-color`.
3. `pisi install --no-color --yes-all --ignore-safety <arquivo>` (`PISI_INSTALL`). O
   pisi aceita o arquivo renomeado.

### 2.5 No contêiner (`guest.py`)

Banco `/var/lib/pisi/package/<nome>-<versão>/` (`metadata.xml`, `files.xml`).
`pisi_dependents` lê as `RuntimeDependencies` dos instalados; com dependentes, recusa;
senão `pisi_bus()` e `pisi remove --no-color --yes-all`.

### 2.6 Atualização

`updates.SCRIPTS["pisilinux"]` copia o script para
`/usr/libexec/multipak-pisi-setup.sh` e roda `--upgrade`: D-Bus, `pisi update-repo`,
`pisi upgrade` com as mesmas opções.

### 2.7 Diferenças em relação ao uso normal

| Assunto | Pisi Linux de verdade | MultiPak |
| --- | --- | --- |
| D-Bus | o do sistema | um próprio, no contêiner |
| Safety switch | ligado | `--ignore-safety` (como o build da imagem) |
| Remover com dependentes | remove junto | recusa |

### 2.8 Onde olhar quando algo dá errado

- `~/.local/share/multipak/installations/<id>/installation.log`.
- `distrobox enter MultiPak-Pisi-Linux -- pisi list-installed`.
- Erro de COMAR/D-Bus: `distrobox enter MultiPak-Pisi-Linux -- ls -l /run/dbus`.
