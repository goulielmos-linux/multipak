# Como o MultiPak roda AppImages

*MultiPak 1.1.0.*

---

## 1. Explicação simples

### 1.1 O que é um AppImage?

Um AppImage é um aplicativo **num arquivo só**. Por dentro, ele é um pequeno programa
de partida (o *runtime*) grudado a uma pasta compactada com o aplicativo, as
bibliotecas que o autor decidiu incluir, um ícone e um arquivo `.desktop` (nome,
ícone, comando).

O jeito "normal" de usar é baixar, marcar como executável e dar dois cliques. Isso
tem três problemas conhecidos:

- o arquivo não aparece no menu de aplicativos, nem tem ícone;
- o runtime precisa do **FUSE** para montar a pasta compactada, o que nem toda
  distribuição traz pronto;
- o AppImage **promete** levar tudo de que precisa, mas muitas vezes depende de
  bibliotecas ou módulos Python que espera encontrar no sistema. Se o sistema não
  tem, o aplicativo não abre, e o usuário não sabe o que falta.

### 1.2 O que o MultiPak faz?

Quando você abre um AppImage com o MultiPak:

1. **Lê sem executar:** o MultiPak lê o arquivo como um pacote, por fora, e tira de
   lá nome, versão, ícone e atalho. Ele **nunca roda** o programa de partida do
   AppImage para descobrir essas coisas.
2. **Confere o que falta:** ele olha, também sem executar nada, quais bibliotecas e
   módulos Python o aplicativo espera do sistema e pergunta ao seu sistema se eles
   existem.
3. **Guarda uma cópia:** o arquivo original fica onde está; o MultiPak guarda uma
   cópia numa pasta dele (`~/.local/share/multipak/appimages`).
4. **Abre a caixa uma vez:** a cópia é extraída para uma pasta. Assim o aplicativo
   roda direto da pasta, **sem precisar de FUSE**.
5. **Atalho no menu:** o aplicativo aparece no menu com o ícone dele, inclusive as
   ações extras do atalho (como "Nova janela").

Se nada faltar, é só isso: o aplicativo roda direto no seu sistema, sem contêiner.

> **"No sistema" não quer dizer rpm-ostree.** Neste documento, "rodar no sistema"
> significa só que o aplicativo abre fora de qualquer contêiner, a partir da pasta
> extraída em `~/.local/share/multipak/appimages`, usando as bibliotecas que o seu
> sistema já tem. Nada é instalado na imagem do sistema: o MultiPak não usa
> rpm-ostree para AppImages e não precisa reiniciar o computador.

### 1.3 Quando faltam dependências: o contêiner de AppImages

Se a conferência do passo 2 achar algo **obrigatório** que o sistema não tem, o
instalador avisa ("Este AppImage não inclui todas as dependências de que precisa",
com um link para ver quais) e mostra duas opções:

- **"Instalar o aplicativo e suas dependências (recomendado)":** é a que já vem
  escolhida;
- **"Instalar só o aplicativo"**.

Na opção recomendada, entra em cena **um contêiner**. Mas o AppImage em si **não é
instalado dentro dele**: a cópia e a pasta extraída continuam na pasta do MultiPak
(`~/.local/share/multipak/appimages`), como em 1.2. O que vai para o contêiner são só
as **dependências que faltam**. Quando você abre o aplicativo, ele **roda dentro do
contêiner**: os arquivos do programa são os da pasta do MultiPak (o contêiner enxerga a
sua pasta pessoal), mas o processo roda lá dentro e usa as bibliotecas do contêiner.

- é **um único contêiner Fedora**, chamado `MultiPak-AppImages`, compartilhado por
  todos os AppImages que precisarem dele. Ele é criado na primeira vez e depois
  reaproveitado;
- o MultiPak instala nele as dependências que faltam, com o gerenciador de pacotes do
  Fedora. O Fedora acha o pacote certo a partir do nome de uma biblioteca, então isso é
  automático;
- se um módulo Python não existir nos pacotes do Fedora, o MultiPak tenta buscá-lo no
  **PyPI** (o repositório oficial do Python), com cuidado (veja 2.7);
- o aplicativo usa a **sua pasta pessoal** de sempre, mesmo rodando no contêiner;
- o seu sistema não é alterado: as dependências instaladas ficam dentro do
  contêiner.

Se você escolher "Instalar só o aplicativo", ele fica fora do contêiner (na pasta do
MultiPak, como em 1.2; sem rpm-ostree). Nesse caso o MultiPak **não instala nada do que
falta**: o aplicativo provavelmente não vai abrir até você mesmo buscar e instalar essas
dependências no sistema, por conta própria (em sistemas atômicos baseados no Fedora,
isso pode significar usar o rpm-ostree; essa decisão e esse passo ficam com você, o
MultiPak não faz isso). A lista do que falta aparece na tela antes da instalação.

### 1.4 O teste rápido numa cópia isolada

Sempre que o AppImage passa a usar o contêiner, o MultiPak também **testa** o
aplicativo antes de terminar a instalação (a tela de confirmação avisa disso; não há
opção para pular o teste). Ele o abre por cerca de **15 segundos** numa **cópia
descartável** do contêiner, **sem internet** e **sem acesso aos seus arquivos**, com uma
tela virtual. Se o aplicativo reclamar de uma biblioteca ou módulo que falta, o MultiPak
instala e testa de novo (até 3 vezes). Depois a cópia é apagada.

O teste não prova que tudo funciona: alguns aplicativos não conseguem abrir nessas
condições. Quando ele não consegue confirmar, o MultiPak avisa, mas a instalação
continua valendo.

### 1.5 Atualizar

Em **Gerenciador → Atualizar**, o MultiPak procura versões novas quando você clica em
**Verificar atualizações**.
Ele descobre onde procurar pela informação que vem dentro do próprio AppImage, pela
página do projeto citada nos dados do aplicativo ou por um link que você informa
(GitHub, GitLab, Codeberg ou um link direto).

Ele não compara números de versão: há atualização quando **o arquivo publicado é
diferente do instalado**. Antes de trocar, ele baixa a versão nova, confere se o
arquivo chegou inteiro e se é **o mesmo aplicativo**, pede para fechar o aplicativo
se ele estiver aberto e só então troca. Se algo der errado no meio, **a versão antiga
volta** sozinha.

### 1.6 Consertar e desinstalar

Se alguém renomear ou apagar o contêiner `MultiPak-AppImages` por fora, os aplicativos
dele param de abrir. Nesse caso o atalho mostra uma notificação pedindo para abrir o
Gerenciador, e o Gerenciador oferece **Reparar**: retomar o contêiner renomeado,
recriá-lo e reinstalar as dependências, ou, se o sistema agora já tiver tudo, passar a
abrir o aplicativo fora do contêiner (a mesma pasta extraída de sempre; nada é
instalado no sistema).

Desinstalar apaga a cópia do AppImage, a pasta extraída e o atalho. **Seus dados
pessoais** (as configurações que o aplicativo gravou na sua pasta pessoal) ficam.
Se for o último AppImage que usava o contêiner, o MultiPak oferece apagar o contêiner
também.

### 1.7 O que (ainda) não funciona

- **Sem atualização por diferença (delta)**: o arquivo novo é baixado inteiro.
- Depois de uma atualização **bem-sucedida**, o arquivo antigo é apagado.
- AppImages que não estão no GitHub, GitLab ou Codeberg e não trazem informação de
  atualização precisam que você informe um link direto.
- Dependências que **nem o Fedora nem o PyPI** oferecem, e módulos Python que só
  existem como código a **compilar** (C, Rust etc.), não são instaladas: o MultiPak
  avisa quais ficaram faltando.
- O AppImage precisa ser da **mesma arquitetura** do computador e ter exatamente **um**
  arquivo `.desktop` na raiz.

---

## 2. Explicação técnica

Caminhos de código são relativos a `src/multipak`.

### 2.1 Visão geral dos módulos

| Módulo | Papel |
| --- | --- |
| `appimage.py` | leitor do formato (ELF + SquashFS/ISO), instalação, atalho, atualização com volta atrás, lançamento, limpeza |
| `appimage_dependencies.py` | análise estática de dependências (ELF `DT_NEEDED`, versão de glibc, imports Python) e sonda no sistema |
| `appimage_container.py` | contêiner Fedora compartilhado, instalação com DNF e PyPI, teste isolado, reparo |
| `appimage_updates.py` | fontes de atualização, consulta às APIs, cache, download conferido, detecção de cópias em uso |
| `appimage_launcher.py` | o comando `multipak-appimage` chamado pelos atalhos; avisa por notificação quando falha |
| `file_associations.py` + `assets/multipak-appimage.xml` | tipo MIME para abrir `.AppImage` com o MultiPak |
| `kirigami/live.py` | inspeção antes de instalar (`inspect_installation`, `appimage_dependencies`, `appimage_environment`) |
| `kirigami/main.py` | escolhas do assistente (`appimage_options`: contêiner sim/não, teste sim/não) |
| `kirigami/manager.py` | atualizações, reparo e exclusão de AppImages no Gerenciador |
| `containers.py` | regras comuns de contêiner: quem ainda usa, remoção conferida |

### 2.2 Leitura do arquivo, sem executar (`appimage.py`)

- **`elf_info`** lê o cabeçalho ELF e exige a assinatura `AI\x01` ou `AI\x02` no
  byte 8 (tipos 1 e 2 da especificação). A arquitetura do ELF (`e_machine`: x86_64,
  aarch64, i686, armv7l) tem de ser igual à do computador. No tipo 2, a carga
  SquashFS começa logo depois da tabela de seções do ELF; o código aceita até 64 KiB
  de preenchimento com zeros antes da assinatura `hsqs`.
- **`Archive`** escolhe a ferramenta: `unsquashfs -o <offset>` para o tipo 2; `7zz`,
  `7z` ou `bsdtar` para o tipo 1 (que é uma imagem ISO 9660). Ele lista (`names`,
  `listing` com tipo e tamanho), lê um membro (`read`, limitado), extrai alguns
  (`extract_paths`) ou tudo (`extract`). O `unsquashfs` roda com `-no-xattrs`,
  `-processors 2` e `-mem 64M`; o `bsdtar`, com `--no-same-owner
  --no-same-permissions`.
- **De onde vêm essas ferramentas:** elas são procuradas com `shutil.which` e rodam
  **dentro** do Flatpak (não estão em `host.HOST_TOOLS`), então o que está instalado no
  computador não importa. O `unsquashfs` é compilado no próprio Flatpak (módulo
  `squashfs-tools` do manifesto, em `/app/bin`) e o `bsdtar` vem do runtime
  `org.kde.Platform` 6.11 (`/usr/bin/bsdtar`; conferido no Flatpak instalado em
  novembro de 2026). O 7-Zip não vem em nenhum dos dois, por isso o tipo 1 é lido com
  o `bsdtar`. O usuário não precisa instalar nada. Se a ferramenta faltar (por
  exemplo, rodando o MultiPak fora do Flatpak), a leitura para com a mensagem "Could
  not find unsquashfs…" ou "Could not find 7-Zip or bsdtar…", que diz o que falta
  mas **não oferece instalar**.
- **`_member`** recusa nomes internos absolutos, com `..`, com `\`, com caracteres de
  controle ou começando com `-` (que virariam opções da ferramenta).
- **`inspect_appimage`** exige exatamente um `.desktop` na raiz, com
  `Type=Application`. Pega `Name` no idioma atual (ou o genérico), `X-AppImage-Version`
  ("Not provided" se faltar ou for inválida) e o ícone: candidatos `.svg`/`.png` com o
  nome do `Icon=` (preferindo SVG e `hicolor`), depois `.DirIcon`; o conteúdo precisa
  ser de fato PNG ou SVG. Limites: 1 MiB para metadados, 4 MiB para o ícone.

### 2.3 Análise de dependências (`appimage_dependencies.analyze`)

Feita antes da instalação (`kirigami/live.appimage_dependencies`) e de novo a cada
atualização. Uma falha na análise **nunca impede** a instalação: só é registrada.

1. Lista o AppImage e separa os candidatos: arquivos em `bin/`/`sbin/` ou com nome de
   biblioteca (`*.so`, `*.so.N…`), e, **só se o AppImage usa o Python do sistema**,
   os `.py`. "Usa o Python do sistema" = o `AppRun` é um script `#!` que cita
   `python`/`python3` e o AppImage não traz `usr/bin/python3*` próprio.
2. Respeita limites antes de extrair (quando a listagem informa tamanhos): até 400
   ELFs e 500 `.py`, 256 MiB por ELF, 2 MiB por `.py`, 768 MiB no total. Passou disso,
   o relatório sai marcado como **incompleto**.
3. Extrai **só** esses arquivos numa pasta temporária (`multipak-analysis-*`), lê um
   por vez e apaga logo em seguida. Links simbólicos e caminhos que saiam da pasta não
   são seguidos.
4. De cada ELF lê `DT_NEEDED` e a maior versão `GLIBC_x.y` exigida (seção
   `.gnu.version_r`). De cada `.py`, os `import` via `ast`: imports dentro de funções
   ou de `try/except` contam como **opcionais**.
5. Descarta o que o próprio AppImage traz (bibliotecas de mesmo nome, módulos `.py`
   e pacotes com `__init__.py`), componentes da própria glibc (`libc`, `libm`,
   `ld-linux`…) e a biblioteca padrão do Python, exceto o que as distribuições
   separam (`tkinter`, `turtle`, `idlelib` → `_tkinter`).
6. Roda uma **sonda** pequena e confiável com o `python3` do sistema (texto fixo em
   `PROBE`): `importlib.util.find_spec` para os módulos, `ldconfig -p` e pastas comuns
   de bibliotecas, e `os.confstr("CS_GNU_LIBC_VERSION")`. Sem resposta da sonda, nada
   é dado como faltando (exceto o interpretador, se o AppImage precisa dele) e o
   relatório fica incompleto.

O resultado (`DependencyReport`) tem:

- `missing`: o que o **sistema** não tem (decide se o contêiner é oferecido; só os não
  opcionais contam, em `required`);
- `requirements`: **tudo** o que o AppImage espera de fora, presente no sistema ou
  não. Como o contêiner é outro sistema, tudo isso é pedido lá
  (`container_packages`; o DNF pula o que já estiver instalado).

Cada item vira um nome que o DNF entende: bibliotecas como capacidade RPM
`libfoo.so.1()(64bit)`, módulos Python como `python3dist(nome)`, com uma tabela para
os casos em que o nome do pacote Fedora é outro (`yaml` → `python3-pyyaml`, `gi` →
`python3-gobject`, `PIL` → `python3-pillow`, `_tkinter` → `python3-tkinter`…). Uma
glibc nova demais é apontada, mas não tem pacote a instalar.

### 2.4 Instalação (`appimage.install` → `_complete`)

No assistente (`AppImagePage.qml`), se `report.required` não estiver vazio, aparecem
dois `MethodCard`: "Instalar o aplicativo e suas dependências (recomendado)" e
"Instalar só o aplicativo". `kirigami/main.set_dependency_report` deixa o primeiro
escolhido (`_appimage_container = True`) e põe `_appimage_verify = True`. Existe o
slot `setAppImageVerify`, mas **nenhuma tela o usa** hoje: na prática o teste isolado
sempre roda quando o contêiner é escolhido, e a página de confirmação
(`AppImageContainerPage.qml`) só informa isso. `appimage_options` só passa
`container=True` se houver dependências obrigatórias faltando **e** o primeiro cartão
continuar escolhido. Se o usuário escolher "só o aplicativo", a
instalação segue em modo `extracted` sem instalar nenhum pacote: o que faltava
continua faltando no sistema e cabe ao usuário instalar por conta própria (o reparo do
tipo `system`, em 2.9, só existe para AppImages que estavam no contêiner).

Etapas (com o progresso mostrado):

1. **5 %:** cria a transação (`installations/<id>/manifest.json`, `method="appimage"`),
   cria `appimages/<id>/` (0700) e **copia** o arquivo para `application.AppImage`
   (verificando espaço livre; cancelável). O original não é tocado. Calcula o SHA-256.
2. Lê a **cópia** de novo e confere nome, versão e arquitetura com o que foi mostrado
   ao usuário; se mudou, recusa ("o arquivo mudou").
3. **45–60 %:** `chmod 0755` e extração completa para `AppDir/`, que precisa ter um
   `AppRun` dentro dela (recebe permissão de execução).
4. **70–80 %** (só com contêiner) — `ensure_container`, depois `install_packages` com
   a lista do relatório (2.7). O que não se achou vai para `appimage_missing` e para
   os avisos; o que veio do PyPI também é avisado.
5. `appimage_mode` = `"container"` ou `"extracted"`.
6. **90 %:** atalho (`_desktop`, abaixo).
7. **95 %** (contêiner + teste) — `verify_and_complete` (2.7). Uma falha aqui vira
   aviso: **não desfaz** a instalação.
8. **100 %:** `status="installed"`.

Uma falha registra o erro na transação (`record_install_failure`). **Tentar de novo**
(`retry`) só reaproveita a cópia se o SHA-256 ainda for o gravado.

**O atalho** (`_desktop`) é o `.desktop` do próprio AppImage reescrito em
`~/.local/share/applications/multipak-<id>-appimage.desktop`:

- `Exec` passa a chamar `flatpak run --command=multipak-appimage
  io.github.goulielmos_linux.multipak -- <id> …` (fora do Flatpak:
  `python3 appimage_launcher.py <id> …`);
- um prefixo `env VAR=valor` do `Exec` original vira `--setenv VAR=valor`;
- `%f %F %u %U` são mantidos, `%i` vira `--icon <ícone>`, `%c` vira o nome, `%k` sai,
  e os outros argumentos são mantidos com as aspas certas;
- as seções `[Desktop Action …]` são preservadas; outras seções são removidas;
- `TryExec`, `Path`, `OnlyShowIn`, `NotShowIn` saem; `DBusActivatable=false`,
  `Hidden=false`, `NoDisplay=false`, `X-MultiPak-Method=appimage`;
- `Icon` aponta para `appimages/<id>/icon.svg|png` (ou `application-x-executable`).

`migrate_legacy_launchers` corrige atalhos antigos que chamavam o ID da versão Alpha
(`io.github.multipak.manager`).

### 2.5 Onde ficam os arquivos

```
~/.local/share/multipak/
├── installations/
│   ├── <id>/manifest.json            transação (modo, contêiner, pacotes, avisos, fonte…)
│   ├── <id>/installation.log         registro da operação
│   ├── appimage-updates.json         cache das verificações de atualização
│   └── environments/MultiPak-AppImages/container.json   registro do contêiner
└── appimages/<id>/
    ├── application.AppImage          cópia gerenciada (0755)
    ├── AppDir/                       o aplicativo extraído (AppRun, usr/…)
    ├── icon.svg | icon.png           ícone usado no atalho
    ├── update.AppImage               versão nova baixada, enquanto espera a troca
    └── previous.AppImage, previous.AppDir   cópia anterior, só durante uma atualização
~/.local/share/applications/multipak-<id>-appimage.desktop
```

Os dados que o aplicativo grava (`~/.config/…`, `~/.local/share/…`) ficam na pasta
pessoal do usuário, como em qualquer AppImage, também no modo contêiner (o contêiner
não tem pasta pessoal própria).

### 2.6 Lançamento (`appimage.launch`, `appimage_launcher.py`)

O atalho executa `multipak-appimage <id> [args]`, que acha a transação (precisa estar
`installed`) e confere que `application.AppImage` é um arquivo comum. Depois, conforme
`appimage_mode`:

| Modo | O que roda | Onde |
| --- | --- | --- |
| `extracted` | `AppDir/AppRun` (que precisa resolver para dentro de `AppDir`) | no sistema, via `flatpak-spawn --host` |
| `container` | `distrobox enter --name <contêiner> --no-tty --no-workdir -- env … AppDir/AppRun` | no contêiner, também chamado via `flatpak-spawn --host` |
| `direct` | o próprio `application.AppImage` (com o runtime e FUSE dele) | no sistema |

No modo `container`, nada do aplicativo é copiado para o contêiner: `AppDir/AppRun`
é o mesmo caminho em `~/.local/share/multipak/appimages/<id>/`, que o contêiner
enxerga porque o Distrobox foi criado sem `--home` próprio e monta a pasta pessoal do
usuário. O contêiner só fornece as bibliotecas e os módulos (o que o DNF e o pip
instalaram nele).

O modo `direct` é o valor padrão do campo em `models.py` e só existe para transações
antigas: a instalação atual grava sempre `extracted` ou `container`.

O ambiente recebe as variáveis da especificação: `APPIMAGE` (a cópia), `APPDIR`, `OWD`
(pasta atual) e `ARGV0`, mais as do `--setenv`. Dentro do Flatpak, só essas são
repassadas; o resto do ambiente é o do sistema (`host.command`).

No modo contêiner, antes de abrir, `podman container inspect --format {{.Id}}` confere
que o nome **ainda leva ao mesmo contêiner** (ID de 64 dígitos gravado). Se não leva,
o lançador levanta `RepairNeeded` e **não** deixa o Distrobox criar outro nem abre o
aplicativo num contêiner que tomou o nome. Qualquer erro do lançador vai para o
`stderr` e para uma **notificação** (portal `org.freedesktop.portal.Notification`
dentro do Flatpak, com `gdbus`/`notify-send` como alternativa), porque um atalho não
tem janela para mostrar erros.

### 2.7 O contêiner de AppImages (`appimage_container.py`)

**Criação e reaproveitamento (`ensure_container`):**

- `engine.preflight`: exige `distrobox`, `podman` (e `setsid` no Flatpak), Podman
  **sem root** e não remoto.
- Se há registro (`find_record`) e o contêiner ainda existe com o mesmo ID e os rótulos
  certos (`OWNER_LABEL` = dono gravado, `io.github.multipak.appimage-container=1`), é
  reaproveitado. Se foi **renomeado** por fora, é retomado (`podman rename`) e as
  transações são reapontadas (`_retarget`). Se o ID existe mas os rótulos mudaram, é
  recusado.
- Senão, cria: nome `MultiPak-AppImages`, ou `C2-MultiPak-AppImages`, `C3-…` se o nome
  estiver ocupado por outro contêiner; imagem `quay.io/fedora/fedora-toolbox:latest`
  (`podman pull --policy=missing`); `distrobox create --no-entry
  --additional-packages python3` com os rótulos de dono; **sem `--home`** próprio.
  Depois um `distrobox enter … true` para inicializar.
- O contêiner não é oferecido para instalar pacotes comuns, mas aparece na lista de
  atualizações do Gerenciador, na seção Componentes, como "Dependências de AppImages"
  (enquanto responder ao
  próprio nome).

**Instalação de pacotes (`install_packages`):**

1. Filtra nomes (`[A-Za-z0-9][A-Za-z0-9._+()-]{0,199}`) e confere a identidade do
   contêiner (`verify_name`).
2. `sudo -n dnf -y install --skip-unavailable --setopt=install_weak_deps=False -- …`
   (um nome que nenhum pacote fornece não derruba os outros), depois
   `rpm -q --whatprovides` para saber o que ficou faltando.
3. Para cada `python3dist(x)` que faltou, `install_from_pypi`:
   - garante o `pip` (instala `python3-pip` se preciso);
   - tenta um **wheel pronto** (`pip download --no-deps --only-binary=:all:`);
   - sem wheel, baixa o **código-fonte**, mas só segue se ele for **só Python**
     (nenhum `.c`, `.cpp`, `.pyx`, `.rs`, `.go`, `Cargo.toml`, `CMakeLists.txt`,
     `meson.build`… e contém o módulo pedido). As ferramentas de build declaradas no
     `pyproject.toml` (padrão `setuptools>=40.8.0` + `wheel`) são baixadas **só como
     wheels**. O build roda numa **cópia descartável** do contêiner (`podman commit`
     + `podman run --network=none --cap-drop=all --security-opt=no-new-privileges
     --read-only --userns=keep-id`, `/tmp` e `/run` em tmpfs, até 600 s por etapa),
     com até 3 rodadas perguntando ao backend (PEP 517
     `get_requires_for_build_wheel`) o que mais precisa; cada requisito é validado
     (nada de links, caminhos ou repositórios) e baixado como wheel. O resultado tem
     de ser um wheel `-none-any`;
   - em qualquer caminho, o wheel precisa **conter o módulo** que o aplicativo
     importa (lido da lista de arquivos do zip): um projeto de nome parecido não
     serve;
   - instala com `sudo -n python3 -m pip install --only-binary=:all:
     --break-system-packages` (vai para `/usr/local` do contêiner).
   - As pastas temporárias ficam em `installations/pypi/<uuid>` e são apagadas.

**Teste isolado (`isolated_check`, `verify_and_complete`):**

- Garante o Xvfb no contêiner (`xorg-x11-server-Xvfb`); sem ele, testa sem tela.
- `podman commit` do contêiner para `localhost/multipak-appimage-check:<id>` e
  `podman run --rm --network=none --cap-drop=all --security-opt=no-new-privileges
  --read-only --userns=keep-id`, `/tmp`, `/run` (e `/var/lib/xkb`) em tmpfs,
  `AppDir` montado **somente leitura** em `/opt/multipak-appimage`, `HOME=/tmp`,
  `QT_QPA_PLATFORM=offscreen`. Com Xvfb: `Xvfb :99 -screen 0 1280x800x24
  -nolisten tcp` e `timeout --signal=TERM --kill-after=5 15 AppRun`; o Podman tem
  um `--timeout` de reserva (15 + 15 s).
- Resultado: `running` (código 124: ainda rodava aos 15 s), `exited` (saiu com 0) ou
  `unknown`. Da saída, `startup_failures` reconhece **só** mensagens conhecidas:
  `error while loading shared libraries: X.so…`, `No module named 'X'`,
  `version 'GLIBC_x.y' not found`.
- Repete até 3 rodadas enquanto achar algo instalável. O que instalou é somado a
  `appimage_packages` (para um reparo reinstalar o mesmo num contêiner novo); o que
  não conseguiu vai para `appimage_missing`. Container e imagem temporários são
  removidos mesmo se a instalação for cancelada (`_cleanup_runner`).

### 2.8 Atualizações (`appimage_updates.py`, `kirigami/manager.py`)

**Onde procurar** (`check_one`), nesta ordem, usando a primeira que existir:

1. o link salvo para o aplicativo (`appimage_update_source`);
2. a **informação de atualização embutida** (seção ELF `.upd_info`, até 4 KiB):
   `zsync|https://…` e `gh-releases-zsync|dono|repo|tag|padrão` são aceitos; outros
   tipos são ignorados;
3. as páginas de projeto do **AppStream** (`usr/share/metainfo` ou `appdata`, URLs
   `vcs-browser`, `homepage` etc. que sejam GitHub/GitLab/Codeberg), usadas **só** se
   uma versão publicada lá tiver o mesmo SHA-256 ou tamanho da cópia instalada; então
   o link é salvo;
4. senão, o estado é `no-source` e o Gerenciador pede um link ao usuário
   (`check_link`). Esse link só é salvo quando o arquivo publicado é o instalado ou,
   numa atualização, depois que o download prova que é o mesmo aplicativo (senão:
   `LinkMismatch` e o link é pedido de novo).

**Fontes suportadas** (`Source.kind`): `github` (API REST, até 20 versões, sem
rascunhos nem pré-lançamentos quando a tag é `latest`), `gitlab` (gitlab.com),
`gitea` (codeberg.org), `zsync` (lê só o cabeçalho do `.zsync`: SHA-1, tamanho e URL
do arquivo) e `file` (link direto para um `.AppImage`). Só HTTPS, inclusive nos
redirecionamentos.

**Escolha do arquivo** (`choose`): termina em `.AppImage` (ou `.zsync`), casa com o
padrão, cita a arquitetura do computador (ou não cita nenhuma outra); empate é
decidido por citar a arquitetura, ter palavras do nome do aplicativo e nome mais curto.

**"Há atualização?"** (`is_current`): compara o SHA-256 publicado (GitHub informa
`digest`), senão o SHA-1 do zsync, senão o tamanho. **Números de versão nunca são
comparados.** Os resultados ficam em `installations/appimage-updates.json` e valem
**6 horas**; abrir a lista mostra só resultados recentes sem consultar a rede, e os
servidores só são consultados quando o usuário pede para verificar. Uma verificação
que falha mostra o último resultado, marcado como antigo.

**Download** (`download`): para `appimages/<id>/update.AppImage` (máx. 8 GiB,
cancelável), confere SHA-256/SHA-1 quando conhecidos, `chmod 0700`, lê como AppImage
e exige que seja **o mesmo aplicativo** (mesmo nome de `.desktop` ou mesmo `Name`).
Depois refaz a análise de dependências (`_with_dependencies`):

- se já roda no contêiner: pacotes novos que a versão nova pede e ainda não estão em
  `appimage_packages` são mostrados para confirmação;
- se roda no sistema e a versão nova tem dependências obrigatórias faltando: o
  Gerenciador propõe **passar para o contêiner** (`needs_container`).

**Aplicativo aberto:** `running` procura no `ps -eo pid=,args=` do sistema processos
cujo comando cita `appimages/<id>/AppDir/` ou a cópia. Se houver, o Gerenciador pede
para fechar; "Fechar" manda `SIGTERM` e espera até 10 s. Isso é checado antes do
download e de novo antes da troca.

**Troca** (`appimage.update`): move a cópia atual para `previous.AppImage` e a
`AppDir` para `previous.AppDir`, coloca a nova no lugar e roda `_complete` de novo
(extração, pacotes no contêiner, atalho, teste). **Qualquer falha ou cancelamento**
devolve a cópia, a pasta, os ícones, a transação e o atalho anteriores; a instalação
nunca é desfeita por uma atualização cancelada. Com sucesso, `previous.*` é apagado.

"Atualizar tudo" percorre contêineres, AppImages e snaps um de cada vez; um AppImage
que não fecha pode ser pulado.

### 2.9 Desinstalação, exclusão e reparo

**Desinstalar** (`appimage.cleanup`): apaga os atalhos (e `.tmp`), `appimages/<id>/`
e `installations/<id>/`. Se algo falhar, a transação fica `cleanup_failed` com o erro.
Para AppImages em contêiner, `containers.uninstall_application` verifica depois se
alguma outra transação ainda usa o contêiner (`_referenced`); se não, o Gerenciador
oferece removê-lo (`remove_container`, que confere ID, dono e registro, e preserva as
imagens base). AppImages fora do contêiner nunca o reivindicam.

**Detecção de quebra** (`needing_repair`, chamada em segundo plano por
`Manager.checkRepairs`): para cada AppImage em modo contêiner, compara o nome gravado
com os nomes que o `podman ps --all` mostra para o **ID** gravado. Se o Podman não
responde, nada é dado como quebrado.

**Plano de reparo** (`repair_plan`), mostrado ao usuário antes:

| Tipo | Situação | O que acontece |
| --- | --- | --- |
| `relink` | o contêiner já tem o nome certo de novo | só reaponta, sem perguntar |
| `system` | o sistema agora tem todas as dependências (análise completa, sem nada obrigatório faltando) | passa para `extracted`, sem contêiner |
| `renamed` | o mesmo contêiner, com outro nome | `podman rename` de volta (o nome padrão ou o menor `Cn-` livre) |
| `install` | o contêiner gravado sumiu, mas existe o contêiner de AppImages atual | instala lá os `appimage_packages` |
| `recreate` | não existe contêiner de AppImages | cria um novo e instala os pacotes |

Retomar um contêiner conserta todos os AppImages dele de uma vez. Na lista de reparo
também há **Excluir**, que apaga só a cópia e o atalho e deixa o contêiner como está.

### 2.10 Abrir `.AppImage` com o MultiPak (`file_associations.py`)

Se o usuário escolher o MultiPak para AppImages, é instalado o tipo MIME
`application/x-multipak-appimage`
(`~/.local/share/mime/packages/io.github.multipak.appimage.xml`): glob `*.appimage` e
marca inicial (*magic number*) `\177ELF` + `AI\001`/`AI\002` no byte 8, ambos com peso
80. Se o usuário desmarcar e o sistema já trouxer esse tipo globalmente, o MultiPak
grava um tipo local que o declara subclasse de `application/x-executable`, para os
AppImages voltarem a ser tratados como executáveis comuns.

### 2.11 Diferenças em relação ao uso "normal" de AppImage

| Assunto | `chmod +x` e rodar | MultiPak |
| --- | --- | --- |
| Precisa de FUSE | sim (o runtime monta a imagem) | não: roda da pasta extraída |
| Menu e ícone | não | sim, com ações do `.desktop` |
| Onde fica | onde você deixou | cópia em `~/.local/share/multipak/appimages`; original intocado |
| Dependências faltando | o aplicativo falha | detecta antes e oferece o contêiner Fedora com DNF/PyPI |
| Teste antes de usar | não | sempre que usa o contêiner: 15 s numa cópia sem rede nem arquivos |
| Atualizações | manual | sob pedido; `.upd_info`, AppStream ou link; arquivo inteiro, conferido por hash, com volta atrás se falhar |
| Isolamento em uso | nenhum | nenhum |

### 2.12 Onde olhar quando algo dá errado

- Registro da instalação: `~/.local/share/multipak/installations/<id>/installation.log`.
- Estado: `installations/<id>/manifest.json` — `appimage_mode`, `container`,
  `container_id`, `appimage_packages`, `appimage_missing`, `warnings`,
  `appimage_update_source`.
- Ver o erro de um atalho que não abre (sai no terminal):
  `flatpak run --command=multipak-appimage io.github.goulielmos_linux.multipak -- <id>`.
- O contêiner: `podman ps --all --filter label=io.github.multipak.appimage-container=1`
  e `distrobox enter MultiPak-AppImages` (ou o nome `Cn-` que ele recebeu) para
  testar `AppDir/AppRun` à mão.
- Atualizações: `installations/appimage-updates.json` (o que cada verificação achou)
  e o registro do Gerenciador ("Ver detalhes").
