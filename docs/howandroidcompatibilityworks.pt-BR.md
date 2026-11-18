# Como o MultiPak integra os apps Android

*MultiPak 1.1.0.*

---

## 1. Em poucas palavras

Os apps Android rodam num **Waydroid adaptado** pelo MultiPak (base 1.6.3), instalado como
`/usr/local/bin/multipak-waydroid`. Cada contêiner Android tem o seu nome, as suas imagens, os seus apps e
dados, a sua rede e os seus atalhos. O Waydroid do sistema, se estiver instalado, continua separado e não é
alterado.

## 2. A rede de cada contêiner

- Cada contêiner tem uma ponte própria, `mpw<id>`, numa sub-rede própria `10.203.N.0/24` (o host fica com
  `10.203.N.1`).
- Um `dnsmasq` próprio entrega o endereço (DHCP) e responde ao DNS só nessa ponte.
- O tráfego para fora sai por NAT.
- **firewalld (Fedora, Bazzite e outros):** uma interface sem zona cai na zona padrão, que bloqueia o DHCP
  (porta 67) e o DNS (porta 53), e o Android fica sem internet. Por isso, enquanto o contêiner roda, o
  MultiPak põe a ponte na zona `trusted` e a tira quando a rede desce. A mudança vale só em tempo de execução,
  não fica gravada na configuração do firewall e não mexe na `waydroid0` do Waydroid do sistema. Se o
  firewalld for recarregado com o contêiner aberto, a ponte volta para a zona `trusted` na próxima partida.

## 3. Integração com o desktop

- **MultiPak Android Controller** (`io.github.goulielmos_linux.multipak.controller`, código em
  `android/controller`): o app do MultiPak dentro de cada contêiner, chamado de "app auxiliar" no resto
  deste texto. Faz as setas como gestos, leva os links ao Linux (seção 4) e a mídia aos controles do
  desktop. Não aparece na gaveta de apps. A sessão o instala ou atualiza quando o Android fica pronto.
  Antes ele se chamava `io.github.multipak.gestures`; num contêiner que ainda tem esse app, a sessão
  instala o novo, passa as setas para ele na acessibilidade e remove o antigo. O ID segue o prefixo do
  Flatpak do MultiPak (`io.github.goulielmos_linux`), o usuário do projeto no GitHub.
- **Ícone na barra de tarefas:** os atalhos dos apps gravam `StartupWMClass=waydroid.<pacote>`, o mesmo
  nome que a janela do app usa. Assim o painel mostra o ícone do app, e não um genérico.
- **Notificações:** apontam para o atalho do próprio app (`multipak-waydroid.<id>.<pacote>`), para o
  desktop mostrar o nome e o ícone certos.
- **Idioma e fuso:** quando o Android fica pronto, o MultiPak copia para ele o idioma do desktop (`LC_ALL`,
  `LC_MESSAGES` ou `LANG`) e o fuso (`TZ` ou `/etc/localtime`), só quando são diferentes. A mudança vale a
  partir da próxima partida.
- **Ligar ao entrar** (Gerenciador → contêiner → ⋮, desligado por padrão): grava
  `~/.config/autostart/multipak-android-<id>-ready.desktop`, que liga a sessão do Android em segundo plano
  quando o desktop inicia, com a mesma unidade que o Gerenciador usa (`multipak-android-session-<id>`).
  O serviço root do contêiner já sobe com o computador, então não há senha. Ocioso, o Android congela
  sozinho; abrir um app só o descongela, sem esperar o boot. Ocupa memória o tempo todo: o consumo aparece
  no cartão do contêiner. Desligar só apaga o arquivo; remover o contêiner também o apaga.
- **Android mais leve** (ideia do Lepton, da Valve): o `waydroid_base.prop` de cada contêiner ganha
  `debug.sf.nobootanimation=1` (sem animação de boot), `config.disable_networktime=true` (o desktop já
  cuida da hora) e `config.disable_otadexopt=true` (não há atualização OTA). Um arquivo vazio montado por
  cima de `/system/etc/init/perfetto.rc` impede os serviços de rastreamento de subir. A câmera e a seleção
  inteligente de texto continuam ligadas. O runtime só gera esses arquivos ao criar ou **atualizar** o
  contêiner: para um contêiner que já existe, use "Atualizar contêiner…". A chamada `reboot` já é negada
  pelo `waydroid.seccomp` do Waydroid.
- **Proteção do disco do computador** (defeito 2438 do Waydroid): o LineageOS 20 manda o ioctl
  `F2FS_IOC_SHUTDOWN` (`0x8004587d`, o mesmo número de `FS_IOC_SHUTDOWN` do ext4) ao desligar quando
  `/data` é f2fs. Como o `/data` do contêiner é uma pasta do computador montada por bind e o contêiner
  tem `sys_admin`, num computador com f2fs isso desligaria o sistema de arquivos do próprio computador.
  O `waydroid.seccomp` nega esse ioctl (`EPERM`), com máscara de 32 bits no número. Vale para
  contêineres novos e para os que passam por "Atualizar contêiner…", que copia o seccomp de novo.
- **Volume:** o Android tem volume próprio, e o som passa por ele e depois pelo mixer do Linux. Na
  primeira vez que cada contêiner fica pronto, a sessão pede ao serviço root (`MediaVolumeMax`, só com
  comandos fixos `cmd media_session volume --stream 3`) para pôr a mídia no máximo. Assim o volume fica
  só por conta do Linux. O máximo vem da resposta do `--get` (Android 13); o Android 11 não responde ao
  `--get`, mas diz o intervalo quando recusa um `--set 999`, que não muda nada. A marca `media_volume` em `input-preferences.json` faz isso acontecer uma vez;
  se o usuário mudar o volume no Android depois, a escolha dele fica.
- **Volume de cada app:** cada app Android que está tocando aparece no mixer do desktop com o nome e o
  ícone do seu atalho, e o controle desse item muda o volume só daquele app. O item "Waydroid" continua
  lá, como volume geral do contêiner.
  - O Android mistura os apps num som só antes de entregá-lo ao Linux, então o item de cada app é um
    fluxo **mudo**: serve de controle, não carrega o som. Por isso o medidor de nível dele fica parado, e
    não dá para mandar um app para o fone e outro para a caixa.
  - Quem muda o volume dentro do Android é a classe `Volume` do app auxiliar. Ela não roda como parte do
    app: quando o Android fica pronto, a sessão pede ao serviço root `WatchVolumes()`, que a inicia como
    um processo à parte, com `app_process` e o APK instalado como caminho de classes (o caminho vem do
    comando fixo `cmd package path` e é conferido). Só um processo do próprio Android (root) enxerga os
    tocadores dos outros apps; o Android 11 nega isso até ao usuário `shell`. O app auxiliar continua um
    app comum, sem permissão especial. Quando o app auxiliar é atualizado, o Android só termina a troca
    depois que o instalador responde; por isso a sessão repete o pedido quando o Android avisa que o
    pacote mudou, e o serviço reinicia o processo se o APK mudou de lugar.
  - A classe avisa numa linha de texto quais apps tocam (`playing <pacote>…`) e recebe
    `volume <pacote> <0-100>`. O volume é um multiplicador nos tocadores do app
    (`AudioPlaybackConfiguration` → `PlayerProxy.setVolume`), reaplicado quando o app cria um tocador novo;
    não mexe no volume de mídia do Android. A curva é a do mixer do Linux (ao cubo).
  - O serviço root trata as linhas como não confiáveis: até 4096 bytes, até 32 apps, cada um com nome de
    pacote válido; fora disso a linha é ignorada, e uma linha sem fim encerra o processo. O sinal
    `AppsPlaying` não leva dados; a sessão busca a lista com `PlayingApps()` e manda o volume com
    `AppVolume(<pacote>, <0-100>)` (só o dono). O processo sai sozinho quando o serviço fecha a entrada
    dele ou o contêiner desliga.
  - A sessão cria cada item com `paplay` (silêncio de `/dev/zero`) e acompanha as mudanças com
    `pactl subscribe` e `pactl list sink-inputs`: precisa do pacote `pulseaudio-utils`, que também fala
    com o PipeWire. Sem ele, nada muda: fica só o item "Waydroid".
  - O item usa `application.id` igual ao atalho do app, e é por ele que o desktop **lembra o volume** de
    cada app entre uma vez e outra. O mixer do KDE só aceita ícone pelo nome, então, enquanto o app toca,
    a sessão põe um link para o ícone dele em `~/.local/share/icons/hicolor/96x96/apps/`.
  - Crédito (rascunho): a ideia de mudar o volume de cada app pelo tocador dele vem de apps de volume
    por app para Android, como o Volume++, o SoundMaster (ShizuTools), o VolumeManager e o AppMixer. Nenhum
    código deles foi usado: a classe `Volume` foi escrita do zero sobre as funções do próprio Android.
  - Limites: só aparecem apps com atalho (os sons do próprio Android seguem o volume geral); apps que
    dividem o mesmo usuário do Android não são separados; na primeira vez que um app toca depois de o
    Android ligar, o volume lembrado chega uma fração de segundo depois do som.
- **Câmera e microfone** (Gerenciador → contêiner → ⋮ → "Câmera e microfone…"): um diálogo com as duas
  escolhas abaixo e o botão "Reiniciar o Android".
- **Webcam**: escolhe a única webcam do Linux que o contêiner vê, sempre como `/dev/video0`. Opções:
  "Automática" (o padrão: a primeira webcam que funciona nesse Android), "Nenhuma câmera" ou uma webcam
  da lista. Vale **na próxima partida** do Android. Serve, por exemplo, para usar a webcam externa de um
  notebook: sem a escolha, o Android pega a primeira (em geral a interna) e os apps não deixam trocar.
  - O `config_nodes` não monta mais `/dev/video*`, e as linhas antigas (`/dev/videoN dev/videoN`) dos
    contêineres criados antes são tiradas na partida. A cada partida, o serviço root acha o nó escolhido e
    o põe no `config_session` como `/dev/videoN → dev/video0`. Assim o Android não vê o nó de metadados de
    uma webcam USB nem as webcams que não foram dadas a ele. Uma webcam ausente não é erro: o Android
    sobe sem câmera e a escolha continua guardada.
  - A escolha fica em `input-preferences.json` (chave `camera`), e a sessão a passa ao root em `Start`. O
    nome é o link de `/dev/v4l/by-id` (ou `by-path`), que não muda entre boots, ou `name:<nome>` quando o
    udev não cria link (webcams virtuais). O root só **compara** esse nome com as webcams que ele mesmo
    acha em `/dev/video*`; nunca o usa como caminho. Só passa um nó de caractere com major 81, aberto sem
    seguir links, cujo `VIDIOC_QUERYCAP` tem `VIDEO_CAPTURE` em `device_caps`. Só esse nó ganha
    permissão 666 (o Waydroid abria todos os `/dev/video*`), porque o serviço de câmera do Android roda
    com outro usuário.
  - Cada versão do Android lê um formato: o **Android 13** (provedor External Camera) só MJPEG; o
    **Android 11** (`camera.v4l2` legado) YUYV e uma saída por vez, então apps com várias saídas, como o
    app Câmera, podem não funcionar ("saídas" são os fluxos que um app pede da mesma câmera, como prévia e
    foto, não câmeras diferentes). A lista (`VIDIOC_ENUM_FMT`) mostra as webcams que não servem a esse Android
    desabilitadas. Uma câmera virtual parada (OBS com `exclusive_caps`) não aparece.
  - Testado só com webcams virtuais: os dois Androids recebem e abrem a câmera escolhida, mas sem imagem. No
    Android 13 o v4l2loopback em MJPEG anuncia um buffer grande demais (1228800 bytes para 640×480; o
    provedor aceita até 614400) e a sessão não configura. No Android 11 o app Câmera pede duas saídas.
    Falta conferir a imagem com uma webcam USB de verdade.

```bash
multipak-waydroid --instance <id> camera list
multipak-waydroid --instance <id> camera set <id>   # auto (sem id), none ou um id da lista
```

- **Microfone**: escolhe de qual microfone do Linux o
  contêiner grava. O padrão é "Padrão do Linux", o microfone padrão do sistema. Não é reserva: dois
  contêineres, e programas do Linux, podem gravar do mesmo microfone ao mesmo tempo.
  - O serviço de áudio do Android é um cliente PulseAudio e, a cada gravação, lê
    `/vendor/etc/pulse/client.conf`. O serviço root mantém esse arquivo na camada do contêiner
    (`overlay/vendor/etc/pulse/client.conf`), com `default-source = <nome>` ou só um comentário.
  - O arquivo precisa existir quando a camada é montada, então o serviço o grava a cada partida, com a
    escolha que a sessão lhe passa. A troca com o Android ligado (`SetMicrophone`, só o dono, sem senha)
    regrava o mesmo arquivo, sem trocá-lo por outro, e vale **a partir da próxima gravação**.
  - O arquivo nunca vem do usuário: o root o escreve a partir de um nome conferido (letras, números e
    `_ . : @ + -`, até 200 caracteres), porque o `client.conf` também aceita outro servidor de som e o
    contêiner é privilegiado. As pastas do caminho são abertas sem seguir links.
  - A escolha fica em `input-preferences.json`, do usuário. A lista vem do `pactl list sources`, sem os
    "Monitor de …". Um microfone escolhido que não está ligado aparece como "(desligado)" e o Linux usa
    o padrão enquanto isso.
  - Precisa das camadas do Waydroid (`mount_overlays`), que os contêineres do MultiPak já usam. No
    mixer do desktop o fluxo continua com o nome "Waydroid" em todos os contêineres.

```bash
multipak-waydroid --instance <id> microphone list
multipak-waydroid --instance <id> microphone set <nome>   # sem nome: padrão do Linux
```

- **Controle de mídia (MPRIS):** a música ou o vídeo que toca num app Android aparece nos controles de
  mídia do desktop (bandeja do KDE, tela de bloqueio) com título, artista e os botões tocar/pausar,
  próxima e anterior; as teclas de mídia do teclado também funcionam.
  - O app auxiliar tem um `MediaListener`, um ouvinte de notificações que nunca lê as notificações: o
    papel só serve para o Android liberar as sessões de mídia. Quando o Android fica pronto, a sessão pede
    ao serviço root `AllowMediaListener`, que roda o comando fixo `cmd notification allow_listener`.
  - O ouvinte segue a sessão de mídia mais recente (uma que esteja tocando tem preferência) e, quando algo
    muda, grava na caixa de saída `{"v":1,"media":{"package","state","title","artist"}}`, ou
    `{"v":1,"media":{}}` quando nada toca. Ele não repete o mesmo estado.
  - O serviço root confere o arquivo como os links (seção 4) e mais: só essas chaves, `state` igual a
    `playing`, `paused` ou `stopped`, pacote com nome válido, título e artista com até 256 caracteres e sem
    caracteres de controle. O estado não envelhece (vale até mudar) e não entra na cota de links. A sessão,
    depois de cada `TakeRequests()`, chama `MediaState()` (só o dono), que devolve o último estado conferido.
  - A sessão publica `org.mpris.MediaPlayer2.multipak_<id>` só enquanto algo toca ou está pausado. O nome e
    o ícone vêm do atalho do app (`DesktopEntry`). Os botões chamam `MediaCommand(<tecla>)`, que aceita só
    `play-pause`, `play`, `pause`, `stop`, `next` e `previous` e roda o comando fixo
    `input keyevent KEYCODE_MEDIA_<tecla>`: o Android entrega a tecla ao app que está tocando, como faria
    com um teclado. O `cmd media_session dispatch` não serve: sai com erro no Android 11.
  - O artista `<unknown>`, que o Android põe nos arquivos sem essa informação, não aparece.
  - Fora, para ser simples: capa do álbum, posição e "trazer a janela".

- **Teclado virtual:** fica no menu ⋮ do contêiner ("Mostrar teclado virtual"), desligado por padrão,
  porque vale para o contêiner inteiro (`Settings.Secure show_ime_with_hard_keyboard`). A escolha fica nas
  preferências do contêiner (`multipak-waydroid --instance <id> keyboard get|on|off`), é aplicada na hora
  se o Android estiver ligado e de novo a cada app aberto; uma mudança feita dentro do Android vale só até
  o próximo app abrir.

## 4. Links entre o Android e o Linux

**Do Android para o Linux.** O app auxiliar (MultiPak Android Controller, seção 3) passa a ser o
navegador padrão do Android.
- Um link `http`/`https` ou um endereço de e-mail (`mailto:`) aberto num app Android vai para o navegador
  ou o cliente de e-mail do Linux. O assunto e o texto que o app manda junto entram no endereço.
- Abas de login (Custom Tabs) ficam no Android, no navegador dele (o Jelly), para o app receber a resposta.
- **Exceção por app:** na janela de preferências do app, "Abrir os links deste aplicativo no Android" põe o
  pacote em `Settings.Secure multipak_links_android`. O app auxiliar confere quem abriu o link
  (`getReferrer()`) e, se for um app da lista, repassa o link ao navegador do Android. Um app pode informar
  outro nome, mas o pior caso é só os links dele ficarem no Android. Serve para logins que voltam ao app
  por `https`.
- Links de apps instalados (App Links) continuam abrindo no próprio app; quem decide é o Android.
- O app auxiliar não tem acesso à rede. Ele grava cada link num arquivo pequeno na sua pasta privada
  (`files/outbox`, no formato `{"v":1,"uri":"…"}`; o estado da mídia usa a mesma pasta, seção 3), e o
  serviço root do contêiner vigia essa pasta. Quando
  chega um arquivo, o serviço emite o sinal D-Bus `LinksWaiting`, sem dados. A sessão do usuário chama então
  `TakeRequests()`, que só o dono do contêiner pode chamar e que devolve os endereços validados e apaga os
  arquivos. A sessão abre cada um com o programa padrão do Linux.
- Como tudo nessa pasta vem do Android, o serviço root confere cada arquivo
  (`vendor/waydroid/multipak_outbox.py`):
  - cada pasta é aberta sem seguir links, a partir da anterior; uma pasta trocada por link é ignorada;
  - o arquivo precisa ser regular, ter até 8 KiB e um único link, e ter como dono o usuário Android do app
    auxiliar, lido do `packages.list`;
  - o conteúdo precisa ser JSON estrito, só com as chaves `v` (igual a 1) e `uri` (ou `v` e `media`, o
    estado da mídia, seção 3);
  - só passam `http`, `https` e `mailto`, até 4096 caracteres, sem espaços nem caracteres de controle;
  - links com mais de 2 minutos são apagados sem abrir (sobras de uma sessão que caiu);
  - contra inundação: cada chamada lê e apaga até 64 arquivos, abre no máximo 16 links a cada 30 segundos,
    e o sinal sai no máximo 4 vezes por segundo.
- O serviço lê os arquivos pelo lado do Linux, sem entrar no contêiner, então funciona mesmo com o
  contêiner congelado. Antes do primeiro link, a pasta ainda não existe; o primeiro link pode levar alguns
  segundos para abrir.
- Os links vêm **ligados por padrão**. Ligado, o app auxiliar vira o navegador padrão quando é instalado
  ou atualizado; se o usuário escolher outro navegador no Android depois disso, a escolha fica. Desligado,
  o app auxiliar repassa tudo ao navegador do Android. A escolha fica na configuração `multipak_links` do
  Android, gravada pela sessão sem root. Para ligar ou desligar:

```bash
multipak-waydroid --instance <id> links off
multipak-waydroid --instance <id> links on
```

**Do Linux para o Android.** Na instalação, o MultiPak lê os endereços próprios que o app declara, como
`meuapp://` (só os de atividades com `VIEW` e `BROWSABLE`; nunca `http`, `https`, `mailto`, `file` e
outros genéricos). Ele cria um atalho oculto, `multipak-<instalação>-links.desktop`, com
`MimeType=x-scheme-handler/meuapp;`, e atualiza os índices de atalhos (`update-desktop-database`, usado
pelos apps GTK e pelo Firefox, e `kbuildsycoca6`, do KDE). Clicar num `meuapp://…` no navegador do Linux
abre o app Android (`multipak-waydroid --instance <id> open-uri <pacote> <endereço>`). O `open-uri` aceita
só a ação `VIEW`, até 4096 caracteres, sem espaços nem caracteres de controle, e recusa `http`, `https`,
`mailto`, `file`, `content` e outros genéricos.
- O MultiPak registra os endereços de cada app. Com a pergunta desligada, ele não torna o app o padrão: se
  um programa do Linux também abrir o endereço, o navegador pergunta, ou o usuário escolhe em "Aplicativos
  padrão".
- **Pergunta** (Gerenciador → contêineres Android, caixa "Perguntar qual aplicativo usar quando um link
  puder abrir no Android ou no Linux", **ligada por padrão**, vale para todos os contêineres): navegadores
  baseados no Chrome não mostram a escolha. Ligada, o MultiPak procura os endereços dos atalhos
  `multipak-*-links.desktop` que têm mais de um programa (`gio mime x-scheme-handler/<esquema>`), como o
  mesmo app em dois contêineres ou um app Android e um programa do Linux, e vira o padrão **só desses** em
  `mimeapps.list` (`src/multipak/android_link_choice.py`). O padrão anterior fica em
  `~/.config/multipak/android-links.json` e volta quando a opção é desligada ou o conflito some.
  - Clicar num desses endereços abre o instalador do MultiPak (`multipak-installer %u`, o mesmo atalho do
    `snap:`), que mostra uma janela pequena "Abrir este endereço com:". O programa escolhido abre direto
    pelo `.desktop` dele (`Gio.DesktopAppInfo.launch_uris`, pelo `python3` do computador), nunca pelo
    `xdg-open`, para não voltar ao MultiPak. O `gio launch` não serve: ele reescreve endereços que não
    conhece (`meuapp://x?a=1` vira `meuapp://x/?a=1`), o que quebra logins. Se só sobrou um programa, ele
    abre sem perguntar.
  - Os conflitos são recalculados ao ligar a opção e ao instalar ou remover um app Android. Um programa do
    Linux instalado depois só entra no próximo recálculo.
- O atalho oculto é removido junto com o app.

**Links de sites para apps Android (`intent://` e `market://`).** Alguns sites usam esses endereços para
abrir um app Android ou a página dele na loja. Eles não pertencem a um app: o app vem dentro do próprio
endereço. Por isso cada contêiner tem **um único** atalho oculto para os dois,
`multipak-waydroid.<id>.links.desktop`, com `MimeType=x-scheme-handler/intent;x-scheme-handler/market;` e
`open-uri <endereço>`, sem pacote. A sessão do contêiner o cria quando o Android fica pronto, junto com os
atalhos dos apps, e só o regrava (e atualiza os índices) quando o conteúdo muda. Ele é apagado com o
contêiner, como os atalhos `multipak-waydroid.<id>.*.desktop`. Sem pacote, o `open-uri` aceita só esses
dois endereços.
- `intent://…#Intent;…;end`: o `open-uri` lê só o `package`, o `scheme` e o `S.browser_fallback_url`; ação,
  componente e extras nunca chegam ao Android. Se o app está instalado no contêiner (existe o atalho
  dele, então nem é preciso ligar o Android para saber), abre `<scheme>://…` no Android. Senão, abre o
  endereço reserva (`http`/`https`) no navegador do Linux; sem reserva, um endereço `https` abre no Linux,
  e um app ausente segue como `market://`.
- `market://details?id=<pacote>`: se houver uma loja instalada (Play Store, F-Droid ou Aurora), abre nela
  (o endereço é refeito só com o pacote, sem os outros parâmetros); senão, abre a página do app na Play
  Store, no navegador do Linux. Outros `market://`, como os de busca, são recusados.
- Com vários contêineres, cada um tem o seu atalho e o Linux usa um deles.

**Por que um atalho por contêiner, e não um por app: a lição do winemenubuilder.** O Wine tem o mesmo
problema de fazer programas de outro sistema parecerem nativos, e o `winemenubuilder` é a peça que cria os
atalhos e as associações dos programas do Windows. Ele ficou conhecido por "sequestrar" associações: o
Bloco de Notas do Wine aparecia para arquivos de texto e o Internet Explorer para links, e muita gente
desliga o `winemenubuilder` por isso. Se cada app Android declarasse `intent` e `market`, 20 apps virariam
20 entradas iguais no "Abrir com" e em "Aplicativos padrão", e o Linux escolheria uma ao acaso. As regras
que o MultiPak segue para não repetir o erro:
- registrar só os endereços específicos que o app declara (`meuapp://`), nunca genéricos (`http`,
  `https`, `mailto`, `file`);
- endereços que não pertencem a um app (`intent://`, `market://`) ficam num só atalho por contêiner;
- nunca escolher sozinho entre dois programas: o MultiPak só vira padrão dos endereços em conflito para
  mostrar a pergunta, e deixa de ser quando ela é desligada;
- limpar tudo ao desinstalar o app ou remover o contêiner.

## 5. Envio de arquivos

Arquivos vão e voltam entre o Linux e o Android, como no KDE Connect. Não há pastas compartilhadas
(abandonadas em 18/11/2026: o contêiner congela cerca de 69 s depois de partir sem app aberto, e as
varreduras do MediaProvider travavam com ele).

**Linux → Android.** O arquivo chega em `Download` do Android e aparece nos apps e nas galerias.

- **Dolphin:** botão direito num ou mais arquivos → "Enviar para o Android" → o contêiner. O progresso
  aparece no aviso de transferência do KDE, com Cancelar; ao terminar, o aviso diz que concluiu.
- **Arquivos (GNOME):** botão direito → Scripts → "Enviar para o Android" → o contêiner. Avisos simples no
  começo e no fim.
- **Gerenciador:** contêiner → ⋮ → "Enviar arquivos…" abre o seletor e depois uma janela com a barra
  ("Enviando X.", "Concluído", que fecha sozinha) e Cancelar.
- **Linha de comando:** `multipak-waydroid --instance <id> send ARQUIVO...` (`--progress` troca os avisos
  por linhas `START`/`PROGRESS`/`DONE`/`ERROR`, que o Gerenciador lê).
- Os menus são gravados quando o Android fica pronto, só para os gerenciadores de arquivos instalados:
  `~/.local/share/kio/servicemenus/multipak-waydroid.<id>.send.desktop` (um item por contêiner no mesmo
  submenu) e `~/.local/share/nautilus/scripts/Enviar para o Android/<nome do contêiner>`. Remover o
  contêiner apaga os dois.
- Com o Android desligado, o envio só avisa: "O Android está desligado. Inicie uma sessão para enviar
  arquivos." Congelado, ele é descongelado para receber e congelado de novo no fim.
- Só arquivos: pastas são recusadas com aviso. Um nome que já existe em `Download` ganha `(1)`, `(2)`…
- **Como funciona** (`vendor/waydroid/multipak_files.py`): o comando, como o usuário, abre o arquivo e um
  pipe, e passa só o pipe ao serviço root (`SendFile`, D-Bus `hst`, só para o dono). O root confere que é
  um pipe legível (nunca o reabre), confere o nome (sem `/`, sem caracteres de controle, até 255 bytes) e
  entrega o pipe a um `sh` dentro do Android, como o usuário `shell` (2000) com o grupo `media_rw` (1023;
  sem ele, a visão FUSE recusa a escrita, e o `lxc-attach` não dá grupos extras), que grava em `/mnt/user/0/emulated/0/Download/<nome>` pela visão FUSE do MediaProvider. Nome e tamanho vão
  como argumentos, nunca no texto do script. O script não substitui arquivos (`set -C`) e apaga o arquivo
  se o tamanho final não for o anunciado: é assim que Cancelar (o comando fecha o pipe antes do fim) não
  deixa metade de arquivo. Um envio por vez.
- O que o usuário `shell` grava não entra sozinho no MediaStore (como no `adb push`), então o root pede
  a varredura só daquele arquivo (`content call --uri content://media/ --method scan_file`, comando fixo,
  até 30 s). Se ela falhar, o arquivo já está lá e o envio vale; só demora a aparecer nas galerias.

**Android → Linux.** No Android, Compartilhar → "Linux" (o app auxiliar) manda o arquivo para a pasta
Downloads do Linux, com o aviso "Recebido do Android".

- O app auxiliar abre cada arquivo compartilhado (só endereços `content://`, para outro app não fazê-lo
  mandar os arquivos privados do próprio auxiliar), copia para `files/<n>.part` e renomeia para
  `files/sent/<ms>-<aleatório>-<nome>` quando termina. Texto compartilhado sem arquivo vira `Android.txt`.
  Até 16 arquivos esperando.
- O serviço root vigia `files/sent` e emite o mesmo `LinksWaiting` dos links; a sessão chama
  `TakeFiles()` (só o dono), que abre cada arquivo sem seguir links, exige arquivo regular com um único link
  e o usuário Android do auxiliar como dono, confere o nome, apaga o arquivo e devolve nome e arquivo
  aberto (D-Bus `a(sh)`). O resto da pasta é apagado.
- A sessão copia para a pasta Downloads do `xdg-user-dirs` com um nome novo (`O_EXCL`, sem seguir links;
  `(1)`, `(2)`… se o nome existir), cada arquivo numa thread, e recusa se sobrar menos de 1 GiB livre.

## 6. Diagnóstico

```bash
multipak-waydroid --instance <id> diagnose
```

Mostra, em JSON, o que as notificações e a área de transferência precisam (imagem com cliente de
notificações, servidor de notificações, versão do gbinder, `pyclip`) e o idioma e o fuso do desktop e do
Android.

A integração com o KDE (janelas do tamanho do app, camada B) foi guardada fora do repositório, na pasta
`integracao-kde`, e não faz parte da 1.1.0.
