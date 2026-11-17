# Como o MultiPak instala pacotes RPM (Fedora, Mageia, openSUSE e ALT Linux)

*MultiPak 1.1.0.*

---

## 1. Explicação simples

### 1.1 O que é um RPM?

RPM é o formato de pacote de várias distribuições: Fedora, Mageia, openSUSE, ALT
Linux, RHEL e derivadas. O formato é o mesmo, mas **cada distribuição tem as suas
bibliotecas e o seu gerenciador**: um RPM do openSUSE não costuma funcionar num Fedora.
Por isso não basta "saber instalar RPM": é preciso saber **de onde** ele veio.

### 1.2 O que o MultiPak faz?

1. **Lê o cabeçalho:** usa a ferramenta `rpm` que vem com o MultiPak, sem instalar nem
   rodar scripts, para obter nome, versão, arquitetura, dependências e os campos
   *Release* e *Distribution*.
2. **Descobre a origem:** olha a marca no *Release*, como `.fc44` (Fedora 44), `.mga10`
   (Mageia 10), `lp156`/`bp161`/`160100` (openSUSE Leap) e `alt1` (ALT). Quando o
   *Release* não diz (o Tumbleweed não marca; o ALT quase nunca diz o ramo), usa o
   *Distribution*, como "openSUSE Tumbleweed" ou "ALT p11". Se o cabeçalho não disser
   nada, tenta o nome do arquivo.
3. **Sugere o ambiente:** a distribuição e a release encontradas, com o grau de
   confiança (Alta, Média ou Baixa) explicado na tela. Sem nenhuma pista, usa o Fedora
   estável mais novo.
4. **Instala com o gerenciador certo:** dnf no Fedora e no Mageia, zypper no openSUSE,
   apt-get (APT-RPM) no ALT. As dependências vêm dos repositórios da distribuição.
5. **Atalho no menu.**

### 1.3 Os ambientes

| Distribuição | Releases oferecidas (nov. 2026) | Imagem | Contêiner |
| --- | --- | --- | --- |
| Fedora | latest, 44, 43 | `quay.io/fedora/fedora-toolbox` | `MultiPak-Fedora-44` |
| Mageia | 10, 9 | `docker.io/library/mageia` | `MultiPak-Mageia-10` |
| openSUSE | Leap 16.1, 16.0 e Tumbleweed | `docker.io/opensuse/leap`, `…/tumbleweed` | `MultiPak-openSUSE-16.1`, `MultiPak-openSUSE-Tumbleweed` |
| ALT Linux | p11, p10 e Sisyphus | `docker.io/library/alt` | `MultiPak-ALT-p11`, `MultiPak-ALT-Sisyphus` |

A lista é atualizada pelo catálogo (registro de imagens); a tabela acima é a reserva
sem rede. Pacote de release antiga (ex.: Leap 15.6) recebe essa release mesmo assim;
pacote de release mais nova que as estáveis recebe a linha contínua (Tumbleweed,
Sisyphus) ou, no Mageia, a estável mais nova. O Cauldron (desenvolvimento do Mageia)
não é oferecido: o contêiner dele não se distingue de uma estável futura.

### 1.4 Tema e ícones

Antes de cada instalação RPM, em todas as quatro distribuições, o MultiPak adiciona
um tema de ícones, o leitor de SVG e os temas Breeze do GTK 2 e 3, e segue o tema claro
ou escuro do computador, inclusive em programas GTK 2. Sem isso, o openSUSE mostrava
ícones quebrados na barra.

### 1.5 Assinatura

- dnf e APT-RPM instalam arquivo local sem exigir assinatura.
- O zypper recusa arquivo cuja assinatura não consegue verificar. O MultiPak confere
  antes (`rpm -K`); se não houver assinatura válida com as chaves do openSUSE, instala
  sem a checagem, **com um aviso**.

### 1.6 Atualizar e remover

- **Atualizar**: em **Gerenciador → Atualizar**, o MultiPak atualiza o contêiner inteiro
  com o comando do próprio Distrobox (`distrobox upgrade`), que roda `dnf upgrade`,
  `zypper dup` ou `apt-get dist-upgrade`, conforme a distribuição.
- **Versões novas**: o Gerenciador pergunta ao gerenciador da distribuição (`dnf
  check-update` e `repoquery`, `zypper list-updates` e `search`, ou `apt-cache policy`
  no APT-RPM) quais aplicativos têm versão nova e mostra cada um em **Com atualização**,
  **Em dia** ou **Sem fonte de atualização** (instalado de um arquivo que nenhum
  repositório oferece). Ao abrir, usa a lista que o contêiner já tem, sem baixar nada;
  **Verificar atualizações** antes roda a atualização das listas.
- **Remover**: `rpm -e`, que recusa se outro pacote depende do alvo.

### 1.7 Instalação direta no sistema (só Fedora Atomic/Universal Blue)

Em sistemas com rpm-ostree, há também a instalação direta na imagem do sistema. É o
último recurso: exige confirmação explícita, pode causar conflitos de dependência ou
afetar as atualizações do sistema, e só vale depois de reiniciar.

### 1.8 O que (ainda) não funciona

- Pacotes de RHEL, Rocky, Alma, Nobara: sem pista no cabeçalho, vão para o Fedora com
  confiança Baixa e um texto que explica o risco.
- Mageia Cauldron.

---

## 2. Explicação técnica

Caminhos de código são relativos a `src/multipak`.

### 2.1 Visão geral dos módulos

| Módulo | Papel |
| --- | --- |
| `metadata.py` (`inspect_package`, `rpm_release_hints`, `rpm_metadata_hint`) | leitura do cabeçalho e dica de origem |
| `models.py` (`rpm_environment`, `RPM_FAMILY_ENVIRONMENTS`, `container_title`) | ambientes e nomes |
| `catalog.py` (`REPOSITORIES`, `registry_tags`, `rpm_family_profiles`, `rpm_package_environment`) | releases do dia e escolha |
| `environments.py` (`release_identity`) | `opensuse-leap`/`-tumbleweed` → `opensuse`; `altlinux` → `alt` + ramo |
| `engine.py` (`_install_rpm`, `_install_dnf`, `_install_zypper`, `_install_apt_rpm`, `MAGEIA_SETUP_HOOK`) | instalação |
| `assets/rpm-setup.sh` | `--prepare`: ícones, Breeze GTK, tema claro/escuro |
| `suggestions.py` (`rpm_family_details`) | confiança e texto com o gerenciador |
| `system.py` | instalação direta pelo rpm-ostree |

### 2.2 Leitura

`rpm -qp --queryformat '%{NAME}\x1f%{VERSION}-%{RELEASE}\x1f%{ARCH}\x1f%{DISTRIBUTION}'`
(no Flatpak `/app/bin/rpm -qp --nosignature`), em modo binário, com limite de saída;
campos vazios ou com caracteres de controle são recusados. `--requires` dá as
dependências; linhas `warning: `/`error: ` (como o aviso NOKEY) são filtradas.

### 2.3 Dica de origem

`rpm_release_hints` aplica, em ordem: `.fcN`; `.mgaN`; `lp`/`bp` + `15x`–`19x`
(`lp156` → Leap 15.6); `1[5-9]XX00` no início do *Release* (`160100.2.1` → Leap 16.1,
o Leap 16 é SLE 16 por baixo); `altN` e `altN.pM`. `rpm_metadata_hint` dá prioridade ao
*Release* e usa o *Distribution* para Tumbleweed, "openSUSE Leap X" e "ALT pNN"; "ALT
Sisyphus" vira só `alt` (pacotes copiados do Sisyphus para um ramo mantêm essa marca).
Se o cabeçalho não der nada, `rpm_release_hints(nome do arquivo)` com uma única
resposta vira dica com origem `filename` (confiança Média).

### 2.4 Escolha do ambiente

- Fedora: o catálogo do Distrobox e o registro dão as versões; `fedora-latest` é
  resolvido para o número real antes de nomear o contêiner.
- Outras famílias (`rpm_family_profiles`): tags de `skopeo list-tags` ≥ `RPM_OLDEST`
  (Mageia 9, Leap 16.0, ALT p10; Leap 42.x ignorada) + a linha contínua.
- `rpm_package_environment`: exata; só a família → estável mais nova; mais nova que as
  estáveis → linha contínua ou estável mais nova; antiga → ela mesma.
- `suggestions.rpm_family_details`: **Alta** quando bate (cita o gerenciador),
  **Média** sem release ou com release mais nova, **Baixa** se a família não pôde ser
  oferecida.

### 2.5 Criação do contêiner

`distrobox create` com a imagem da release. No Mageia, um pre-init hook
(`MAGEIA_SETUP_HOOK`): o `setup_dnf` do Distrobox 1.8.2.5 reinstala `glibc-common`
(pacote do Fedora) quando faltam `/usr/share/i18n/charmaps` e depois roda `localedef`,
que a imagem do Mageia apaga. O hook instala `glibc-i18ndata` e, se faltar `localedef`,
faz `dnf reinstall glibc`.

### 2.6 Instalação (`engine._install_rpm`)

1. `rpm-setup.sh --prepare` (copiado para a pasta do guest, roda com sudo): pacotes de
   tema por família (openSUSE `adwaita-icon-theme gdk-pixbuf-loader-rsvg
   gtk2/3-metatheme-breeze6`; Fedora/Mageia `adwaita-icon-theme breeze-gtk`; ALT
   `icon-theme-adwaita librsvg gtk-theme-breeze`), um por um e não fatal; GTK 3
   `prefer-dark` em `/etc/xdg/gtk-3.0/settings.ini`; GTK 2 com `include` do gtkrc do
   Breeze ou Breeze-Dark em `/etc/gtk-2.0/gtkrc` (o XSETTINGS do host manda
   `Net/ThemeName ""`).
2. Por família (do `environment_id`):
   - **Fedora/Mageia**: `dnf -y install --downloadonly` e depois `dnf -y install`.
   - **openSUSE**: `zypper refresh`; `rpm -Kv` — sem linha "Signature…: OK" →
     `--no-gpg-checks --allow-unsigned-rpm` + aviso; `zypper install
     --auto-agree-with-licenses`.
   - **ALT**: `apt-get update` e `apt-get -y install <arquivo>`.

### 2.7 No contêiner (`guest.py`)

Estado `rpm -q`, arquivos `rpm -ql`, remoção `rpm -e` (com as checagens de dependência
do rpm; dependências não são removidas junto).

### 2.8 Contêiner existente

`release_identity` lê o os-release: Tumbleweed tem `VERSION_ID` em data → `tumbleweed`;
ALT usa `ALT_BRANCH_ID` (ou `p<VERSION_ID>`, ou `sisyphus`). O contêiner só é
reaproveitado se família e release baterem.

### 2.9 Diferenças em relação ao uso normal

| Assunto | Na distribuição | MultiPak |
| --- | --- | --- |
| Gerenciador | o da distribuição | o mesmo, sem terminal |
| zypper e pacote sem assinatura | recusa | instala com aviso |
| Tema | o do desktop da distribuição | Breeze GTK claro ou escuro, como o host |
| Remover | `dnf remove` pode levar dependências | `rpm -e`, só o pacote |

### 2.10 Onde olhar quando algo dá errado

- `~/.local/share/multipak/installations/<id>/installation.log`.
- `distrobox enter MultiPak-openSUSE-Tumbleweed -- rpm -qi <nome>`.
- Biblioteca faltando: veja a confiança na tela de ambiente e escolha outra release na
  lista avançada.
