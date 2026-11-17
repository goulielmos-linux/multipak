# Como o MultiPak instala pacotes do Puppy Linux

*MultiPak 1.1.0.*

---

## 1. Explicação simples

### 1.1 O que é um `.pet`?

O Puppy Linux não é uma distribuição com repositório próprio no sentido usual: cada
"sabor" do Puppy é montado **a partir dos pacotes de outra distribuição** (Slackware,
Debian ou Ubuntu). Um `.pet` é um TAR comprimido (gzip ou xz) com os arquivos do
programa, uma ficha `pet.specs` e, no fim do arquivo, a soma md5 do resto. A ficha diz,
entre outras coisas, **para qual distribuição o pacote foi feito** (ex.:
`slackware64|15.0`, `ubuntu|focal`, `debian|bookworm`).

### 1.2 Por que não há contêiner do Puppy?

Porque não precisa: os programas de um `.pet` foram compilados para a distribuição de
origem. O MultiPak instala o `.pet` **nessa distribuição**, no contêiner dela (por
exemplo, `MultiPak-Debian-12`), e o gerenciador dela passa a registrar e remover o
pacote como qualquer outro.

### 1.3 O que o MultiPak faz?

1. **Confere e lê sem executar:** calcula o md5 do arquivo e compara com o do fim;
   lê só o `pet.specs`. A arquitetura vem dos próprios programas (ELF).
2. **Sugere o ambiente:** Slackware 15.0, Debian 12, Ubuntu 20.04, 22.04 ou 24.04,
   conforme o `pet.specs`. Se a ficha não disser, ou disser algo que o MultiPak não
   oferece, sugere Debian 12 com confiança **Baixa** e um aviso.
3. **Converte:** reescreve o pacote com dono root, sem bits setuid, diretórios 755 e
   sem o `pet.specs`. No Slackware vira `nome-versão-arch-1_pet.txz`; no Debian/Ubuntu,
   um `.deb` com nome em minúsculas.
4. **Acrescenta as dependências:** nomes do Puppy que existem na distribuição são
   usados; as bibliotecas que os programas não acham (`ldd`) viram pacotes da
   distribuição (no Debian/Ubuntu, com o índice de arquivos do repositório, `apt-file`).
5. **Instala** com o gerenciador da distribuição e cria os atalhos.

Os scripts do Puppy (`pinstall.sh`, `puninstall.sh`) rodam dentro do contêiner, com
limite de tempo; se falharem, só aparece um aviso no registro.

### 1.4 Atualizar e remover

Depois da conversão, a instalação é Slackware ou DEB comum: atualizar o contêiner e
remover funcionam como nesses formatos. Na remoção, o `puninstall.sh` roda antes.
Como o pacote veio de um arquivo `.pet`, que nenhum repositório oferece, a verificação
de versões novas o mostra em **Sem fonte de atualização**; para atualizá-lo, instale a
versão nova a partir do arquivo.

### 1.5 O que (ainda) não funciona

- `.pet` de 32 bits (recusados).
- Bases antigas (tahr, xenial, slacko 14.x) ficam com confiança Baixa.
- Bibliotecas que o próprio Puppy oferece em outros `.pet` (ex.: `libgoffice` do
  S15Pup) não existem na distribuição: o MultiPak avisa para instalar esses `.pet`
  antes, no mesmo ambiente.
- Bibliotecas em `/usr/lib64` dentro de um `.pet` instalado no Debian não são achadas
  em tempo de execução.
- Um `.pet` com o mesmo nome de um pacote oficial pode ser substituído pelo apt numa
  atualização, se o repositório tiver versão maior.

---

## 2. Explicação técnica

Caminhos de código são relativos a `src/multipak`.

### 2.1 Visão geral dos módulos

| Módulo | Papel |
| --- | --- |
| `puppy.py` | leitura, md5, `compatible_environment`, `to_slackware`, `to_deb`, nomes |
| `metadata.py` | detecção depois do Arch e antes do Gentoo; dica pela ficha |
| `models.py` (`package_environment_kinds`, `package_profiles`) | o `.pet` vê ambientes `slackware` e `deb` |
| `engine.py` (`install`, `_convert_puppy`, `_puppy_deb_dependencies`) | cópia, conversão e dependências |
| `guest.py` (`puppy_deb_dependencies`, `run_puppy_script`, `slackware_dependencies`) | ldd, apt-file, scripts |
| `suggestions.py` | Alta quando bate com a ficha; Baixa com o ambiente sugerido |
| `package_icons.py` | ícone lido de dentro do `.pet` |
| `assets/multipak-puppy.xml` | MIME `application/x-puppy-pet` (`*.pet`) |

### 2.2 Leitura

`puppy.is_candidate`: gzip/xz chamado `.pet` ou com o trailer md5 de 32 caracteres. O
md5 é calculado sobre o arquivo inteiro menos o trailer (limite de 512 MB).
`pet.specs`: campos separados por `|`; os campos 10 e 11 dão distribuição e release.
A versão perde o sufixo do Puppy (`3.0.4-x86_64_s84` → `3.0.4`). Arquitetura vazia vira
`noarch`; i686 é recusado.

### 2.3 Escolha do ambiente

`compatible_environment`: `slackware|15.0`/`slackware64|15.0` → `slackware-15.0`;
`debian|bookworm`/`12` → `debian-12`; `ubuntu|focal`/`jammy`/`noble` →
20.04/22.04/24.04. Devuan e desconhecidos dão "" → `debian-12` com origem `default`. Com
distribuição na ficha, `package_profiles` mostra só os ambientes daquela família (ex.:
`slackware64|14.0` → Slackware 15.0, confiança Baixa).

### 2.4 Conversão (`engine._convert_puppy`)

`engine.install` copia o `.pet` para `source.pet`, relê e confere que é o mesmo
pacote, e chama `puppy.to_slackware` ou `puppy.to_deb`, que reescrevem o TAR membro a
membro: arquivos especiais são recusados; uma pasta `install/` também, no Slackware.
`pinstall.sh`/`puninstall.sh` vão para `/usr/share/multipak/puppy/<nome>/`:

- Slackware: `install/doinst.sh` roda o pinstall; o puninstall roda no `guest --remove`,
  antes do `removepkg`.
- Debian: `postinst` roda o pinstall e `prerm` o puninstall.

Ambos com `timeout 300` e `cd /`. Depois disso a transação é Slackware ou DEB
(`converted_from="puppy"`).

### 2.5 Dependências

- **Slackware**: os nomes do Puppy entram no `--slackware-dependencies` (só os que
  existem no slackpkg), junto com a busca por ldd do Slackware (ver
  `howslackwarecompatibilityworks.pt-BR.md`).
- **Debian/Ubuntu** (`guest.py --puppy-deb-dependencies`): `apt-get update`;
  `dpkg-deb -x` e `ldd` nos ELF com `LD_LIBRARY_PATH` nas pastas de `.so` do pacote;
  instala `apt-file`, `apt-file update` e `search --regexp`, preferindo pastas de
  biblioteca do sistema; acrescenta os nomes do Puppy que existem no apt e pacotes de
  dados gráficos quando há GTK, X11, Qt…; reescreve o `Depends` (`dpkg-deb -R` e `-b
  --root-owner-group`). Depois, a instalação DEB normal (`apt-get install`).
- Biblioteca não achada → aviso `puppy_missing_warning`.

### 2.6 Diferenças em relação ao uso normal

| Assunto | Puppy de verdade | MultiPak |
| --- | --- | --- |
| Registro | `/root/.packages` do Puppy | gerenciador da distribuição de origem |
| Dono e setuid | como vierem | root, sem setuid |
| Scripts | rodam como root no sistema | rodam no contêiner, com limite de tempo |
| Dependências | outros `.pet` | pacotes oficiais da distribuição de origem |

### 2.7 Onde olhar quando algo dá errado

- `~/.local/share/multipak/installations/<id>/installation.log` (pacotes adicionados,
  bibliotecas faltando, falha de pinstall).
- Debian/Ubuntu: `dpkg -s <nome>` no contêiner mostra o `Depends` gerado.
- Slackware: `/var/log/packages/<nome>-…_pet` no contêiner.
