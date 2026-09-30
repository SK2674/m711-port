# m711 — log de descobertas e bugs corrigidos

Documento vivo. Registra o que foi **medido no hardware** e o que estava
errado, para não repetir a investigação.

## Armadilha principal: cabeçalho de 8 bytes

Toda resposta do mouse vem como `[8 bytes de cabeçalho][dados]`:

```
02|03  08  <addr_lo> <addr_hi>  <count>  00  fa  fa
```

`usb.get_dados()` **devolve só os dados**, sem o cabeçalho. Índices de
leitura contam a partir do primeiro byte de dado. Escrever o índice como se
o cabeçalho estivesse incluído (o que o `mouse_m908` faz, porque lá o buffer
bruto inclui tudo) desloca tudo em 8 — silenciosamente.

O `count` pedido tem que ser **exatamente** o `count` do quadro de leitura.
Pedir mais faz o mouse responder com menos e o `get_dados` reclamar.

O byte alto do endereço volta `0x60` (o firmware soma `0x6000` ao endereço
que a gente mandou), então não dá para conferir o endereço de volta.

---

## O MOUSE PRECISA SER DESTRAVADO E RETRAVADO (bug que travava o mouse na LEITURA)

**Este era o bug mais grave de todos, e ele fazia `m711 ler` e `m711
diagnostico` travarem o mouse.** Reportado pelo usuário em 2026-09-29.

O protocolo tem um par de frames de trava:

| frame | significado |
|---|---|
| `02 f5 00 00 ...` | **destrava** o mouse (equivalente a `_c_data_read_1[0]`) |
| `02 f5 01 00 ...` | **retrava** o mouse (equivalente a `_c_data_read_3[45]`) |

O driver C++ sempre envia os dois, em par: abre com o destravamento e fecha
com o retravamento, na mesma sequência de leitura ou escrita.

O meu código enviava **só o destravamento**. O `f5 01` estava no
`DATA_READ_3[45]`, e eu tinha substituído a leitura do buffer 3 pela
`ler_keymap()` (para corrigir o bug do 9º botão) sem levar o retravamento
junto. Resultado: **toda leitura deixava o mouse permanentemente
destravado**, e a partir da segunda ou terceira o mouse deixava de
funcionar.

Corrigido com `Mouse._travar(travado)`, chamado em par dentro de
`ler_bruto()` e de `enviar()`, ambos em `try/finally` — então o
retravamento acontece mesmo se a leitura falhar no meio. O `diagnostico`
faz o par explicitamente em vez de chamar `ler_buffer1` direto.

**Regra:** qualquer sequência de frames no M711 tem que começar com
`f5 00` e terminar com `f5 01`. Não existe leitura ou escrita "solta".

### Como isso foi encontrado

Rodei `diagnostico` e `ler` dezenas de vezes durante o desenvolvimento sem
notar, porque o mouse aguentava algumas leituras destravado. Só quando o
usuário rodou os dois seguidos é que travou — provavelmente porque o
destravamento residual se acumula. Testes repetidos com o mouse real não
substituem o usuário testando no ritmo dele.

---

## O M711 NÃO TEM DPI ASSIMÉTRICO (medido, bloqueado no CLI)

Pedir X≠Y **não dá erro** — o mouse aceita, grava e depois **descarta o
eixo Y**. Medido em 2026-09-29:

```
m711 dpi --perfil 1 --nivel 5 --x 800 --y 400 --yes
```

O buffer enviado estava correto (`01 12 00 09 00 00` = flag, X=0x12, Y=0x09).
A releitura do meu código devolveu X=800 Y=800, e o driver C++ leu
`dpi5=0x12000000` — ou seja **Y zerado**, não 800. O firmware usa só o X.

Por isso `--x N --y M` com N≠M é recusado antes de abrir o mouse. O registro
tem os 4 bytes, o que fez o layout parecer simétrico, mas o firmware ignora
o Y. Não há como ter sensibilidade diferente nos eixos neste mouse.

## A quantização do DPI é real e o cli avisa

O firmware só representa DPI em degraus de ~22 (`código × 44.4444 × 2^alto`).
Pedir 500 grava 489, pedir 1000 grava 978, pedir 10000 grava 9956. Valores
"redondos" que caem em código inteiro são exatos: 400, 800, 1200, 1600, 2000,
3200, 4000, 6400, 8000.

O `led`/`dpi` mostram o aviso antes de escrever, e a confirmação compara
contra o que o firmware **pode** devolver (não o que foi pedido), senão todo
DPI não-redondo apareceria como DIVERGE.

---

## ARMADILHA DO LED: escrever a cor em um modo que não usa cor TRAVA O MOUSE

**Descoberto na prática em 2026-09-29, reportado pelo usuário.**

Reprodutor:

```bash
m711 led --perfil 1 --modo arcoiris --yes     # põe em arcoiris
m711 led --perfil 1 --cor ff0000 --yes        # TRAVA O LED
```

O mouse **não brickou** — continuava enumerando no `lsusb`, os bytes de
configuração voltavam corretos na leitura (`ff 00 00 01 08 08 03` = vermelho
em arcoiris, brilho 3) e o mouse respondia. O que travava era a animação do
LED. Quando a cor é escrita em um modo cujo efeito é próprio, o firmware
guarda os canais RGB num estado que a animação não sabe reidratar.

**Recuperação** (o próprio usuário testou, funcionou):

```bash
m711 led --perfil 1 --modo estatico --cor ffffff --yes
```

Modo com cor própria + cor válida sempre destrava.

### Modos que NÃO usam a cor

| modo | bytes (b1,b2) | tem efeito próprio |
|---|---|---|
| `off` | (00,00) | sim |
| `arcoiris` | (01,08) | sim |
| `onda` | (02,00) | sim |
| `respirar_arcoiris` | (01,01) | sim |

### Modos que USAM a cor (escrita de cor é segura)

`estatico` (01,02), `respirar` (01,04), `piscar` (01,10), `botao` (03,00),
`aleatorio` (04,00), `alternar` (06,00), `reativo` (07,00)

### Regra

Nunca escrever **só** a cor (`--cor` sem `--modo`) quando o perfil estiver em
um modo sem cor. Escrever **juntos** (`--modo X --cor Y`) é sempre seguro,
porque a cor vai para um modo que a usa. Isso está travado em `cli.py`
(`MODOS_SEM_COR` + checagem em `cmd_led`), que recusa o comando com código 1
e imprime a receita de recuperação.

Isso vale para os outros campos sem interação também: quando a Fase 2
liberar DPI/keymap, escrever num perfil não deve depender do estado atual do
LED.

---

## Bugs do upstream `mouse_m908` confirmados e corrigidos

### 1. Velocidade do LED lida do byte errado
Escrita em `[12]`, mas `readers.cpp` e o protótipo Python liam `[13]`, que é
o segundo byte do modo. Por isso o `ler` antigo reportava "velocidade 4" no
perfil 1, que era na verdade o código de breathing.

### 2. Tabela de DPI do upstream divergente
`_c_dpi_codes` erra em vários pontos (400, 2000, 3200, 4000, 5000, 10000).
A fórmula real, medida: `dpi = codigo_baixo * 44.4444 * 2**byte_alto`, com o
baixo em 0x04..0x8c e o alto em {0, 1}. Acima de ~4933 DPI o alto vira 1.
Isso explica o "6400 virou 328 e travou": `0x0148` é 0x48 com alto 1.

O mantenedor do upstream confirma na issue #2 que essa tabela "segue nenhum
padrão usável" e precisa ser medida mouse por mouse.

### 3. Endereços de escrita de DPI transpostos (GRAVE)
O template `_c_data_settings_3` traz os quadros de DPI **agrupados por
nível** (os 5 perfis do nível 1, depois os do nível 2...), mas o upstream
assume a ordem **por perfil** em `b3[7 + 5*i + j]`. E os endereços do
template são `base+2 + 6*nível`, com as bases 0x0044, 0x0104, 0x01b4,
0x0264, 0x0314.

Resultado: cada perfil lia o DPI do perfil **anterior**. Confirmado na
prática: o perfil 2 lia `[800, 978, 978, 978, 978]` quando o esperado era
`[500, 1000, 2000, 3000, 5000]` — o 800 era o primeiro valor do perfil 1.

Corrigido resolvendo o índice do quadro pelo **endereço**, não pela posição
no array (`_ENDERECO_DPI` em `mouse.py`).

### 4. Endereços de escrita de keymap deslocados (GRAVE)
Mesmo problema: a partir do 9º botão de cada perfil, o template salta um
endereço (0x009e → 0x00aa em vez de 0x00a2). Como o 10º slot de cada perfil
é o 1º do perfil seguinte, o estrago se propaga. Os 8 primeiros botões de
cada perfil estavam certos; o `profile_switch` (9º) era lido como `none`.

Corrigido reescrevendo o endereço dos 10 quadros de cada perfil para a
sequência medida de 4 em 4.

### 5. Keymap de leitura perdia o 9º botão
`DATA_READ_3` (tabela do upstream) traz 9 botões por perfil e salta
0x009e → 0x00aa. A sequência real é de 10 em passo 4, endereços 0x0082 +
4*j (mesma base nos outros perfis: 0x0142, 0x01f2, 0x02a2, 0x0352).

Substituído por `ler_keymap()`, que lê os 10 endereços medidos.
Confirmado contra `usb-mouse-config` (ernesto-elsaesser), que usa as mesmas
bases.

### 6. Brilho: escrita e leitura em endereços diferentes
Na **leitura**, o brilho vem embutido no registro de LED (0x0449 com
count=7, byte [6] dos dados). Na **escrita**, tem quadro separado (0x044f,
count=1, byte [8]). Não são o mesmo lugar — comparar os dois dá falso
negativo de "brilho virou 0".

### 7. `buffer2[265]` declarado, 256 enviados
Em `write_macro`. Não afeta a leitura.

### 8. Scrollspeed "não legível"
Verdadeiro para as tabelas `_c_data_read_*`, falso na prática: um GET
direto em 0x0020 com count 10 devolve os 5 perfis, valor no byte par.

---

## Endereços medidos

| o quê | endereço | count | layout |
|---|---|---|---|
| perfil ativo | 0x002c | 1 | `[0]` = índice 0-based |
| scrollspeed | 0x0020 | 10 | 5 perfis, valor no byte par |
| poll rate | 0x0032 | 10 | 5 perfis, valor no byte par |
| LED perfil N | 0x0449 + 8*(N-1) | 7 | R,G,B,m1,vel,m2,brilho (7º byte com count=8 é 0x80) |
| brilho (escrita) | 0x044f + 8*(N-1) | 1 | quadro separado |
| DPI perfil N | 0x0042,0x0102,0x01b2,0x0262,0x0312 | 32 | 2 bytes de índice, depois 5×6: `[flag,X_lo,X_hi,Y_lo,Y_hi,lixo]` |
| keymap perfil N | base + 4*j (10 botões) | 4 | keycode direto |
| macros slot N | 0x0478 + 200*(N-1) | 4×50 | 200 bytes |

Bases do keymap: 0x0082, 0x0142, 0x01f2, 0x02a2, 0x0352.

Desconhecidos que o driver escreve mas leem 0: 0x003e e 0x0446.

---

## Quantização de DPI: o firmware não representa 500

Pedir 500 produz o código 0x0b, que relê como 489 (= 11 × 44.4444). O mesmo
com 1000→978, 3000→2978, 5000→4978. Os valores "redondos" que o mouse
aceita de verdade são os que caem em código inteiro × 44.4444: 400, 800,
1600, 3200, 6400 (esses são exatos).

Os `.prof` do software oficial dizem 500/1000/2000/3000/5000, mas
**não batem com o que o mouse tem** — o perfil 1 estava em
400/800/1600/3200/6400 enquanto o `.prof` dizia 500/1000/2000/3000/5000. Os
`.prof` são os padrões de fábrica, não o estado atual. Não serve de
referência para cross-check.

---

## Estado validado em 2026-09-29

O mouse foi modificado acidentalmente durante a investigação (rodei
`mouse_m908 -c` num .ini nosso, que escrevou no mouse e corrompeu DPI e modo
do LED). Recuperado com o `restaurar` do próprio m711 depois de corrigir os
bugs 3 e 4. Estado final verificado por `mouse_m908 -R -` (driver
independente) e por `m711 verificar`.

Configuração atual: perfil 1 = respirar, ff0000, brilho 3, vel 2,
DPI 400/800/1600/3200/6400. Perfis 2-5 = onda, ff0000, brilho 2, vel 4,
DPI 489/978/2000/2978/4978. Todos com poll 500 Hz e scrollspeed 1.

O perfil 1 tem `forward` mapeado para `macro1:while`, e o b2/b3 desse slot
ficaram `00 00` (antes eram `ff ff`) — efeito colateral da escrita via
template, que não preserva os bytes de repetição da macro. Não afeta o
mapeamento, só a contagem de repetições.

Depois da investigação do travamento do LED, o perfil 1 ficou em
`estatico` branco com brilho 3 e velocidade 8.
