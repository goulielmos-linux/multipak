# Como o MultiPak roda snaps

*MultiPak 1.2.0.*

---

## 1. Explicação simples

### 1.1 O que é um snap?

Um snap é um aplicativo **empacotado com tudo de que precisa**: o programa, as
bibliotecas e até um "mini-Ubuntu" por baixo (chamado de **base**, como `core22` ou
`core24`). A ideia é funcionar igual em qualquer Linux.

Normalmente, quem instala e roda snaps é um programa do sistema chamado **snapd**.
Ele roda como administrador (root), fica sempre ligado e depende de recursos que nem
toda distribuição tem (como o AppArmor). Em sistemas atômicos, instalar o snapd é
difícil ou impossível.

### 1.2 O que o MultiPak faz?

O MultiPak faz o trabalho do snapd **sem o snapd e sem pedir senha de administrador**.

Pense no snap como uma caixa lacrada:

1. **Baixar:** o MultiPak busca a caixa na Snap Store oficial e confere se ela chegou
   inteira (compara uma "impressão digital" do arquivo com a que a loja publica).
2. **Abrir:** ele abre a caixa numa pasta sua (`~/.local/share/multipak`). A base, o
   "mini-Ubuntu", é baixada uma vez só e compartilhada entre todos os snaps que a usam.
3. **Rodar numa bolha:** quando você abre o aplicativo, o MultiPak monta uma **bolha**
   (um *sandbox*) com uma ferramenta chamada **bubblewrap**. Dentro da bolha, o
   aplicativo enxerga o mundo como se estivesse num Ubuntu com snapd, com a base dele,
   os arquivos dele e a sua pasta pessoal. Fora da bolha, nada do seu sistema é
   alterado.
4. **Atalho no menu:** o aplicativo aparece no menu como qualquer outro.

### 1.3 Snaps clássicos

A maioria dos snaps roda presa à bolha descrita acima. Alguns, chamados **clássicos**,
são publicados para rodar **sem isolamento**, como um programa instalado normalmente:
enxergam o sistema inteiro. É o caso de editores de código e ferramentas de
desenvolvimento (VS Code, IntelliJ, `go`, `node`), que precisam usar os compiladores e
os arquivos do computador.

Um snap clássico espera encontrar os próprios arquivos numa pasta `/snap`, na raiz do
sistema. Em sistemas atômicos essa pasta não existe e não pode ser criada, porque a raiz
é só leitura; por isso esses snaps não rodam lá nem com o snapd. O MultiPak contorna
isso com o bubblewrap: para o programa, ele mostra o computador inteiro com uma pasta
`/snap` a mais, onde estão os arquivos do snap. Essa pasta só existe na visão do
programa; no computador de verdade, nada é criado nem alterado.

Como não têm isolamento, os comandos de administrador desses snaps não rodam pelo
MultiPak.

### 1.4 E os "serviços"?

Nem todo snap é um aplicativo com janela. Alguns são **servidores** que ficam rodando
em segundo plano, sem janela. O Nextcloud, por exemplo, tem um banco de dados, um
servidor web e outros programas.

O MultiPak roda esses serviços pelo **systemd do seu usuário** (o mesmo mecanismo que
o seu desktop usa para os próprios programas de fundo), cada um na sua bolha. Ele:

- inicia os serviços depois de instalar;
- reinicia um serviço que travou;
- para tudo em cerca de um segundo quando você pede;
- inicia de novo quando você entra na sessão depois de religar o computador.

Você pode ver, iniciar e parar os serviços em **Gerenciador → Serviços Snap → Serviços
em segundo plano**: cada serviço tem o botão de iniciar ou parar e o de ver o registro.

### 1.5 "Root" sem ser root

Os scripts de instalação dos snaps e muitos serviços acham que estão rodando como
administrador (root). O MultiPak deixa que eles **pensem** que são root, mas só
**dentro da bolha**. Para o computador, continuam rodando com o seu usuário, sem
nenhum poder extra.

O mesmo truque permite rodar **comandos de administrador** de um snap, como o `occ`
do Nextcloud: **Gerenciador → Serviços Snap → Comandos de administrador**, com um aviso
antes. O comando é root só dentro da bolha daquele snap. Aplicativos com janela (os que
têm atalho no menu, como o Firefox) não aparecem nessa lista e não rodam como
administrador: como root, eles deixariam nos dados do snap arquivos que só o root
consegue alterar.

### 1.6 Atualizar e voltar atrás

O MultiPak verifica se há versões novas na loja. Ao atualizar um snap com serviços,
ele mantém os serviços no ar enquanto baixa e prepara tudo e só os para por alguns
segundos na troca. Se a versão nova falhar ao se configurar, ele **volta sozinho**
para a anterior. Também dá para pedir "Voltar à versão anterior", em **Gerenciador →
Atualizar**, com o snap selecionado.

### 1.7 É uma "camada de tradução"?

Não exatamente. O nome mais certo é **camada de compatibilidade**: para os snaps, o
MultiPak monta por conta própria um ambiente parecido com o do snapd, o suficiente para
eles rodarem.

- Uma camada de tradução, como o **Wine**, converte as chamadas de um programa feito
  para outro sistema (Windows) em chamadas que o Linux entende. Ela "traduz" o
  programa enquanto ele roda.
- Um snap **já é um programa Linux**. Os binários rodam direto no kernel, na mesma
  velocidade, sem nada no meio. O que falta sem o snapd é o **ambiente** em volta: a
  base montada como raiz, as pastas `/snap/...` e `/var/snap/...`, as variáveis
  `SNAP_*`, o `/tmp` próprio, quem responde ao `snapctl`, quem cuida dos serviços.
  O MultiPak **monta um ambiente parecido** com ferramentas comuns (bubblewrap e o
  systemd do usuário).

Há só um ponto em que existe algo parecido com tradução: quando o snap faz um pedido
ao snapd (pelo `snapctl`, como "guarde esta configuração" ou "reinicie o serviço
X"), o MultiPak atende no lugar dele e converte o pedido no que tem: um arquivo de
configuração, um comando do systemd do usuário, uma janela no desktop.

### 1.8 O que (ainda) não funciona

- Snaps que pedem **permissões de sistema** (por exemplo, controlar a rede do
  computador): o MultiPak recusa, de propósito.
- Serviços que precisam das **portas abaixo de 1024** (80, 443, 53): sem root de
  verdade, não dá. Normalmente dá para trocar a porta.
- Os serviços sobem quando você **entra na sessão**, não quando o computador liga.
- Serviços iniciados pelo barramento D-Bus do sistema, como os dos snaps `bluez`,
  `network-manager` e `udisks2`. São snaps feitos sobretudo para o Ubuntu Core, a
  versão do Ubuntu para aparelhos; num desktop, essas funções já vêm do próprio sistema.

---

## 2. Explicação técnica

Caminhos de código são relativos a `src/multipak`.

### 2.1 Visão geral dos módulos

| Módulo | Papel |
| --- | --- |
| `snap_store.py` | API pública e anônima da Snap Store; download conferido por SHA3-384 |
| `snap_yaml.py`, `snap_meta.py` | leitura estrita do `meta/snap.yaml` e regras de recusa |
| `snap.py` | instalação, ganchos, lançamento de apps e serviços, desinstalação |
| `snap_sandbox.py` | monta os argumentos do bubblewrap (a "visão" que o snapd daria) |
| `snap_ctl.py` | `snapctl` sem snapd: cliente em shell dentro da bolha + servidor no MultiPak |
| `snap_daemons.py` | serviços como unidades transitórias do `systemd --user` |
| `snap_timer.py` | converte os timers do snapd em eventos de calendário do systemd |
| `snap_refresh.py`, `snap_updates.py`, `snap_runtime_updates.py` | atualização e reversão |
| `snap_components.py` | componentes opcionais (snapd 2.67+) |
| `snap_dialog.py`, `snap_open.py` | `zenity` e `xdg-open` atendidos no desktop do usuário |
| `snap_launcher.py` | o comando `multipak-snap` (apps, serviços, `--set`, `--admin`) |

### 2.2 Instalação

1. **Download** (`snap_store`): o MultiPak usa a API pública da Snap Store, sem conta
   nem login. A revisão baixada é comparada com o SHA3-384 que a loja publica para ela
   antes de ser usada.
2. **Leitura e recusa** (`snap_meta.refusal`): o snap é recusado se for de sistema
   (`kernel`, `gadget`…), de outra arquitetura, se pedir **interfaces de sistema**
   (`network-control`, `snapd-control`…), usuários de sistema desconhecidos, arquivos
   de sistema que o próprio snapd não aceitaria, ou se só tiver serviços que o
   MultiPak não sabe rodar.
3. **Bases e conteúdo** (`ensure_runtime`): a base e os snaps de conteúdo
   (`content` plugs, como `gnome-42-2204`) são extraídos uma vez em
   `~/.local/share/multipak/snap-runtime/<nome>/<revisão>` e compartilhados. Uma pasta
   incompleta no lugar (sem `meta/snap.yaml`) é tirada do caminho antes.
   `collect_runtime` apaga o que nenhum snap usa mais.
4. **Extração** do próprio snap em `snaps/<id>/root`.
5. **Ganchos** `install` e `configure` (`run_hook`), como root na bolha (2.4).
6. **Registro** em `installations/<id>/manifest.json`, atalhos `.desktop`, serviços
   de D-Bus de sessão, autostart dos serviços e início dos serviços habilitados.

Pastas de um snap instalado:

```
~/.local/share/multipak/
├── installations/<id>/manifest.json      registro (revisão, canal, avisos…)
├── snap-runtime/<base>/<rev>/            bases e conteúdo compartilhados
└── snaps/<id>/
    ├── root/            /snap/<nome>/<rev>   (somente leitura)
    ├── data/<rev>/      $SNAP_DATA           /var/snap/<nome>/<rev>
    ├── data/common/     $SNAP_COMMON         /var/snap/<nome>/common
    ├── root-home/snap/  /root/snap           (HOME de ganchos e serviços)
    ├── state/config.json                      o que "snapctl set" guardou
    ├── accounts/        passwd/group com os usuários de sistema do snap
    ├── tools/           cliente snapctl
    └── channels/        canais de pedido de snapctl em uso
~/snap/<nome>/<rev>      $SNAP_USER_DATA (apps rodando como o usuário)
/tmp/multipak-private-tmp-<uid>/<id>/tmp   o /tmp do snap
```

### 2.3 A bolha (`snap_sandbox.build`)

Para snaps **strict** e **devmode**, o bubblewrap começa com `--tmpfs /` e monta:

- a **base** como raiz (`/usr`, `/bin`, `/lib`…), somente leitura;
- do computador, a lista do modo normal do `snap-confine`: `/home`, `/run`,
  `/var/tmp`, `/mnt`, `/media`, `/dev`, `/sys`, `/proc` próprio;
- um **`/etc` composto**: o da base, com entradas do computador que descrevem a máquina
  (`passwd`, `group`, `hosts`, `resolv.conf`, `localtime`, fontes, GPU…), como o Flatpak
  faz. Os usuários de sistema do snap entram num `passwd`/`group` próprio;
- `/snap/<nome>/<rev>` (e `current`) para o snap, a base e os provedores de conteúdo;
  `/var/snap/<nome>/{<rev>,common}` para os dados;
- os **layouts** do `snap.yaml` (`bind`, `bind-file`, `symlink`, `tmpfs`). Onde um
  layout pede um caminho que não existe num diretório somente leitura, o diretório vira
  uma cópia gravável cujas entradas são religadas ao original (o *writable mimic* do
  snapd);
- o **/tmp do snap**: uma pasta no `/tmp` do computador, igual para todos os apps,
  serviços e ganchos do mesmo snap (é por ela que o PHP do Nextcloud acha o
  `mysql.sock`) e invisível para outros snaps, que nunca veem o `/tmp` do computador.
  O computador a esvazia ao reiniciar, e a desinstalação a apaga;
- pastas que só existem para segurar montagens (`/snap/<nome>`) são criadas com
  `--dir` (0755). Criadas implicitamente pelo bubblewrap, ficariam com 0700, fechadas
  para os usuários de sistema do snap.

Snaps **classic** veem a raiz do computador (como no snapd) e o `/tmp` do computador.

O ambiente segue o do snapd (`SNAP`, `SNAP_DATA`, `SNAP_USER_DATA`, `SNAP_NAME`,
`PATH` com `$SNAP/usr/bin`…), seguido do `environment` do snap e do app, expandidos
na ordem.

### 2.4 Identidades: usuário, "root" e usuários de sistema

| Quem roda | Identidade dentro da bolha | No computador |
| --- | --- | --- |
| app normal | o usuário (uid 1000) | o usuário |
| serviço de usuário (`daemon-scope: user`) | o usuário | o usuário |
| gancho, serviço de sistema, app com `--admin` | root (uid 0) | o usuário |
| processo que troca para `snap_daemon` / `_daemon_` | 584788 / 584792 | um ID subordinado do usuário |

- **Root sem privilégio:** `bwrap --unshare-user --uid 0 --gid 0` cria um *user
  namespace* em que o usuário aparece como root. O bubblewrap tira todas as
  capacidades desse root (`CapEff=0`); ele só "é dono" dos arquivos do usuário.
- **Usuários de sistema** (`system-usernames`): o snap pode trocar para `snap_daemon`
  (por exemplo com `setpriv` ou um `drop` próprio). Para isso, `user_mapping` monta o
  namespace com `unshare --map-users/--map-groups` (via `newuidmap`/`newgidmap`, como o
  Podman sem root): root = o usuário, `snap_daemon` = o último ID subordinado de
  `/etc/subuid`, `_daemon_` = o penúltimo. O bubblewrap entra nesse namespace em vez de
  criar o seu (`bubble_command`). Arquivos gravados pelo `snap_daemon` aparecem no
  computador com um dono numérico alto (por exemplo, 589823) e são devolvidos ao usuário
  (`chown` dentro do namespace) antes de serem apagados.
- **Modo administrador** (`plan_launch(admin=True)`, `multipak-snap --admin`,
  "Comandos de administrador"): roda o comando do **app** com a identidade dos
  ganchos (HOME `/root/snap/<nome>/<rev>`, `USER=root`). Recusa snaps classic (sem
  isolamento não há "root só lá dentro") e serviços.

### 2.5 `snapctl` sem snapd (`snap_ctl.py`)

Dentro da bolha, `snapctl` é um script POSIX (a base pode não ter mais que shell e
coreutils). Ele grava o pedido numa pasta compartilhada (`/snap/.multipak/channel`) e
espera a resposta num *named pipe*. O MultiPak responde como o snapd onde existe
equivalente: `get`/`set`/`unset` (guardados em `state/config.json`), `services`,
`start`/`stop`/`restart` (dentro de um gancho, adiados até ele terminar, como no
snapd), `is-connected`, `user-open` (o `xdg-open` das bases novas abre no desktop) etc.
Quando não há equivalente, a resposta diz isso claramente.

Quem responde é um `snap_ctl.Server` (uma *thread*) dentro do processo do MultiPak que
abriu aquela bolha, enquanto ela existir, e cada bolha tem o seu canal
(`snaps/<id>/channels/<canal>`):

| Na bolha | Processo que atende o `snapctl` |
| --- | --- |
| app (inclusive com `--admin`) | `multipak-snap <id> <app>` |
| serviço | `multipak-snap --service <id> <serviço>` (iniciado pelo systemd) |
| gancho de `--set` | `multipak-snap --set` |
| gancho de instalação, atualização ou remoção | o assistente ou o gerenciador que faz a operação |

### 2.6 Serviços (`snap_daemons.py`, `snap.run_service`)

- Cada serviço vira uma **unidade transitória** do `systemd --user`
  (`multipak-snap-<id>-<serviço>.service`, criada com `systemd-run`), que executa
  `multipak-snap --service <id> <serviço>`. Propriedades vêm do `snap.yaml`:
  `Restart` (`restart-condition`), `RestartSec`, `TimeoutStopSec`, `After`/`Before`,
  `Type=oneshot`; limite de 5 partidas em 120 s.
- **Timers** viram `.timer` com `OnCalendar` (e `RandomizedDelaySec` para janelas
  como `9:00~11:00`).
- O lançador segura a trava do serviço (`services.lock`), mantém um servidor `snapctl`
  e roda a bolha com `--unshare-pid`: quando o serviço termina, nada que ele iniciou
  sobra. O script `SERVICE`, dentro da bolha:
  - sorteia o número inicial dos PIDs (`ns_last_pid`, ou consome alguns), para que um
    arquivo de PID velho não aponte para o novo processo depois de um `kill -9`;
  - inicia o comando e espera; em `forking`, espera também o que ele deixou rodando;
  - atende `stop`/`reload` por um *named pipe* (`stop-command`, `stop-mode`,
    `post-stop-command`).
- **Parada:** o systemd não alcança processos dentro do sandbox do Flatpak; o
  `ExecStop` chama `multipak-snap --services halt`, que pede ao lançador para parar a
  bolha. `Services.stop` para todas as unidades num único `systemctl stop` (só as
  carregadas), então elas param em paralelo.
- **Tubo de vida:** se o lançador Python morrer (mesmo com `kill -9`), um vigia no
  computador lê EOF num pipe e encerra o grupo inteiro.
- **`notify`, watchdog e sockets:** `NOTIFY_SOCKET` aponta para o canal do MultiPak,
  que repassa a prontidão; sockets (`sockets:`) são abertos pelo lançador e entregues
  como fd 3, 4… com `LISTEN_FDS`, ativando o serviço na primeira conexão.
- **Depois de religar:** unidades transitórias somem no reinício. O arquivo
  `~/.config/autostart/multipak-snap-<id>-services.desktop` roda
  `multipak-snap --services login <id>` quando a sessão começa, iniciando todos os
  serviços habilitados (os já ativos são pulados).
- Linha de comando: `multipak-snap --services list|start|stop|restart <id> [serviço…]`.

### 2.7 Atualização e reversão (`snap_refresh.py`)

1. Baixa e extrai a revisão nova num diretório de preparo (`.refresh`), com bases e
   conteúdo novos, **com os serviços no ar**.
2. Roda `pre-refresh` da revisão antiga. Se ele recusar, nada muda e os serviços não
   param.
3. Para os serviços, espera os processos terminarem, copia `$SNAP_DATA` para a
   revisão nova e troca a revisão.
4. Roda `post-refresh` da nova com os serviços parados (como o snapd). Se falhar,
   **desfaz a troca** e volta à revisão anterior.
5. Inicia os serviços. A revisão anterior fica guardada para "Voltar à versão anterior".

Na prática, o serviço fica parado só alguns segundos.

### 2.8 Desinstalação

Para os serviços (se algum não parar, nada é apagado), roda o gancho `remove`, devolve
ao usuário os arquivos dos usuários de sistema, apaga as pastas do snap, o `/tmp`
privado, atalhos, autostart e serviços de D-Bus, e recolhe bases que ficaram sem uso.

### 2.9 Diferenças em relação ao snapd

| Assunto | snapd | MultiPak |
| --- | --- | --- |
| Privilégio | daemon root | nenhum; tudo como o usuário |
| Isolamento | AppArmor + seccomp + namespaces | namespaces do bubblewrap (sem AppArmor/seccomp por interface) |
| Interfaces | conectadas e mediadas uma a uma | as de desktop comuns; as de sistema são recusadas |
| Serviços de sistema | root, no boot | root só na bolha, no login da sessão |
| Portas < 1024 | sim | não |
| Serviços ativados pelo D-Bus do sistema | sim | não |
| `/etc` | o do computador, com algumas entradas da base | o da base, com entradas do computador |
| `sudo <snap>.<app>` | sim | `multipak-snap --admin` / "Comandos de administrador" |
| Atualização automática | sim | o usuário verifica e atualiza no gerenciador |

### 2.10 Onde olhar quando algo dá errado

- Registro da instalação: `~/.local/share/multipak/installations/<id>/installation.log`.
- Saída dos serviços: `journalctl --user -u 'multipak-snap-<id>-*'`
  (ou o botão de registro do serviço, em **Gerenciador → Serviços Snap**).
- Estado: `multipak-snap --services list <id>`.
- Rodar um serviço em primeiro plano para ver o erro: `flatpak run
  --command=multipak-snap io.github.goulielmos_linux.multipak -- --service <id>
  <serviço>`.
