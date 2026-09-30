# Arquitetura da porta do m711 (contrato entre módulos)

Documento normativo. **Nenhum módulo pode inventar nomes diferentes destes.**
Todos os módulos são planos (sem `__init__.py`) e vivem em `m711/`.
Somente stdlib. O entrypoint faz `sys.path.insert(0, dirname(__file__))`.

## Divisão de responsabilidade

| arquivo | responde por |
|---|---|
| `gerar_dados.py` | extrai tabelas do C++ e gera `dados.py` |
| `dados.py` | **GERADO — nunca editar à mão** |
| `codigo.py` | enums, tabelas semânticas, DPI, keycodes, cores |
| `usb.py` | binding libusb via ctypes |
| `mouse.py` | leitura, montagem de buffers, escrita |
| `ini.py` | .ini no formato mouse_m908 |
| `cli.py` | argparse, apresentação, backup/restore |
| `m711.py` | shim: `python3 m711/m711.py <comando>` |

Import forbidden: `mouse.py` **não** importa `ini.py` nem `cli.py`.
`codigo.py` não importa `mouse.py`. `ini.py` não importa `mouse.py`.
Só `cli.py` conhece todos.

---

## 1. `dados.py` (gerado)

Símbolos públicos, todos em MAIÚSCULA:

```python
LIMITS: dict[str, tuple[int, int]]
    # "scrollspeed": (0x01, 0x3f), "brilho": (1, 3), "velocidade": (1, 8),
    # "nivel": (0, 4), "dpi": (0x04, 0x8c), "dpi_alto": (0x00, 0x01)

BUTTON_NAMES: dict[int, str]          # {0: "button_left", ..., 9: "scroll_down"}
DPI_CODES_UPSTREAM: dict[int, tuple[int, int]]   # tabela do C++ — SABIDAMENTE ERRADA
KEYCODES: dict[str, tuple[int, int, int, int]]   # {"left": (0x81, 0, 0, 0), ...}
MODIFICADORES: dict[str, int]         # {"ctrl_l+": 1, ...}
TECLAS: dict[str, int]                # {"a": 0x04, ...}  (HID usage)
LIGHTMODE_VALUES: dict[tuple[int, int], str]   # {(0, 0): "off", (1, 2): "static", ...}
REPORT_RATE_VALUES: dict[int, int]    # {8: 125, 4: 250, 2: 500, 1: 1000}
MACROS_CODES: list[tuple[int, int]]   # 15 slots, endereço LE do slot

DATA_S_PROFILE:  list[list[int]]      # 6 x 16
DATA_SETTINGS_1: list[list[int]]      # 15 x 16
DATA_SETTINGS_2: list[int]            # 64 (flat)
DATA_SETTINGS_3: list[list[int]]      # 90 x 16
DATA_MACROS_1:   list[int]            # 16 (flat)
DATA_MACROS_2:   list[int]            # 256 (flat)
DATA_MACROS_3:   list[int]            # 16 (flat)
DATA_READ_1:     list[list[int]]      # 9 x 16
DATA_READ_2:     list[list[int]]      # 85 x 64
DATA_READ_3:     list[list[int]]      # 46 x 16
```

O arquivo gerado começa com um cabeçalho: que é gerado, por qual comando, de
quais arquivos-fonte, e o aviso de licença (o C++ é GPLv3 e o `dados.py`
derivado também precisa ser GPLv3).

Fontes: `port/mouse_m908/include/m711/data.cpp` (arrays do m711) e
`port/mouse_m908/include/data.cpp` (tabelas genéricas). Caminho **relativo ao
script**: `Path(__file__).resolve().parent.parent / "port/mouse_m908/include"`.

---

## 2. `codigo.py`

Constantes:
```python
PERFIS = 5
NIVEIS_DPI = 5
NOMES_BOTAO: list[str]   # 10 nomes na ordem física, igual a dados.BUTTON_NAMES
FATOR_DPI = 10000 / 225  # 44.4444...
```

Exceção: `class ErroCodigo(ValueError)`.

### Modo de luz
Nomes **canônicos em português, com alias em inglês**:
`off`, `estatico`, `respirar`, `arcoiris`, `piscar`, `onda`, `botao`,
`aleatorio`, `alternar`, `reativo`, `respirar_arcoiris`.
Aliases aceitos na entrada: `static`→`estatico`, `breathing`→`respirar`,
`rainbow`→`arcoiris`, `flashing`→`piscar`, `wave`→`onda`,
`reactive_button`→`botao`, `random`→`aleatorio`, `alternating`→`alternar`,
`reactive`→`reativo`, `breathing_rainbow`→`respirar_arcoiris`.

```python
MODOS: dict[str, tuple[int, int]]   # nome -> (byte11, byte13), a partir de dados.LIGHTMODE_VALUES
MODOS_INV: dict[tuple[int, int], str]
VELOCIDADES: dict[str, tuple[int, ...]]  # modo -> valores válidos de velocidade
def normaliza_modo(nome: str) -> str
def modo_para_bytes(nome: str) -> tuple[int, int]
def bytes_para_modo(b11: int, b13: int) -> str   # "?" se desconhecido
```

### Taxa de poll
```python
POLLRATE: dict[int, int]    # {125: 8, 250: 4, 500: 2, 1000: 1}
def pollrate_para_bytes(hz: int) -> int
def bytes_para_pollrate(b: int) -> int | None
```

### DPI — a fórmula real, não a tabela do C++
```python
def dpi_para_bytes(dpi: int) -> tuple[int, int]
    # procura o par (lo, hi) tal que |dpi - lo*FATOR*2**hi| seja mínimo,
    # restrito a lo em dados.LIMITS["dpi"], hi em {0, 1}.
    # ErroCodigo se dpi < 50 ou > 10000.
def bytes_para_dpi(lo: int, hi: int) -> int
    # round(lo * FATOR_DPI * 2**hi)
def dpi_para_texto(dpi: int) -> str        # "400" ou "X400Y400"
def texto_para_dpi(texto: str) -> tuple[int, int]
    # aceita "800" e "X800Y400" (case-insensitive). ErroCodigo no resto.
```

### Botões / keycodes
```python
ALIASES_BOTAO: dict[str, str]   # nome do .ini -> nome canônico de KEYCODES
    # "button_left"->"left", "button_dpi_up"->"dpi+", "button_dpi_down"->"dpi-",
    # "button_lightmode"->"led_mode_switch", "button_forward"->"forward",
    # "button_backward"->"backward", "button_middle"->"middle"
    # scroll_up/scroll_down já são iguais.
def botao_para_texto(b4: tuple) -> str
    # (0,0,0,0) -> "none"
    # casa exata em dados.KEYCODES -> nome canônico
    # 0x8f -> "ctrl_l+a" (modificadores em ordem, depois a tecla de TECLAS)
    # senão "bruto:aa bb cc dd"
def texto_para_botao(texto: str) -> tuple[int, int, int, int]
    # "none", nome de KEYCODES, ou "mod+mod+tecla" (ErroCodigo se não casar)
```
`botao_para_texto` e `texto_para_botao` devem ser **round-trip** para todo
`dados.KEYCODES`.

### Cor
```python
def cor_para_bytes(texto: str) -> tuple[int, int, int]   # "ff8800" / "#ff8800"
def bytes_para_cor(r: int, g: int, b: int) -> str         # "ff8800"
def cor_nome_para_rgb(texto: str) -> tuple[int,int,int]   # "vermelho"->(255,0,0), etc.
```

---

## 3. `usb.py`

```python
class ErroUsb(Exception):  # .codigo: int | None, .mensagem: str
class Dispositivo:
    def abrir(self) -> None
    def fechar(self) -> None
    def __enter__(self) -> "Dispositivo"
    def __exit__(self, *_) -> None
    def set(self, quadro: bytes, wvalue: int, tamanho: int, timeout: int = 1000) -> int
    def get(self, wvalue: int, tamanho: int, timeout: int = 1000) -> bytes
    @property
    def id(self) -> tuple[int, int, int, int]   # (vid, pid, bus, addr)
    @property
    def caminho(self) -> str                    # "/dev/bus/usb/003/004"

def discovering(vid: int, pid: int) -> list[tuple[tuple, str]]
    # [(id, caminho)] de todos os dispositivos casando, mesmo sem permissão
def versao_libusb() -> str | None
```

Constantes:
```python
CTRL_SET, CTRL_GET = 0x21, 0xA1
REQ_SET, REQ_GET = 0x09, 0x01
WINDEX = 0x0002
TIMEOUT_MS = 1000
REPORT_2, REPORT_3 = 0x0302, 0x0303
```

Regras de `get()`:
- envia `(0xA1, 0x01, 0, wvalue, WINDEX, tamanho)` e lê a resposta;
- o mouse responde com **8 + count** bytes, não com o `tamanho` pedido.
  `get()` devolve **exatamente** os bytes recebidos (sem padding) e
  `get_dados()` — se existir — devolve só os `count` bytes úteis a partir
  de `[8]`, validando o cabeçalho.
- `abrir()` faz `libusb_open`, `libusb_claim_interface(2)` e
  `libusb_detach_kernel_driver` **somente** se o claim falhar com
  `LIBUSB_ERROR_BUSY`; guarda a flag para reanexar em `fechar()`.
- `fechar()` reanexa o driver do kernel se desatou, libera a interface e fecha.
- Nunca chama `exit` no libusb global (vários `Dispositivo` no mesmo processo).
- Se `libusb-1.0.so.0` não existir, `versao_libusb()` devolve `None` e
  `Dispositivo.abrir()` levanta `ErroUsb` explicando que é preciso instalar
  `libusb-1.0-0` (ou `libusb`).
- Se der `LIBUSB_ERROR_ACCESS` (-3), a mensagem de erro **lembra** de rodar
  `./fix-usb.sh` na raiz do projeto.

---

## 4. `mouse.py`

```python
VID, PID = 0x04D9, 0xFC30
INTERFACE_VENDOR = 2

class ErroMouse(Exception)

class Mouse:
    def __init__(self)                      # não abre nada
    def abrir(self) -> "Mouse"             # cria e abre o Dispositivo
    def fechar(self) -> None
    def __enter__ / __exit__

    # ---- leitura ----
    def ler_buffer1(self) -> list[bytes]    # 9 quadros, usa DATA_READ_1
    def ler_buffer2(self) -> list[bytes]    # 85 quadros, usa DATA_READ_2
    def ler_buffer3(self) -> list[bytes]    # 46 quadros, usa DATA_READ_3
    def ler_bruto(self) -> dict[str, list[bytes]]   # {"b1":..,"b2":..,"b3":..}

    def ler(self) -> dict                   # estado completo, ver abaixo
    def decodifica(self, bruto) -> dict      # puro: bytes -> estado (testável)
    def monta(self, estado) -> tuple[bytes, bytes, bytes]   # puro: estado -> 3 buffers

    # ---- escrita ----
    def aplicar(self, alterar: dict) -> dict
        # ver "Travas" abaixo
    def enviar(self, b1, b2, b3) -> None
```

### Modelo de estado (dict puro, JSON-serializável)

```python
{
  "perfil_ativo": 1,                     # 1..5
  "desconhecidos": {"0x003e": "0000", "0x0446": "0000"},
  "perfis": [
    {
      "numero": 1,
      "led": {"cor": "ff0000", "modo": "respirar", "brilho": 3, "velocidade": 2},
      "scrollspeed": 1,
      "report_rate": 500,
      "dpi": [ {"ativo": true, "x": 400, "y": 400}, ... 5 níveis ],
      "botoes": {"left": "left", "right": "right", ... 10 entradas},
    }, ... 5 perfis
  ],
  "macros": [ {"slot": 1, "bruto": "hex", "eventos": []}, ... 15 ],
}
```
Chaves de `botoes` são as 10 posições físicas em ordem
(`dados.BUTTON_NAMES` sem o prefixo: `left, right, middle, forward,
backward, dpi_up, dpi_down, lightmode, scroll_up, scroll_down`) e os valores
são os nomes canônicos de `dados.KEYCODES` (via `codigo.botao_para_texto`).

### Offsets de leitura (medidos no hardware — não inventar)

LED / buffer1, quadro `i` (1..5), dados começam em `[8]`:
```
cor        = q[8], q[9], q[10]
modo       = (q[11], q[13])
velocidade = q[12]          # <-- o upstream lê q[13], está errado
brilho     = q[14]
```
`perfil_ativo` = `b1[0][8] + 1`, com clamp em 1..5.

`report_rate`: perfis 1..3 em `b1[6][6 + 2*(i-1)]`; perfis 4..5 em `b1[7][2*(i-4)]`.

DPI, buffer2: para o perfil `p` (1..5) o quadro é `b2[p-1]` (endereços
0x0042, 0x0102, 0x01b2, 0x0262, 0x0312). Nível `j` (0..4) em `b = 10 + 6*j`:
```
ativo = dados_b2[p-1][b] != 0
x = dados_b2[p-1][b+1] | (dados_b2[p-1][b+2] << 8)
y = dados_b2[p-1][b+3] | (dados_b2[p-1][b+4] << 8)
dpi = codigo.bytes_para_dpi(...)
```

Keymap: 10 botões × 5 perfis, passo 4 bytes. Endereços derivados dos quadros de
escrita `DATA_SETTINGS_3[35 + 10*i + j]` trocando `0xf3`→`0xf2` no byte 1.
Para o 10º botão de cada perfil, endereço = o do 1º botão + `0x2c`
(0x00ae, 0x016e, 0x021e, 0x02ce, 0x037e). Dados em `[8:12]`.
O passo por perfil é **10** (o upstream usa 8 e erra do perfil 2 em diante).

Macros: slot `n` (1..15) em `0x0478 + 200*(n-1)`, quatro leituras de 50 bytes.
Guardar os 200 bytes crus em hex; a decodificação dos eventos é da Fase 2
(`eventos` pode ficar `[]` nesta fase, mas o bruto tem de estar lá).

Scrollspeed: GET em 0x0020, 10 bytes, valor no **byte par** (0, 2, 4, 6, 8).
Não usar a tabela de leitura do C++ (que não lê).

### Offsets de escrita (do `writers.cpp` — a ordem importa)

`monta(estado)` parte de cópias profundas de `dados.DATA_SETTINGS_1/2/3` e
aplica, para i = 0..4 (perfil i+1):
```
b2[8 + 2*i]                    = scrollspeed
b1[3 + 2*i][8..10]             = cor
b1[3 + 2*i][11], b1[3+2*i][13]= modo
b1[3 + 2*i][12]                = velocidade
b1[4 + 2*i][8]                 = brilho
b3[7 + 5*i + j][8]             = dpi ativo (1/0)
b3[7 + 5*i + j][9..12]         = X_lo, X_hi, Y_lo, Y_hi
b3[35 + 10*i + j][8..11]       = keymap do botão j
b1[13][8 + 2*i] = report_rate  (i < 3)
b1[14][2 + 2*i] = report_rate  (i >= 3)
```
Retorna `(bytes(b1) achatado com 15 quadros de 16, bytes(b2) de 64,
bytes(b3) com 90 quadros de 16)` — o cli mostra o diff assim.

`enviar` faz, nesta ordem exata e sem nada no meio:
1. 15 quadros de 16 bytes, `wvalue=0x0302`
2. **um único** envio de 64 bytes, `wvalue=0x0302`
3. 90 quadros de 16 bytes, `wvalue=0x0302`

### `aplicar(alterar)`

`alterar` é um patch parcial, ex.:
```python
{"perfis": {1: {"led": {"cor": "00ff00", "brilho": 3}}}}
```
Passos (a ordem é a trava de segurança):
1. `bruto_antes = self.ler_bruto()`; `estado = self.decodifica(bruto_antes)`
2. `estado_novo = copia_funda(estado)`; aplica o patch validando com `codigo.py`
   (rejeita DPI, scrollspeed, report_rate e keymap **nesta fase**, com
   `ErroMouse` explicando que a escrita está bloqueada até a Fase 2)
3. `b1, b2, b3 = self.monta(estado_novo)`
4. `bruto_depois_de_simular = self.decodifica_buffers(b1, b2, b3)` e devolve
   `{"antes": estado, "depois": estado_novo, "b1": b1, "b2": b2, "b3": b3,
     "diff": [(rótulo, offset, antes, depois), ...], "simulado": True}`
5. **não** envia nada. Quem envia é o cli, com `--yes`, via `self.enviar(...)`
   e depois relê e compara.

---

## 5. `ini.py`

```python
def para_texto(estado: dict) -> str        # dicionário -> .ini do mouse_m908
def de_texto(texto: str) -> dict            # .ini -> dicionário de estado
def ler_arquivo(caminho) -> dict
def salvar_arquivo(caminho, estado) -> None
class ErroIni(ValueError)
```

Formato idêntico ao de `port/mouse_m908/examples/example_m711.ini`: seções
`[profile1]`..`[profile5]`, chaves `lightmode`, `color`, `brightness`, `speed`,
`scrollspeed`, `dpi1_enable`..`dpi5_enable`, `dpi1`..`dpi5`, `report_rate`,
`button_left`..`button_scroll_down`. Macros em seções `;## macroN` com linhas
`;# down<TAB>tecla` (nesta fase: preserva o bruto e ignora, sem erro).

`de_texto` **não** valida contra o mouse — só sintaxe e faixa; a validação de
faixa é de `codigo.py` e só acontece na escrita.

---

## 6. `cli.py`

```python
def main(argv: list[str] | None = None) -> int
```

Comandos: `ler`, `bruto [perfil]`, `exportar [-o arq]`, `led`, `backup [arq]`,
`restaurar arq`, `diagnostico`, `verificar`.
Opções globais: `--json`, `--simular` (default em escrita), `--yes`.

Regras:
- nada escreve sem `--yes`; sem ele, imprime o diff e diz o que digitar;
- toda mensagem de erro após uma escrita lembra de `iniciar.sh`;
- `led` exige pelo menos um campo e valida a faixa antes de abrir o mouse;
- `verificar` só lê: faz `monta(decodifica(ler_bruto()))` e mostra quais bytes
  mudariam de identidade (tem de ser zero) — é o invariante do read-modify-write;
- `diagnostico` não precisa do mouse: mostra libusb, se o VID:PID aparece em
  `lsusb`, permissões do nó USB, hidraw e versão do driver;
- backup em `~/.local/state/m711/backup-AAAAMMDD-HHMMSS.json` com
  `{"data":..., "estado":..., "bruto": {"b1":[hex...], "b2":..., "b3":...}}`;
- `restaurar` reescreve os 3 buffers a partir do bruto do backup e **exige
  `--yes`**.

## 7. `m711.py` (shim)

```python
#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cli import main
if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

## 8. Compatibilidade

`python3 m711/m711.py ler` e `python3 m711/m711.py bruto` têm que continuar
funcionando com a mesma saída legível de hoje (a informação errada de
velocidade passa a ser a correta — isso é o conserto, não uma regressão).
