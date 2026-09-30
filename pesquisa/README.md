# pesquisa/

Scripts da etapa de investigação, **anteriores** ao port. Não são
necessários para usar o `m711` e não são mantidos.

Eles existem pelo que ensinaram, e o que ensinaram está registrado em
[`../m711/DESCOBERTAS.md`](../m711/DESCOBERTAS.md).

| script | o que fazia | o que revelou |
|---|---|---|
| `ver-descritor.py` | despeja o descritor USB | o M711 **não tem HID Feature Reports** — só Input/Output. Por isso hidraw não funciona e o port fala com o endpoint vendor via libusb. |
| `prova-hidraw.py` | PoC lendo a tabela de DPI por `HIDIOCGFEATURE` | negativos: confirmou que o caminho de hidraw é impasse. |
| `prova-transporte.py` | primeiro binding libusb via ctypes | o quirk do `wLength`: o mouse responde com `8 + count` bytes, não o que foi pedido. |
| `ler-dpi.py` | lê o dump bruto `mouse_m908 -D -` | layout real do registro de DPI e o bug do `-R`, que só lê 2 dos 4 bytes. |
| `calibrar.py` | cruza o driver com os `.prof` do app oficial | os `.prof` **não batem** com o estado real do mouse (dizem 500/1000/2000 onde o mouse tem 400/800/1600). Servem só como padrão de fábrica — não como referência. |

## Aviso

Estes scripts usam `Path.home()` com caminhos absolutos do ambiente onde
foram escritos, e `calibrar.py`/`ler-dpi.py` dependem do binário
`port/mouse_m908/mouse_m908` compilado. Nada disso é necessário para o port.
