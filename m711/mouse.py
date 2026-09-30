"""Núcleo do Redragon M711: leitura, montagem de buffers e escrita.

É aqui que os bytes do protocolo viram estado legível e vice-versa.
O módulo é dividido em três partes:

1. **Leitura** (`ler_buffer*`, `ler_bruto`): usa `usb.Dispositivo` para
   enviar os quadros de `dados.DATA_READ_*` e receber os buffers crus.
2. **Decodificação** (`decodifica`): função pura que transforma os bytes
   crus em um dicionário de estado legível.
3. **Montagem** (`monta`): função pura que transforma o estado de volta
   em buffers de escrita, prontos para `enviar`.

A invariante crítica é: `monta(decodifica(ler_bruto()))` deve reproduzir
exatamente os buffers que o mouse tem (read-modify-write). O teste em
`/tmp/teste_invariante.py` verifica isso.

**Nada é escrito sem chamada explícita de `enviar()`** — `aplicar()` apenas
simula e calcula o diff.
"""

from __future__ import annotations

import copy
from typing import Any

import dados
import codigo
import usb

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

VID, PID = 0x04D9, 0xFC30
INTERFACE_VENDOR = 2

# Endereços do keymap: 10 botões por perfil, passo de 4 bytes.
#
# Medido no M711 (o quadro de leitura do upstream, DATA_READ_3, pula o
# endereço 0x00a2 e lê 0x00aa no lugar — por isso ele perde o
# profile_switch do botão 9. A sequência abaixo é a sequencial de verdade,
# conferida botão a botão contra o que o mouse devolve):
#
#   perfil 1: 0x0082 0x0086 0x008a 0x008e 0x0092 0x0096 0x009a 0x009e
#             0x00a2 0x00a6
#   perfil 2: 0x0142 ...             perfil 3: 0x01f2 ...
#   perfil 4: 0x02a2 ...             perfil 5: 0x0352 ...
#
# Confirmado contra usb-mouse-config (ernesto-elsaesser), que usa as mesmas
# bases (0x0082, 0x0142, 0x01f2, 0x02a2, 0x0352) com passo 4.
_ENDERECO_KEYMAP = [
    [0x0082 + 0x04 * j for j in range(10)],
    [0x0142 + 0x04 * j for j in range(10)],
    [0x01F2 + 0x04 * j for j in range(10)],
    [0x02A2 + 0x04 * j for j in range(10)],
    [0x0352 + 0x04 * j for j in range(10)],
]

# Endereço base para macros: slot n (1..15) em 0x0478 + 200*(n-1)
_ENDERECO_MACRO_BASE = 0x0478
_TAMANHO_MACRO = 200  # 4 leituras de 50 bytes cada


# Endereço de escrita de cada (perfil, nível) de DPI.
#
# Medido no M711: a leitura usa base + 6*nível, com as bases
# 0x0042, 0x0102, 0x01b2, 0x0262, 0x0312. O template de escrita
# (_c_data_settings_3 do upstream) tem os mesmos endereços +2 (por causa
# do índice de perfil de 2 bytes que a leitura tem e a escrita não) e,
# o mais importante, os quadros vêm agrupados por NÍVEL, não por perfil:
#
#   [7..11]  nível 1 dos perfis 1..5   (0x0044 0x0104 0x01b4 0x0264 0x0314)
#   [12..16] nível 2 dos perfis 1..5
#   [17..21] nível 3 ...
#   [22..26] nível 4 ...
#   [27..31] nível 5 ...
#
# O upstream assume a ordem por perfil em b3[7 + 5*i + j], que na verdade
# escreve o nível i do perfil j — por isso cada perfil lia o DPI do perfil
# anterior. Aqui cada (perfil, nível) é resolvido pelo endereço, que é o
# que o mouse realmente usa.
_DPI_INDICES: dict[tuple[int, int], int] = {}
for _i, _q in enumerate(dados.DATA_SETTINGS_3):
    _end = _q[2] | (_q[3] << 8)
    for _perfil, _base in enumerate((0x0044, 0x0104, 0x01B4, 0x0264, 0x0314)):
        for _nivel in range(5):
            if _end == _base + 0x06 * _nivel:
                _DPI_INDICES[(_perfil, _nivel)] = _i

if len(_DPI_INDICES) != 25:
    raise ValueError(
        f"esperava 25 quadros de DPI em DATA_SETTINGS_3, encontrei "
        f"{len(_DPI_INDICES)} — o template do upstream mudou de formato")

# Índice do quadro de escrita para cada (perfil, nível).
_ENDERECO_DPI = [[_DPI_INDICES[(i, j)] for j in range(5)] for i in range(5)]

# Mesma armadilha no keymap, e pior: no template _c_data_settings_3 os 10
# quadros de botão de cada perfil têm os endereços ERRADOS a partir do 9º.
# O upstream salta de 0x009e direto para 0x00aa, mas a sequência que o
# mouse lê é de 4 em 4 sem buraco (0x009e, 0x00a2, 0x00a6). Isso
# significa que os dois últimos botões de cada perfil são gravados no
# endereço errado — e, como o 10º slot de cada perfil é o 1º do perfil
# seguinte, o estrago se propaga.
#
# Correção: reescrever o endereço dos 10 quadros de cada perfil para a
# sequência medida. O índice no template continua sendo 35 + 10*perfil +
# botão, só o endereço muda.
_ENDERECO_KEYMAP_BASE = (0x0082, 0x0142, 0x01F2, 0x02A2, 0x0352)

# Índice do quadro de escrita para cada (perfil, botão).
_ENDERECO_KEYMAP_ESCRITA = [
    [35 + 10 * p + j for j in range(10)] for p in range(5)
]


class ErroMouse(Exception):
    """Erro de operação do mouse (leitura, escrita, validação)."""


# ---------------------------------------------------------------------------
# Classe principal
# ---------------------------------------------------------------------------

class Mouse:
    """Representa um mouse Redragon M711 aberto via USB.

    Uso típico::

        with Mouse() as m:
            estado = m.ler()
            print(estado["perfis"][0]["led"])
    """

    def __init__(self) -> None:
        """Inicializa o objeto, mas não abre o dispositivo."""
        self._dev: usb.Dispositivo | None = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def abrir(self) -> "Mouse":
        """Abre o dispositivo USB. Retorna self para encadeamento."""
        if self._dev is not None:
            raise ErroMouse("mouse já está aberto")
        self._dev = usb.Dispositivo(VID, PID, INTERFACE_VENDOR)
        self._dev.abrir()
        return self

    def fechar(self) -> None:
        """Fecha o dispositivo USB. Idempotente."""
        if self._dev is not None:
            self._dev.fechar()
            self._dev = None

    def __enter__(self) -> "Mouse":
        return self.abrir()

    def __exit__(self, *_: Any) -> None:
        self.fechar()

    def _exige_aberto(self) -> usb.Dispositivo:
        """Garante que o dispositivo está aberto."""
        if self._dev is None:
            raise ErroMouse("mouse não está aberto (chame abrir())")
        return self._dev

    # ------------------------------------------------------------------
    # Leitura — retorna bytes crus
    # ------------------------------------------------------------------

    def ler_buffer1(self) -> list[bytes]:
        """Lê o buffer 1: perfil ativo e LED dos 5 perfis.

        Só os 6 primeiros quadros do DATA_READ_1: o perfil ativo (0x002c) e
        os 5 registros de LED (0x0449 a 0x0469). A taxa de poll é lida à
        parte, em ler_pollrate(), porque 0x0032 com count=10 traz os 5
        perfis de uma vez.

        NÃO destrava o mouse: quem faz isso é ler_bruto(), que também
        retrava no final. Destravar aqui e nunca retravar deixava o mouse
        instável depois de qualquer leitura.

        Retorna lista de 6 `bytes`: [perfil_ativo, led_p1, ..., led_p5].
        """
        dev = self._exige_aberto()
        # Cada quadro tem o seu próprio count, e o mouse anuncia esse count
        # no cabeçalho da resposta — pedir mais do que ele devolve faz o
        # get_dados reclamar.
        b1: list[bytes] = []
        for quadro in dados.DATA_READ_1[1:7]:
            count = quadro[4]
            dev.set(bytes(quadro), usb.REPORT_2, 16)
            b1.append(dev.get_dados(usb.REPORT_2, count))
        return b1

    def ler_buffer2(self) -> list[bytes]:
        """Lê o buffer 2: tabela de DPI (85 quadros de 64 bytes).

        Retorna lista de 85 bytes-objects de 64 bytes cada.
        """
        dev = self._exige_aberto()
        b2: list[bytes] = []
        for quadro in dados.DATA_READ_2:
            count = quadro[4]
            dev.set(bytes(quadro), usb.REPORT_3, 64)
            b2.append(dev.get_dados(usb.REPORT_3, count))
        return b2

    def ler_keymap(self) -> list[list[bytes]]:
        """Lê o keymap completo: 10 botões × 5 perfis, 4 bytes cada.

        Não usa a tabela DATA_READ_3 do upstream porque ela pula o endereço
        0x00a2 (e os equivalentes nos outros perfis) e lê o endereço
        seguinte no lugar — o que faz o botão 9 (profile_switch) ser
        reportado como "none". Aqui os endereços vêm de _ENDERECO_KEYMAP,
        medidos no M711 e confirmados no projeto usb-mouse-config.

        Retorna lista de 5 listas de 10 `bytes` de 4 bytes.
        """
        dev = self._exige_aberto()
        tabela: list[list[bytes]] = []
        for enderecos in _ENDERECO_KEYMAP:
            botoes: list[bytes] = []
            for addr in enderecos:
                quadro = bytearray(16)
                quadro[0] = 0x02
                quadro[1] = 0xF2  # OP_SEEK
                quadro[2] = addr & 0xFF
                quadro[3] = (addr >> 8) & 0xFF
                quadro[4] = 0x04  # count = 4 (o keycode do botão)
                dev.set(bytes(quadro), usb.REPORT_2, 16)
                botoes.append(dev.get_dados(usb.REPORT_2, 0x04))
            tabela.append(botoes)
        return tabela

    def ler_bruto(self) -> dict[str, Any]:
        """Lê todos os buffers crus do mouse.

        O protocolo exige destravar antes de ler (`f5 00`) e RETRAVAR
        depois (`f5 01`). O driver C++ sempre faz os dois, em par. Ler sem
        retravar deixa o mouse permanentemente destravado, que é o que
        fazia os comandos de leitura travarem o mouse.

        O retravamento é feito no `finally`, então acontece mesmo se uma
        leitura falhar no meio.

        Retorna dict com as chaves ``b1``, ``b2``, ``keymap``,
        ``scrollspeed``, ``pollrate`` e ``macros``.
        """
        self._travar(False)  # f5 00 = destravar
        try:
            return {
                "b1": self.ler_buffer1(),
                "b2": self.ler_buffer2(),
                "keymap": self.ler_keymap(),
                "scrollspeed": self.ler_scrollspeed(),
                "pollrate": self.ler_pollrate(),
                "macros": self.ler_macros(),
            }
        finally:
            self._travar(True)  # f5 01 = retravar

    def _travar(self, travado: bool) -> None:
        """Destrava (`travado=False`) ou retrava (`travado=True`) o mouse.

        Frame `02 f5 <00|01> 00 ...` (OP_LOCK do protocolo). É o mesmo
        quadro que o C++ manda em `_c_data_read_1[0]` (destravar) e
        `_c_data_read_3[45]` (retravar).
        """
        dev = self._exige_aberto()
        quadro = bytearray(16)
        quadro[0] = 0x02
        quadro[1] = 0xF5  # OP_LOCK
        quadro[2] = 0x01 if travado else 0x00
        dev.set(bytes(quadro), usb.REPORT_2, 16)

    def ler_pollrate(self) -> list[int]:
        """Lê a taxa de poll dos 5 perfis de uma vez (GET em 0x0032).

        Medido: pedindo count=10 o mouse devolve os 5 perfis como pares de
        2 bytes, com o valor no byte par. A tabela de leitura do upstream
        (DATA_READ_1) pede count=5 e depois um segundo quadro em 0x0038 —
        funciona, mas é a mesma informação dividida em dois.

        Retorna lista de 5 ints em Hz.
        """
        dev = self._exige_aberto()
        valores = self._seek(0x0032, 0x0A)
        return [valores[i] for i in range(0, 0x0A, 2)]

    def _seek(self, addr: int, count: int) -> bytes:
        """Faz um OP_SEEK (0xf2) e devolve os `count` bytes de dados.

        Atalho para as leituras pontuais, todas no mesmo formato de quadro.
        """
        dev = self._exige_aberto()
        quadro = bytearray(16)
        quadro[0] = 0x02
        quadro[1] = 0xF2  # OP_SEEK
        quadro[2] = addr & 0xFF
        quadro[3] = (addr >> 8) & 0xFF
        quadro[4] = count
        dev.set(bytes(quadro), usb.REPORT_2, 16)
        return dev.get_dados(usb.REPORT_2, count)

    def ler_scrollspeed(self) -> list[int]:
        """Lê o scrollspeed (GET em 0x0020, count 10, valor no byte par).

        Retorna lista de 5 ints (um por perfil).
        """
        dev = self._exige_aberto()
        quadro = bytearray(16)
        quadro[0] = 0x02
        quadro[1] = 0xF2
        quadro[2] = 0x20
        quadro[3] = 0x00
        quadro[4] = 0x0A  # count = 10
        dev.set(bytes(quadro), usb.REPORT_2, 16)
        bruto = dev.get_dados(usb.REPORT_2, 10)
        # Valor nos bytes pares: 0, 2, 4, 6, 8
        return [bruto[i] for i in range(0, 10, 2)]

    def ler_macros(self) -> list[dict[str, Any]]:
        """Lê os 15 slots de macros (200 bytes cada).

        Retorna lista de 15 dicts com ``slot`` e ``bruto`` (hex string).
        """
        dev = self._exige_aberto()
        macros: list[dict[str, Any]] = []
        for n in range(1, 16):
            addr = _ENDERECO_MACRO_BASE + _TAMANHO_MACRO * (n - 1)
            bruto_bytes = bytearray()
            for i in range(4):
                quadro = bytearray(16)
                quadro[0] = 0x02
                quadro[1] = 0xF2
                quadro[2] = (addr + i * 50) & 0xFF
                quadro[3] = ((addr + i * 50) >> 8) & 0xFF
                quadro[4] = 0x32  # count = 50
                dev.set(bytes(quadro), usb.REPORT_2, 16)
                bruto_bytes.extend(dev.get_dados(usb.REPORT_2, 50))
            macros.append({"slot": n, "bruto": bruto_bytes.hex()})
        return macros

    # ------------------------------------------------------------------
    # Decodificação — função pura (não abre o mouse)
    # ------------------------------------------------------------------

    @staticmethod
    def decodifica(bruto: dict[str, Any]) -> dict[str, Any]:
        """Decodifica os bytes crus em estado legível.

        Args:
            bruto: dict retornado por `ler_bruto()`.
        Returns:
            dict com ``perfil_ativo``, ``perfis``, ``scrollspeed`` e
            ``macros``.
        """
        b1 = bruto["b1"]
        b2 = bruto["b2"]

        # ATENÇÃO: `get_dados()` já devolve só os bytes de dados, sem o
        # cabeçalho de 8 bytes (02|03 08 <addr> <count> 00 fa fa). Todos os
        # offsets abaixo contam a partir do primeiro byte de dado, não do
        # início do quadro. Os endereços e o layout foram medidos no M711.

        # Perfil ativo: b1[0] tem count=1, logo o índice 0 é o valor.
        perfil_ativo = min(max(b1[0][0] + 1, 1), 5)

        # Perfis
        perfis: list[dict[str, Any]] = []
        for i in range(5):  # i = 0..4 (perfil i+1)
            idx = i + 1  # índice 1..5 (b1[1..5] = LED dos perfis 1..5)

            # LED: registro de 7 bytes = R, G, B, modo1, vel, modo2, brilho.
            # O 8º byte (count=8) é um 0x80 de padding, não usamos.
            led = b1[idx]
            cor = codigo.bytes_para_cor(led[0], led[1], led[2])
            modo_bytes = (led[3], led[5])
            modo = codigo.bytes_para_modo(*modo_bytes)
            velocidade = led[4]
            brilho = led[6]

            # Taxa de poll: lida dos 5 perfis de uma vez em 0x0032.
            rr_byte = bruto["pollrate"][i]
            report_rate = codigo.bytes_para_pollrate(rr_byte)
            if report_rate is None:
                report_rate = f"0x{rr_byte:02x}"

            # DPI: 32 bytes por perfil. Os 2 primeiros são o índice do perfil,
            # depois 5 registros de 6 bytes: [flag, X_lo, X_hi, Y_lo, Y_hi, lixo]
            dpi_list: list[dict[str, Any]] = []
            b2_perfil = b2[i]
            for j in range(5):
                b = 2 + 6 * j
                ativo = b2_perfil[b] != 0
                x_lo, x_hi, y_lo, y_hi = b2_perfil[b + 1:b + 5]
                dpi_x = codigo.bytes_para_dpi(x_lo, x_hi)
                dpi_y = codigo.bytes_para_dpi(y_lo, y_hi)
                dpi_list.append({
                    "ativo": ativo,
                    "x": dpi_x,
                    "y": dpi_y,
                })

            # Keymap: 10 botões por perfil, lidos dos endereços medidos.
            botoes: dict[str, str] = {}
            for j in range(10):
                key_bytes = tuple(bruto["keymap"][i][j])
                nome_botao = codigo.NOMES_BOTAO[j]
                botoes[nome_botao] = codigo.botao_para_texto(key_bytes)

            perfis.append({
                "numero": idx,
                "led": {
                    "cor": cor,
                    "modo": modo,
                    "brilho": brilho,
                    "velocidade": velocidade,
                },
                "scrollspeed": bruto["scrollspeed"][i],
                "report_rate": report_rate,
                "dpi": dpi_list,
                "botoes": botoes,
            })

        return {
            "perfil_ativo": perfil_ativo,
            "perfis": perfis,
            "scrollspeed": bruto["scrollspeed"],
            "macros": bruto["macros"],
        }

    # ------------------------------------------------------------------
    # Montagem — função pura (não escreve)
    # ------------------------------------------------------------------

    @staticmethod
    def monta(estado: dict[str, Any]) -> tuple[bytes, bytes, bytes]:
        """Monta os buffers de escrita a partir do estado.

        Args:
            estado: dict retornado por `decodifica()`.
        Returns:
            Tupla (b1, b2, b3) como bytes achatados.
        """
        # Cópias profundas dos templates de dados
        b1 = [list(q) for q in dados.DATA_SETTINGS_1]
        b2 = list(dados.DATA_SETTINGS_2)
        b3 = [list(q) for q in dados.DATA_SETTINGS_3]

        # Conserta os endereços do keymap, que o template do upstream traz
        # errados a partir do 9º botão de cada perfil (pula um endereço).
        for perfil, linha in enumerate(_ENDERECO_KEYMAP_ESCRITA):
            base = _ENDERECO_KEYMAP_BASE[perfil]
            for botao, idx in enumerate(linha):
                certo = base + 0x04 * botao
                b3[idx][2] = certo & 0xFF
                b3[idx][3] = (certo >> 8) & 0xFF

        # Aplica para cada perfil i = 0..4 (perfil i+1)
        for i in range(5):
            perfil = estado["perfis"][i]

            # Scrollspeed: b2[8 + 2*i]
            b2[8 + 2 * i] = perfil["scrollspeed"]

            # LED
            cor_bytes = codigo.cor_para_bytes(perfil["led"]["cor"])
            modo_bytes = codigo.modo_para_bytes(perfil["led"]["modo"])
            velocidade = perfil["led"]["velocidade"]
            brilho = perfil["led"]["brilho"]

            # b1[3 + 2*i][8..10] = cor
            b1[3 + 2 * i][8:11] = list(cor_bytes)
            # b1[3 + 2*i][11], b1[3+2*i][13] = modo
            b1[3 + 2 * i][11] = modo_bytes[0]
            b1[3 + 2 * i][13] = modo_bytes[1]
            # b1[3 + 2*i][12] = velocidade
            b1[3 + 2 * i][12] = velocidade
            # b1[4 + 2*i][8] = brilho
            b1[4 + 2 * i][8] = brilho

            # DPI: escreve direto no quadro cujo ENDEREÇO é o do perfil e
            # nível, em vez de confiar na posição no template.
            #
            # O template do upstream (_c_data_settings_3) traz os quadros de
            # DPI agrupados por NÍVEL (os 5 perfis do nível 1, depois os do
            # nível 2...) e com endereço base+2. Medido no M711, o mouse
            # lê por PERFIL, nos endereços base + 6*nível — então usar a
            # ordem do template faz cada perfil receber o DPI do anterior.
            # Ver _ENDERECO_DPI.
            for j in range(5):
                dpi = perfil["dpi"][j]
                idx = _ENDERECO_DPI[i][j]
                x_lo, x_hi = codigo.dpi_para_bytes(dpi["x"])
                y_lo, y_hi = codigo.dpi_para_bytes(dpi["y"])
                b3[idx][8] = 1 if dpi["ativo"] else 0
                b3[idx][9] = x_lo
                b3[idx][10] = x_hi
                b3[idx][11] = y_lo
                b3[idx][12] = y_hi

            # Keymap: mesma armadilha de endereço do DPI — resolvido pelo
            # endereço real (ver _ENDERECO_KEYMAP_ESCRITA), não pela
            # posição no template.
            #
            # NOTA: o texto do botão não preserva os 4 bytes. Uma macro
            # `macro1:while` decodifica de `91 80 ff ff` mas re-codifica
            # para `91 80 00 00`, porque o texto carrega só o slot e a
            # variante. Escrever por texto portanto perde os bytes b2/b3.
            # Por isso a escrita de keymap está bloqueada na Fase 1 (ver
            # Mouse.aplicar) e `verificar` acusa a perda. A correção é da
            # Fase 2: guardar o keycode cru de 4 bytes no estado, junto do
            # texto, e escrever o cru.
            for j in range(10):
                nome_botao = codigo.NOMES_BOTAO[j]
                key_bytes = codigo.texto_para_botao(perfil["botoes"][nome_botao])
                b3[_ENDERECO_KEYMAP_ESCRITA[i][j]][8:12] = list(key_bytes)

            # Report rate: perfis 1..3 em b1[13][8+2*i], perfis 4..5 em b1[14][2+2*i]
            rr_byte = codigo.pollrate_para_bytes(perfil["report_rate"])
            if i < 3:
                b1[13][8 + 2 * i] = rr_byte
            else:
                b1[14][2 + 2 * i] = rr_byte

        # Achata
        b1_flat = bytes(b for q in b1 for b in q)
        b2_flat = bytes(b2)
        b3_flat = bytes(b for q in b3 for b in q)

        return (b1_flat, b2_flat, b3_flat)

    # ------------------------------------------------------------------
    # Escrita
    # ------------------------------------------------------------------

    def enviar(self, b1: bytes, b2: bytes, b3: bytes) -> None:
        """Envia os 3 buffers para o mouse na ordem exata.

        1. Destrava o mouse (f5 00)
        2. 15 quadros de 16 bytes (wValue=0x0302)
        3. Um único envio de 64 bytes (wValue=0x0302)
        4. 90 quadros de 16 bytes (wValue=0x0302)
        5. Retrava o mouse (f5 01)

        O par destravar/retravar é obrigatório: deixar o mouse destravado
        depois de escrever o deixa instável.
        """
        dev = self._exige_aberto()

        self._travar(False)
        try:
            # 1. 15 quadros de 16 bytes
            for i in range(15):
                dev.set(b1[i * 16:(i + 1) * 16], usb.REPORT_2, 16)

            # 2. Um único envio de 64 bytes
            dev.set(b2, usb.REPORT_2, 64)

            # 3. 90 quadros de 16 bytes
            for i in range(90):
                dev.set(b3[i * 16:(i + 1) * 16], usb.REPORT_2, 16)
        finally:
            self._travar(True)

    def aplicar(self, alterar: dict[str, Any]) -> dict[str, Any]:
        """Aplica um patch ao estado e calcula os buffers resultantes.

        Esta função **não envia nada** para o mouse. Ela:
        1. Lê o estado atual
        2. Aplica o patch (apenas LED — DPI, scrollspeed, report_rate e
           keymap são bloqueados nesta fase)
        3. Monta os novos buffers
        4. Calcula o diff

        Args:
            alterar: dict parcial, ex.: ``{"perfis": {1: {"led": {"cor": "00ff00"}}}}``
        Returns:
            dict com ``antes``, ``depois``, ``b1``, ``b2``, ``b3``,
            ``diff`` e ``simulado``.
        """
        # 1. Lê o estado atual
        bruto_antes = self.ler_bruto()
        estado = self.decodifica(bruto_antes)

        # 2. Aplica o patch em uma cópia profunda
        estado_novo = copy.deepcopy(estado)
        self._valida_e_aplica_patch(estado_novo, alterar)

        # 3. Monta os novos buffers
        b1, b2, b3 = self.monta(estado_novo)

        # 4. Calcula o diff dos TRÊS buffers.
        # Só o b1 não serve: o LED fica no b1, mas scrollspeed no b2 e
        # DPI + keymap no b3. Comparar só o b1 fazia toda alteração de DPI
        # aparecer como "nenhum byte muda" no dry-run — que é exatamente o
        # que a trava de segurança deve mostrar.
        diff = self._calcula_diff3(estado, estado_novo)

        return {
            "antes": estado,
            "depois": estado_novo,
            "b1": b1,
            "b2": b2,
            "b3": b3,
            "diff": diff,
            "simulado": True,
        }

    # ------------------------------------------------------------------
    # Métodos auxiliares
    # ------------------------------------------------------------------

    @staticmethod
    def _calcula_diff3(estado_antes: dict[str, Any],
                       estado_depois: dict[str, Any]
                       ) -> list[tuple[str, int, int, int]]:
        """Diff dos 3 buffers entre dois estados.

        Monta os dois estados e compara byte a byte. Precisa comparar os
        TRÊS porque cada um carrega parte da configuração:

        - ``b1``: LED (cor, modo, brilho, velocidade) e poll rate
        - ``b2``: scrollspeed
        - ``b3``: DPI e keymap

        Comparar só o ``b1`` esconde toda alteração de DPI, que é o que
        acontece se alguém rodar o dry-run e acreditar que "nenhum byte
        muda" quer dizer que nada vai ser gravado.

        Retorna lista de (rótulo, offset, antes, depois).
        """
        antes = Mouse.monta(estado_antes)
        depois = Mouse.monta(estado_depois)
        rotulos = ("b1", "b2", "b3")
        diff: list[tuple[str, int, int, int]] = []
        for rotulo, buf_antes, buf_depois in zip(rotulos, antes, depois):
            tam = min(len(buf_antes), len(buf_depois))
            for i in range(tam):
                if buf_antes[i] != buf_depois[i]:
                    diff.append((rotulo, i, buf_antes[i], buf_depois[i]))
        return diff

    @staticmethod
    def _valida_e_aplica_patch(estado: dict[str, Any], alterar: dict[str, Any]) -> None:
        """Valida e aplica o patch ao estado (in-place).

        Permitidos: LED (cor, modo, brilho, velocidade) e DPI.

        Bloqueados: scrollspeed, report_rate e keymap. O scrollspeed e o
        report_rate não foram medidos na escrita; o keymap perde bytes
        porque o texto do botão não carrega os 4 bytes do keycode (ver
        `monta`).
        """
        # Ainda bloqueados (ver nota no docstring)
        chaves_bloqueadas = {"scrollspeed", "report_rate", "botoes"}
        for perfil_patch in alterar.get("perfis", {}).values():
            for chave in perfil_patch:
                if chave in chaves_bloqueadas:
                    raise ErroMouse(
                        f"Alteração de '{chave}' ainda está bloqueada. "
                        f"Nesta fase, apenas LED e DPI são permitidos."
                    )

        # Aplica alterações de LED
        for perfil_num, perfil_patch in alterar.get("perfis", {}).items():
            idx = perfil_num - 1
            if idx < 0 or idx >= 5:
                raise ErroMouse(f"Perfil inválido: {perfil_num}")

            if "led" in perfil_patch:
                led = estado["perfis"][idx]["led"]
                novo_led = perfil_patch["led"]

                if "cor" in novo_led:
                    # Valida a cor
                    codigo.cor_para_bytes(novo_led["cor"])  # levanta ErroCodigo se inválida
                    led["cor"] = novo_led["cor"].lower()

                if "modo" in novo_led:
                    # Valida o modo
                    modo_can = codigo.normaliza_modo(novo_led["modo"])
                    led["modo"] = modo_can

                if "brilho" in novo_led:
                    brilho = novo_led["brilho"]
                    lo, hi = dados.LIMITS["brilho"]
                    if not (lo <= brilho <= hi):
                        raise ErroMouse(
                            f"Brilho inválido: {brilho}. Válido: {lo}..{hi}"
                        )
                    led["brilho"] = brilho

                if "velocidade" in novo_led:
                    vel = novo_led["velocidade"]
                    lo, hi = dados.LIMITS["velocidade"]
                    if not (lo <= vel <= hi):
                        raise ErroMouse(
                            f"Velocidade inválida: {vel}. Válido: {lo}..{hi}"
                        )
                    led["velocidade"] = vel

            if "dpi" in perfil_patch:
                Mouse._aplica_patch_dpi(
                    estado["perfis"][idx], perfil_patch["dpi"], idx + 1)

    NIVEIS_DPI = codigo.NIVEIS_DPI

    @classmethod
    def _aplica_patch_dpi(cls, perfil: dict[str, Any], patch: dict[str, Any],
                          num_perfil: int) -> None:
        """Aplica um patch de DPI a um perfil, validando tudo antes.

        Aceita duas formas:

        - ``{"nivel": 3, "valor": 1600, "ativo": True}`` — um nível só
        - ``{"valores": [400, 800, 1600, 3200, 6400]}`` — os 5 de uma vez,
          mais ``ativos`` opcionalmente para desativar níveis

        O firmware quantiza o DPI em degraus de ~22 (pedir 500 grava 489),
        então o valor gravado pode não ser exatamente o pedido. Isso é
        normal e o cli avisa; aqui só gravamos o mais próximo.
        """
        niveis = perfil["dpi"]

        if "valores" in patch:
            valores = patch["valores"]
            if not isinstance(valores, (list, tuple)):
                raise ErroMouse("'valores' tem que ser uma lista de 5 DPI")
            if len(valores) != cls.NIVEIS_DPI:
                raise ErroMouse(
                    f"'valores' tem {len(valores)} itens, "
                    f"esperado {cls.NIVEIS_DPI}")
            ativos = patch.get("ativos")
            if ativos is not None and (
                    not isinstance(ativos, (list, tuple))
                    or len(ativos) != cls.NIVEIS_DPI):
                raise ErroMouse(
                    f"'ativos' tem que ser uma lista de {cls.NIVEIS_DPI} "
                    f"booleanos")
            for j, valor in enumerate(valores):
                if valor is None:
                    continue
                nivel = dict(niveis[j])
                nivel["x"] = nivel["y"] = valor
                nivel["ativo"] = True if ativos is None else bool(ativos[j])
                codigo.dpi_para_bytes(nivel["x"])  # valida a faixa
                niveis[j] = nivel
        else:
            j = patch.get("nivel")
            if j is None:
                raise ErroMouse(
                    "informe 'nivel' (1..5) ou 'valores' (lista de 5)")
            if not isinstance(j, int) or not 1 <= j <= cls.NIVEIS_DPI:
                raise ErroMouse(
                    f"nível deve ser 1..{cls.NIVEIS_DPI} (veio {j})")
            nivel = dict(niveis[j - 1])
            if "valor" in patch:
                nivel["x"] = patch["valor"]
                nivel["y"] = patch["valor"]
            if "x" in patch:
                nivel["x"] = patch["x"]
            if "y" in patch:
                nivel["y"] = patch["y"]
            if "ativo" in patch:
                nivel["ativo"] = bool(patch["ativo"])
            # O firmware IGNORA o eixo Y: gravar X=800 Y=400 resulta em
            # X=800 Y=0 na releitura (medido em 2026-09-29, confirmado pelo
            # driver C++ que lê dpi5=0x12000000, ou seja Y zerado). O
            # registro tem os 4 bytes, mas o firmware só usa o X.
            # Aceitar X!=Y seria prometer algo que o hardware não faz.
            if nivel["x"] != nivel["y"]:
                raise ErroMouse(
                    f"O M711 não tem DPI assimétrico: o firmware aceita e usa "
                    f"somente o eixo X. Pediu X={nivel['x']} Y={nivel['y']}, "
                    f"mas o Y seria descartado (medido: fica 0). "
                    f"Use --valor {nivel['x']} para os dois eixos.")
            codigo.dpi_para_bytes(nivel["x"])  # valida a faixa
            niveis[j - 1] = nivel

        # O mouse precisa de pelo menos um nível ativo para ter DPI usável.
        # Mesmo o software oficial e o driver C++ recusam desativar o último.
        if sum(1 for n in niveis if n["ativo"]) == 0:
            raise ErroMouse(
                f"perfil {num_perfil}: não dá para desativar todos os "
                f"{cls.NIVEIS_DPI} níveis de DPI — o mouse ficaria sem "
                f"sensibilidade. Deixe pelo menos um ativo.")

    # ------------------------------------------------------------------
    # Leitura completa (atalho)
    # ------------------------------------------------------------------

    def ler(self) -> dict[str, Any]:
        """Lê e decodifica o estado completo do mouse.

        Retorna o mesmo dict que `decodifica(ler_bruto())`.
        """
        return self.decodifica(self.ler_bruto())
