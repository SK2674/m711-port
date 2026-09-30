# m711

Port Linux (CLI) do software oficial do mouse **Redragon M711 "Cobra"**
(Holtek `04d9:fc30`). A Redragon só publica um configurador para Windows
(`RDCfg.exe`); este projeto é uma reimplementação do protocolo USB do
mouse, em Python, sem dependências.

Baseado no trabalho de engenharia reversa do driver
[`dokutan/mouse_m908`](https://github.com/dokutan/mouse_m908) (GPLv3), com
vários bugs dele corrigidos — ver [Descobertas](m711/DESCOBERTAS.md).

## Estado

Funcional e testado no hardware (M711 rev. 0112, sensor PMW3325).

| Recurso | Situação |
|---|---|
| Ler configuração (LED, DPI, keymap, scroll, poll) | ✅ |
| LED: cor, modo, brilho, velocidade | ✅ escrita |
| DPI: 5 níveis por perfil, ativar/desativar | ✅ escrita |
| `.ini` compatível com `mouse_m908` | ✅ importa e exporta |
| Backup/restore do estado | ✅ |
| Macros (ler/editar) | ⚠️ só lê, em bruto |
| Mapeamento de botões | ⚠️ só leitura |
| Poll rate / scrollspeed | ⚠️ só leitura |
| Troca de perfil | ❌ |

> **O mouse tem 5 perfis, cada um com LED, DPI, keymap e poll rate
> independentes.** Só o perfil ativo acende o LED.

## Requisitos

- Linux
- Python 3.10+ (**testado só em 3.14**), **só stdlib** — nada de `pip install`
- `libusb-1.0` (Debian/Ubuntu: `libusb-1.0-0`, Fedora/Arch: `libusb`)
- acesso de escrita no device node USB:

```bash
sudo ./fix-usb.sh     # regra udev p/ libusb poder abrir o mouse
```

Sem isso: `libusb_open: access denied`. Confira com `m711 diagnostico`.

## Instalação

```bash
git clone --recurse-submodules <url> m711-port
cd m711-port
```

O `--recurse-submodules` é necessário **só** se você quiser regenerar os
dados (`m711/dados.py` já vem pronto no repositório).

Para usar sem clonar o projeto inteiro, basta o diretório `m711/`:

```bash
python3 m711/m711.py ler
```

## Uso

```bash
m711 ler                          # estado dos 5 perfis
m711 bruto [1-5]                  # hexdump dos bytes crus
m711 exportar -o meu.ini          # .ini no formato do mouse_m908
m711 led --perfil 1 --cor 00ff00  # simula (não escreve)
m711 led --perfil 1 --cor 00ff00 --yes
m711 dpi --perfil 1 --nivel 3 --valor 2400 --yes
m711 diagnostico                  # libusb, permissões, presença do mouse
m711 verificar                    # invariante de segurança (só lê)
```

**Nada é escrito sem `--yes`.** Sem essa flag o comando imprime o diff
byte a byte e para. Toda escrita tira backup automático antes, em
`~/.local/state/m711/`.

### Modos de LED

`off`, `estatico`, `respirar`, `arcoiris`, `piscar`, `onda`, `rastro`,
`reativo`, `botao`, `aleatorio`, `respirar_arcoiris`

O software oficial da Redragon oferece 8. `botao`, `aleatorio` e
`respirar_arcoiris` existem no firmware e **não** estão na UI do oficial.

### DPI

50–10000, quantizado pelo firmware em degraus de ~22 (pedir 500 grava 489 —
normal, não é bug). **Não existe DPI assimétrico**: o firmware aceita X≠Y e
descarta o Y.

## Segurança

Comando desconhecido pode **travar o mouse**, inclusive o LED. Portas
documentadas em [`AGENTS.md`](AGENTS.md) e [`DESCOBERTAS.md`](m711/DESCOBERTAS.md).

Rode sempre `m711 verificar` antes de mexer em escrita. Em caso de travamento,
o software oficial sempre volta o mouse ao normal:

```bash
./iniciar.sh      # abre o RDCfg.exe via Wine
```

## Dados gerados

`m711/dados.py` é **gerado** e não deve ser editado à mão:

```bash
python3 m711/gerar_dados.py
```

Ele extrai as tabelas de bytes dos `.cpp` do submodule
`port/mouse_m908` (dokutan/mouse_m908, GPLv3) e **valida a contagem de cada
tabela contra a dimensão declarada no C++**, falhando alto se não bater —
não confie em byte à mão num protocolo onde escrever errado pode travar o
mouse.

Por causa do copyleft do C++, **o `dados.py` é GPLv3**, mesmo que o resto do
projeto seja MIT — ver [`LICENSE`](LICENSE).

## Software oficial

O `RDCfg.exe` da Redragon é proprietário e **não** é redistribuído aqui.
Baixe do site da Redragon e rode com `./iniciar.sh` (Wine via flatpak) se
precisar do original, ou como saída de recuperação.

## Licença

MIT, exceto `m711/dados.py` (GPLv3, derivado). Ver [`LICENSE`](LICENSE).
