# Como o MultiPak instala pacotes do Arch Linux

*MultiPak 1.1.0.*

---

## 1. Explicação simples

### 1.1 O que é um pacote do Arch Linux?

O Arch Linux é uma distribuição de atualização contínua (*rolling release*). O
gerenciador de pacotes é o **pacman**, e um pacote é um arquivo TAR, normalmente
comprimido com zstd (`.pkg.tar.zst`), com os arquivos do programa e uma ficha
`.PKGINFO` (nome, versão, arquitetura, dependências).

Distribuições derivadas do Arch, como Manjaro, EndeavourOS e CachyOS, usam o mesmo
formato. A ficha não distingue o Arch delas.

### 1.2 O que o MultiPak faz?

1. **Lê sem executar:** lê só a ficha `.PKGINFO`. Nada é extraído e nenhum script roda.
   Pacotes de código-fonte são recusados: só pacotes já compilados.
2. **Cria (ou reaproveita) o contêiner** `MultiPak-Arch-Linux`, da imagem oficial
   `docker.io/library/archlinux`.
3. **Atualiza o contêiner antes de instalar:** no Arch, instalar um pacote sem
   atualizar o resto do sistema pode quebrar programas. Por isso o MultiPak atualiza o
   contêiner inteiro primeiro.
4. **Instala com o pacman**, que baixa as dependências dos repositórios oficiais.
5. **Atalho no menu.**

### 1.3 O contêiner

- Um ambiente só, `arch-rolling` ("Arch Linux (rolling release)"), com o contêiner
  `MultiPak-Arch-Linux`.
- O Distrobox conhece o pacman e prepara o contêiner sozinho.
- A sugestão tem confiança **Média**: a ficha mostra que o pacote é da família Arch,
  mas não se é do Arch ou de uma derivada.

### 1.4 Atualizar e remover

- **Atualizar**: em **Gerenciador → Atualizar**, o MultiPak atualiza o contêiner inteiro
  com o comando do próprio Distrobox (`distrobox upgrade`), que usa o pacman.
- **Versões novas**: para cada aplicativo, o Gerenciador compara a versão instalada com
  a mais nova dos repositórios. Para não deixar o contêiner num estado de atualização
  parcial, ele consulta uma cópia separada da lista de pacotes, sem tocar na do
  contêiner. Assim mostra cada um em **Com atualização**, **Em dia** ou **Sem fonte de
  atualização** (instalado de um arquivo que nenhum repositório oferece).
- **Remover**: ao desinstalar um aplicativo, o MultiPak remove o pacote dele com o
  `pacman -R`, que recusa a remoção se outro pacote depende dele. Dependências que o
  aplicativo trouxe não são removidas junto (regra geral do MultiPak).

### 1.5 O que (ainda) não funciona

- Pacotes de derivadas que dependem de pacotes que só a derivada tem (por exemplo, do
  repositório do Manjaro ou do CachyOS): o pacman não acha essas dependências no Arch.
- Pacotes do AUR em forma de código-fonte (`PKGBUILD`): só pacotes já compilados.
- Pacotes feitos para outra arquitetura: o MultiPak só aceita pacotes para a arquitetura
  do computador (como `x86_64`) ou para qualquer uma (`any`).

---

## 2. Explicação técnica

Caminhos de código são relativos a `src/multipak`.

### 2.1 Visão geral dos módulos

| Módulo | Papel |
| --- | --- |
| `metadata.py` (`_arch_fields`, `_tar_stream`) | descompressão e leitura do `.PKGINFO` |
| `models.py` (`ROLLING_FAMILIES`) | `arch-rolling`, `MultiPak-Arch-Linux` |
| `catalog.py` | imagem `docker.io/library/archlinux:latest` |
| `suggestions.py` | confiança Média e texto sobre as derivadas |
| `engine.py` (`_install_arch`) | `pacman -Syu` e `pacman -U` |
| `package_check.py` (`check_pacman`) | versões novas com um banco de dados separado |
| `guest.py` | estado (`pacman -Q`), arquivos e remoção |
| `assets/multipak-packages.xml` | MIME `application/x-archlinux-package` |

### 2.2 Leitura e detecção

`_tar_stream` reconhece zstd, gzip, xz e bzip2 pela marca inicial (*magic number*), ou
TAR sem compressão. O TAR é percorrido em modo *stream*, com limite de leitura, até o
`.PKGINFO`. `pkgname`, `pkgver` e `arch` são obrigatórios e únicos; `xdata =
pkgtype=src` (pacote de código-fonte) é recusado. Dependências exibidas: as linhas
`depend`. Na detecção, o Alpine e o Void são testados **antes** do Arch, porque também
são TAR comprimido; o Arch é o caso que sobra. Arquiteturas aceitas: a da máquina e
`any`. Dica `arch-rolling`, confiança **Média**.

### 2.3 Criação do contêiner

`distrobox create` com a imagem do Arch e `--additional-packages python` (no Arch, o
pacote do Python 3 se chama `python`). `ROLLING_FAMILIES` faz o contêiner aceitar o
os-release do Arch, que não tem `VERSION_ID`. Contêineres antigos chamados
`MultiPak-Arch-rolling` (1.0.0 Alpha 1) continuam reconhecidos.

### 2.4 Instalação (`engine._install_arch`)

1. `pacman -Syu --noconfirm`: atualizar só os índices sem atualizar os pacotes criaria
   uma atualização parcial, que o Arch não suporta.
2. `pacman -U --noconfirm <arquivo>`.

### 2.5 No contêiner (`guest.py`)

Estado: `pacman -Q`. Arquivos: `pacman -Qlq`; os `.desktop` encontrados viram atalhos.
Remoção: `pacman -R --noconfirm nome`, sem `-s` (as dependências ficam) e sem forçar.

### 2.6 Versões novas (`package_check.check_pacman`)

Como o `checkupdates`, usa um banco de dados temporário: liga o banco local
(`/var/lib/pacman/local`), roda `pacman -Sy --dbpath <temporário>` e depois
`pacman -Qu` e `pacman -Si` nele. O banco real do contêiner não é sincronizado, então a
próxima instalação não herda uma atualização parcial. A pasta temporária é apagada no
fim.

### 2.7 Diferenças em relação ao uso normal

| Assunto | Arch de verdade | MultiPak |
| --- | --- | --- |
| Instalar arquivo local | `pacman -U` (atualizar antes é por sua conta) | `pacman -Syu` e depois `pacman -U`, sem terminal |
| Ver atualizações | `checkupdates` | banco temporário, como o `checkupdates` |
| Remover | `pacman -Rs` leva as dependências órfãs | `pacman -R`, só o pacote |

### 2.8 Onde olhar quando algo dá errado

- `~/.local/share/multipak/installations/<id>/installation.log`.
- `distrobox enter MultiPak-Arch-Linux -- pacman -Qi <nome>`.
- Dependência não encontrada: confira se o pacote não é de uma derivada do Arch.
