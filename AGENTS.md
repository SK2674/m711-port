# AGENTS.md

Port Linux (CLI) do software oficial do mouse Redragon M711 "Cobra"
(Holtek `04d9:fc30`), escrito do zero em Python. O objetivo é substituir o
`RDCfg.exe` da Redragon, que só roda em Windows/Wine.

## O que é o que

```
Pesquisa.txt                  pesquisa inicial (leia antes de mexer no protocolo)
iniciar.sh                    abre o software oficial via Wine flatpak (RDCfg.exe)
                              — saída de recuperação
fix-usb.sh                    sudo: regra udev p/ libusb poder abrir o mouse
fix-hidraw.sh                 sudo: acesso aos hidraw (só p/ o software oficial)
m711/                         O PORT (ver "Módulos" abaixo)
port/mouse_m908/              SUBMODULE do driver C++ dokutan/mouse_m908 (GPLv3).
                              Use como referência e como verificador
                              independente do port Python.
pesquisa/                     scripts da etapa de investigação. Não use; o que
                              ensinaram está em m711/DESCOBERTAS.md
```

O `RDCfg.exe` oficial (19 MB, proprietário) **não está no repo** — está
ignorado no `.gitignore` por copyright. Mova-o para fora do diretório de
trabalho se for usá-lo.

## Comandos

```bash
cd m711

python3 m711.py <comando>      # entrypoint; funciona de qualquer diretório
python3 cli.py <comando>       # mesmo CLI, direto
python3 gerar_dados.py          # regera dados.py a partir dos .cpp do submodule

# Verificador independente: driver C++ do submodule. Precisa de cmake + libusb-dev.
#   cmake -S port/mouse_m908 -B port/mouse_m908/build
#   cmake --build port/mouse_m908/build
#   ./port/mouse_m908/build/mouse_m908 -M 711 -R -
# O caminho do binário gerado NÃO foi verificado (cmake não estava instalado
# nesta máquina quando este arquivo foi escrito). Confira com `ls build/`.
```

**Verificação**: não há suíte de testes. O check executável é
`python3 m711/m711.py verificar` — só lê, e valida o invariante
read-modify-write. **Rode antes de qualquer escrita.** Se ele acusar
divergência, pare: significa que o `monta` tem um buraco e uma escrita vai
corromper perfis. Erro de sintaxe: `python3 -m py_compile m711/*.py`.

## Comandos do CLI

Leitura (nenhum risco): `diagnostico`, `ler`, `bruto [1-5]`, `exportar [-o arq.ini]`,
`verificar`.

Escrita: `led`, `dpi`, `restaurar` — **sempre dry-run por padrão**; só
escrevem com `--yes`, e tiram backup automático antes. `backup [arq.json]`
salva estado + bytes crus em `~/.local/state/m711/`.

## Regras do protocolo (medidas, não adivinhe)

1. **Destravar/retravar é obrigatório.** Toda sequência começa com `02 f5 00`
   e termina com `02 f5 01`. Ler ou escrever sem o par deixa o mouse
   instável — foi assim que `ler` e `diagnostico` travavam o mouse.
   `Mouse._travar()` faz isso, em `try/finally`.

2. **O cabeçalho de 8 bytes já foi removido.** `usb.get_dados()` devolve só
   os dados. Os offsets do C++ contam a partir do quadro completo, então são
   **8 maiores** aqui. Confundir os dois desloca tudo silenciosamente.

3. **O `count` pedido tem que ser o `count` do quadro.** Pedir mais faz o
   mouse responder com menos e o `get_dados` reclamar.

4. **Endereço de leitura ≠ endereço de escrita, e a ordem no template é
   enganosa.** O `DATA_SETTINGS_3` do upstream agrupa DPI e keymap de um
   jeito que não corresponde à ordem de leitura. Resolva por **endereço**
   (`_ENDERECO_DPI`, `_ENDERECO_KEYMAP_ESCRITA`), nunca pela posição no
   array. Usar a posição fez cada perfil ler o DPI do anterior.

5. **DPI**: `dpi = codigo_baixo * 44.4444 * 2**byte_alto`. A tabela
   `_c_dpi_codes` do C++ está errada em vários pontos. O firmware quantiza em
   degraus de ~22 (pedir 500 grava 489) — isso é esperado, não é bug.

6. **Não existe DPI assimétrico.** O firmware aceita X≠Y e descarta o Y
   (lê 0). Bloqueado no CLI.

7. **Escrever a cor num modo que não usa cor trava o LED** (`off`, `arcoiris`,
   `onda`, `respirar_arcoiris`). Escrever `--modo` e `--cor` juntos é seguro.
   Bloqueado em `cli.py:MODOS_SEM_COR`.

8. **O texto do botão não carrega os 4 bytes.** `macro1:while` decodifica de
   `91 80 ff ff` mas re-codifica para `91 80 00 00`. Por isso o keymap está
   **bloqueado** na escrita — reescrever o texto perderia bytes. É o mesmo
   bug que corrompeu o mouse uma vez.

9. **Endereço de leitura do LED:** 0x0449 + 8*(perfil-1), count 7,
   layout `[R, G, B, modo1, vel, modo2, brilho]`. O byte 8 (count=8) é
   padding 0x80. Na **escrita** o brilho tem quadro separado (0x044f, count 1).

## Onde não mexer

`m711/monta()` é a função mais perigável do projeto: monta os 3 buffers que
reescrevem os 5 perfis de uma vez. Um erro ali se propaga para tudo e
**não aparece no dry-run se o diff só olhar o buffer errado** — o diff
precisa comparar os três (`_calcula_diff3`). Já corrompeu o mouse.

Bloqueados de propósito em `mouse.py:_valida_e_aplica_patch`:
`scrollspeed`, `report_rate`, `botoes`. Não destrave sem medir a escrita
primeiro.

## Módulos

Planos, sem `__init__.py`, `sys.path.insert` no entrypoint. Só stdlib.

`gerar_dados.py` → **`dados.py` (GERADO, nunca edite à mão)**: bytes do
protocolo extraídos dos `.cpp` do C++, GPLv3. `codigo.py`: enums, tabela
DPI, keycodes, cores. `usb.py`: binding libusb via ctypes. `mouse.py`:
leitura, `monta`, escrita. `ini.py`: `.ini` no formato `mouse_m908`.
`cli.py`: argparse e apresentação.

Docs: `m711/DESCOBERTAS.md` (bugs corrigidos + armadilhas medidas),
`m711/ARQUITETURA.md` (contrato entre módulos).

## Nomenclatura

Nomes canônicos em português com alias em inglês: `respirar`/`breathing`,
`estatico`/`static`, `rastro`/`alternating`/`go_without_trace`, `piscar`/
`flashing`, `reativo`/`reactive`, `botao`/`reactive_button`,
`aleatorio`/`random`. Ao adicionar um modo, adicione os dois.

Modes do firmware: 8 no software oficial, mais `botao`, `aleatorio` e
`respirar_arcoiris` que o firmware tem e a Redragon não expõe.

## Perigos

Comando desconhecido pode **travar o mouse** (inclusive o LED). Nada de
estender escrita sem: dry-run → backup → releitura com diff. O `restaurar`
reescreve os 3 buffers **incluindo o keymap**, que perde bytes — não use
enquanto o keymap estiver bloqueado.

Verificação cruzada: rode o `mouse_m908 -R -` (C++) depois de escrever pelo
port Python. Se os dois discordarem, um dos dois está errado.
