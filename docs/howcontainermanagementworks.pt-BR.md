# Como o MultiPak cuida dos seus contêineres

*MultiPak 1.1.0.*

---

## 1. Em poucas palavras

### 1.1 Só os contêineres que o MultiPak criou

O MultiPak instala os pacotes em contêineres do Distrobox (Podman). Ele só trata como seus os
contêineres que **ele mesmo criou**. Um contêiner criado por você, pelo Distrobox ou por outra
ferramenta nunca é reaproveitado, listado ou atualizado pelo MultiPak, mesmo que tenha um nome
parecido com os dele (como `MultiPak-Fedora-43`).

### 1.2 Quando o nome já está ocupado

Cada distribuição tem um nome padrão de contêiner. Se esse nome já estiver em uso por um contêiner
que não é do MultiPak, o MultiPak deixa esse contêiner em paz e cria o seu com o próximo nome livre:
`C2-MultiPak-Fedora-43`, depois `C3-…`, e assim por diante. Daí em diante, ele usa sempre esse.
O mesmo vale para o contêiner compartilhado dos AppImages (`MultiPak-AppImages`).

### 1.3 Quando um contêiner do MultiPak é renomeado ou apagado

Se alguém renomear ou apagar por fora um contêiner do MultiPak:

- os apps dele continuam no Gerenciador, marcados como **"container not found"** (contêiner não
  encontrado);
- o MultiPak **não remove nada sozinho**: você decide o que fazer;
- um contêiner novo que alguém criar com o nome antigo não é do MultiPak e nunca é atualizado por
  engano.

Você tem duas saídas:

- **Desinstalar** o app: remove só o atalho e o registro do MultiPak. Nada dentro de nenhum
  contêiner é apagado.
- **Devolver o nome original** ao contêiner (por exemplo, com `podman rename`): o MultiPak o
  reconhece de novo e os apps voltam ao normal, sem fazer mais nada.

O contêiner dos AppImages tem uma opção a mais, **Reparar**, explicada no
[how-to dos AppImages](howappimagecompatibilityworks.pt-BR.md) (seção 1.6).

### 1.4 Por que isso importa

Antes da 1.1.0, o MultiPak já guardava o ID dos contêineres que criava, mas, para reaproveitar,
listar e atualizar contêineres, ainda se guiava pelo nome. Isso abria espaço para confusões:

- **Mexer no contêiner de outra pessoa.** Um contêiner seu chamado `MultiPak-Fedora-43` podia ser
  usado para instalar pacotes, aparecer na lista do MultiPak e ser atualizado por ele, mesmo sem ter
  sido criado pelo MultiPak.
- **Apps sumindo das atualizações.** Se um contêiner do MultiPak fosse renomeado, o registro dos apps
  continuava apontando para o nome antigo. A verificação de atualizações não encontrava o contêiner,
  e os apps podiam deixar de aparecer como atualizáveis, sem nenhum aviso.
- **Atualizar o contêiner errado.** Se alguém criasse um contêiner novo com o nome antigo, o MultiPak
  podia tratá-lo como o seu e atualizá-lo por engano.
- **Registros perdidos por engano.** Remover os apps automaticamente sempre que o Podman não achasse
  um contêiner poderia apagar registros bons por causa de uma falha passageira.

Com as regras acima, cada app aponta sempre para um contêiner que o MultiPak sabe que é dele. O que
mudou por fora fica visível ("container not found"), e a decisão fica com você.

---

## 2. Detalhes técnicos

### 2.1 Como o MultiPak reconhece os seus contêineres

- Todo contêiner criado pelo MultiPak ganha o rótulo do Podman `io.github.multipak.transaction`.
  Só os contêineres com esse rótulo são do MultiPak (`ExistingEnvironment.owned`, em
  `models.py`). Um nome parecido, sozinho, nunca basta.
- Contêineres com nome escolhido pela pessoa levam também `io.github.multipak.custom-name`, e os
  compartilhados, `io.github.multipak.shared-environment`.

### 2.2 Nomes livres

- `environments.container_names()` lista **todos** os nomes em uso no Podman (criados pelo Distrobox
  ou não), e `environment_choices.free_container_name()` devolve o nome padrão ou o menor `C<n>-`
  livre.
- `environment_choices.default_environment()` escolhe, entre os contêineres do MultiPak para aquela
  distribuição e versão, o de menor número. O contêiner dos AppImages nunca é usado como padrão para
  pacotes.

### 2.3 Contêiner renomeado ou apagado

- Cada app instalado num contêiner guarda o **ID** do contêiner (64 caracteres hexadecimais) e o
  nome. `containers.abandoned()` compara isso com `podman ps --all`: se o ID não existe mais, ou
  existe com outro nome, o app entra na lista de "container not found".
- Nada é removido nessa hora. O Podman pode responder errado por um instante (armazenamento movido,
  migração com falha), e apagar registros com base só nessa resposta poderia perder registros bons.
  Se o Podman não responde, nada é marcado.
- Desinstalar chama `containers.forget()`, que remove os atalhos e o registro do MultiPak e não
  toca em nenhum contêiner.
- Como o ID não muda quando o contêiner é renomeado, devolver o nome original basta para o app sair
  da lista na próxima verificação.
- O contêiner dos AppImages é tratado à parte, em `appimage_container.py`: renomeado, ele é
  retomado (`_reclaim`) com o nome padrão ou o menor livre; apagado, o Gerenciador oferece recriá-lo
  e reinstalar as dependências.
