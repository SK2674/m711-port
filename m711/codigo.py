"""Tabelas semânticas e conversões do protocolo do Redragon M711.

Deriva nomes, fórmulas e constantes de ``dados.py`` (que é gerado do C++).
Aqui ficam: modos de LED, taxa de poll, DPI, keycodes de botão e cores.
"""

from __future__ import annotations

import dados

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

PERFIS = 5
NIVEIS_DPI = 5
FATOR_DPI = 10000 / 225  # 44.4444...

NOMES_BOTAO = [
    "left",
    "right",
    "middle",
    "forward",
    "backward",
    "dpi_up",
    "dpi_down",
    "lightmode",
    "scroll_up",
    "scroll_down",
]


class ErroCodigo(ValueError):
    """Valor inválido para o protocolo (DPI fora da faixa, modo desconhecido, etc.)."""


# ---------------------------------------------------------------------------
# Modos de luz
# ---------------------------------------------------------------------------

# Nomes canônicos em português → enum do C++
_CANONICO_PARA_ENUM = {
    "off": "lightmode_off",
    "estatico": "lightmode_static",
    "respirar": "lightmode_breathing",
    "arcoiris": "lightmode_rainbow",
    "piscar": "lightmode_flashing",
    "onda": "lightmode_wave",
    "botao": "lightmode_reactive_button",
    "aleatorio": "lightmode_random",
    "rastro": "lightmode_alternating",
    "reativo": "lightmode_reactive",
    "respirar_arcoiris": "lightmode_breathing_rainbow",
}

# Aliases aceitos na entrada (canônico + inglês do C++ + sem acento)
_ALIASES_MODO = {
    "off": "off",
    "estatico": "estatico",
    "static": "estatico",
    "respirar": "respirar",
    "breathing": "respirar",
    "arcoiris": "arcoiris",
    "rainbow": "arcoiris",
    "piscar": "piscar",
    "flashing": "piscar",
    "onda": "onda",
    "wave": "onda",
    "botao": "botao",
    "reactive_button": "botao",
    "aleatorio": "aleatorio",
    "random": "aleatorio",
    # "alternating" é o modo que o software oficial chama de
    # "GO WITHOUT TRACE" (a luz deixa um rastro). `rastro` é o nome
    # canônico; `alternar` fica como alias antigo.
    "rastro": "rastro",
    "alternar": "rastro",
    "alternating": "rastro",
    "go_without_trace": "rastro",
    "reativo": "reativo",
    "reactive": "reativo",
    "respirar_arcoiris": "respirar_arcoiris",
    "breathing_rainbow": "respirar_arcoiris",
}

# Constrói MODOS e MODOS_INV a partir de dados.LIGHTMODE_VALUES
MODOS: dict[str, tuple[int, int]] = {}
for (b11, b13), enum_name in dados.LIGHTMODE_VALUES.items():
    # enum_name é "lightmode_xxx" ou "xxx" dependendo do parser
    if enum_name.startswith("lightmode_"):
        nome_enum = enum_name[len("lightmode_"):]
    else:
        nome_enum = enum_name
    # Encontra o nome canônico em português
    for can, enum_cpp in _CANONICO_PARA_ENUM.items():
        if enum_cpp == f"lightmode_{nome_enum}" or enum_cpp == nome_enum:
            MODOS[can] = (b11, b13)
            break
    else:
        # Fallback: usa o próprio nome do enum
        MODOS[nome_enum] = (b11, b13)

MODOS_INV: dict[tuple[int, int], str] = {v: k for k, v in MODOS.items()}

# Velocidades válidas por modo (medido/documentado)
VELOCIDADES: dict[str, tuple[int, ...]] = {
    "off": (0,),
    "estatico": (0,),
    "respirar": tuple(range(1, 9)),
    "arcoiris": tuple(range(1, 9)),
    "piscar": tuple(range(1, 9)),
    "onda": tuple(range(0, 8)),
    "botao": tuple(range(0, 8)),
    "aleatorio": tuple(range(0, 8)),
    "rastro": tuple(range(0, 8)),
    "reativo": tuple(range(0, 8)),
    "respirar_arcoiris": tuple(range(0, 8)),
}


def normaliza_modo(nome: str) -> str:
    """Converte qualquer alias aceito para o nome canônico em português."""
    chave = nome.strip().lower().replace(" ", "_")
    if chave not in _ALIASES_MODO:
        raise ErroCodigo(f"Modo de luz desconhecido: '{nome}'. Válidos: {', '.join(sorted(MODOS))}")
    return _ALIASES_MODO[chave]


def modo_para_bytes(nome: str) -> tuple[int, int]:
    """Nome canônico → (byte11, byte13)."""
    can = normaliza_modo(nome)
    return MODOS[can]


def bytes_para_modo(b11: int, b13: int) -> str:
    """(byte11, byte13) → nome canônico, ou '?' se desconhecido."""
    return MODOS_INV.get((b11, b13), "?")


# ---------------------------------------------------------------------------
# Taxa de poll
# ---------------------------------------------------------------------------

# {Hz: byte} a partir de dados.REPORT_RATE_VALUES
POLLRATE: dict[int, int] = {hz: b for b, hz in dados.REPORT_RATE_VALUES.items()}


def pollrate_para_bytes(hz: int) -> int:
    """Hz → byte do protocolo."""
    if hz not in POLLRATE:
        raise ErroCodigo(f"Taxa de poll inválida: {hz} Hz. Válidas: {', '.join(str(h) for h in sorted(POLLRATE))}")
    return POLLRATE[hz]


def bytes_para_pollrate(b: int) -> int | None:
    """Byte do protocolo → Hz, ou None se desconhecido."""
    return dados.REPORT_RATE_VALUES.get(b)


# ---------------------------------------------------------------------------
# DPI — fórmula real, não a tabela errada do C++
# ---------------------------------------------------------------------------

_LO_MIN, _LO_MAX = dados.LIMITS["dpi"]
_HI_MIN, _HI_MAX = dados.LIMITS["dpi_alto"]


def bytes_para_dpi(lo: int, hi: int) -> int:
    """(byte_lo, byte_hi) → DPI real. Fórmula: lo * 44.4444... * 2**hi."""
    return round(lo * FATOR_DPI * (2 ** hi))


def dpi_para_bytes(dpi: int) -> tuple[int, int]:
    """DPI real → (byte_lo, byte_hi) que minimiza o erro."""
    if dpi < 50 or dpi > 10000:
        raise ErroCodigo(f"DPI fora da faixa suportada (50..10000): {dpi}")
    melhor: tuple[int, int] | None = None
    melhor_erro: float = float("inf")
    for hi in range(_HI_MIN, _HI_MAX + 1):
        for lo in range(_LO_MIN, _LO_MAX + 1):
            candidato = bytes_para_dpi(lo, hi)
            erro = abs(candidato - dpi)
            if erro < melhor_erro:
                melhor_erro = erro
                melhor = (lo, hi)
    if melhor is None:
        raise ErroCodigo(f"Não encontrou par (lo,hi) para DPI {dpi}")
    return melhor


def dpi_para_texto(dpi: int) -> str:
    """DPI → string legível ("400" se X==Y, senão "X400Y400")."""
    x, y = dpi if isinstance(dpi, tuple) else (dpi, dpi)
    if x == y:
        return str(x)
    return f"X{x}Y{y}"


def texto_para_dpi(texto: str) -> tuple[int, int]:
    """String → (X, Y). Aceita "800" (X==Y) ou "X800Y400"."""
    t = texto.strip().upper()
    if t.startswith("X") and "Y" in t:
        partes = t[1:].split("Y", 1)
        return (int(partes[0]), int(partes[1]))
    val = int(t)
    return (val, val)


# ---------------------------------------------------------------------------
# Botões / keycodes
# ---------------------------------------------------------------------------

# Nome da chave no .ini → nome canônico em dados.KEYCODES
ALIASES_BOTAO: dict[str, str] = {
    "button_left": "left",
    "button_right": "right",
    "button_middle": "middle",
    "button_forward": "forward",
    "button_backward": "backward",
    "button_dpi_up": "dpi+",
    "button_dpi_down": "dpi-",
    "button_lightmode": "led_mode_switch",
    "scroll_up": "scroll_up",
    "scroll_down": "scroll_down",
    # Nomes curtos (sem prefixo button_) usados no estado interno
    "dpi_up": "dpi+",
    "dpi_down": "dpi-",
    "lightmode": "led_mode_switch",
}

# Inverte KEYCODES para busca por valor
_KEYCODES_INV: dict[tuple[int, int, int, int], str] = {}
for _nome, _quad in dados.KEYCODES.items():
    _KEYCODES_INV[_quad] = _nome

# Inverte TECLAS para busca por HID usage
_TECLAS_INV: dict[int, str] = {v: k for k, v in dados.TECLAS.items()}

# Ordem dos modificadores para saída canônica
_ORDEM_MOD = ["ctrl_l+", "shift_l+", "alt_l+", "super_l+", "ctrl_r+", "shift_r+", "alt_r+", "super_r+"]


def botao_para_texto(b4: tuple[int, int, int, int]) -> str:
    """4 bytes do protocolo → nome canônico legível."""
    b0, b1, b2, b3 = b4

    if b4 == (0, 0, 0, 0):
        return "none"

    # Fire (0x99): fire:tecla:repeticoes:delay
    if b0 == 0x99:
        tecla = _TECLAS_INV.get(b1, f"0x{b1:02x}")
        return f"fire:{tecla}:{b2}:{b3}"

    # Fire + botão esquerdo (0x92)
    if b0 == 0x92:
        return "fire:mouse_left"

    # Snipe (0x9a 0x01 dd dd)
    if b0 == 0x9A and b1 == 0x01:
        dpi = b2 | (b3 << 8)
        return f"snipe:{dpi}"

    # Macro (0x91)
    if b0 == 0x91:
        if 0x40 <= b1 <= 0x4E:
            slot = b1 - 0x3F
            return f"macro{slot}:until"
        if 0x80 <= b1 <= 0x8E:
            slot = b1 - 0x7F
            return f"macro{slot}:while"
        if b1 <= 0x0E:
            if b2 >= 1:
                return f"macro{b1 + 1}"
            return f"macro{b1 + 1}:repeticoes={b2}:{b3}"

    # Tecla de teclado (0x90)
    if b0 == 0x90:
        tecla = _TECLAS_INV.get(b2, f"0x{b2:02x}")
        return tecla

    # Modificadores + tecla (0x8f)
    if b0 == 0x8F:
        mods = [m for m in _ORDEM_MOD if b1 & dados.MODIFICADORES.get(m, 0)]
        tecla = _TECLAS_INV.get(b2, f"0x{b2:02x}")
        # Remove o + do final de cada modificador para join correto
        mods_clean = [m.rstrip("+") for m in mods]
        return "+".join(mods_clean + [tecla]) if mods_clean else tecla

    # Casa exata em KEYCODES
    if b4 in _KEYCODES_INV:
        return _KEYCODES_INV[b4]

    return f"bruto:{b0:02x} {b1:02x} {b2:02x} {b3:02x}"


def texto_para_botao(texto: str) -> tuple[int, int, int, int]:
    """Nome canônico legível → 4 bytes do protocolo."""
    t = texto.strip()

    if t == "none":
        return (0, 0, 0, 0)

    # Fire
    if t.startswith("fire:"):
        partes = t.split(":", 3)
        if len(partes) == 4:
            _, tecla, reps, delay = partes
            b1 = dados.TECLAS.get(tecla, 0)
            return (0x99, b1, int(reps), int(delay))
        if len(partes) == 2 and partes[1] == "mouse_left":
            return (0x92, 0, 0, 0)
        raise ErroCodigo(f"Formato fire inválido: '{texto}'")

    # Snipe
    if t.startswith("snipe:"):
        dpi = int(t.split(":", 1)[1])
        return (0x9A, 0x01, dpi & 0xFF, (dpi >> 8) & 0xFF)

    # Macro. Formatos aceitos (os mesmos que botao_para_texto produz):
    #   macroN                    -> (0x91, N-1, 1, 0)
    #   macroN:repeticoes=X:Y     -> (0x91, N-1, X, Y)
    #   macroN:until              -> (0x91, 0x40 + N-1, 0, 0)
    #   macroN:while              -> (0x91, 0x80 + N-1, 0, 0)
    if t.startswith("macro"):
        corpo = t[5:]
        slot_txt, _, resto = corpo.partition(":")
        slot = int(slot_txt)
        if resto == "until":
            return (0x91, 0x40 + slot - 1, 0, 0)
        if resto == "while":
            return (0x91, 0x80 + slot - 1, 0, 0)
        if resto.startswith("repeticoes="):
            partes = resto[len("repeticoes="):].split(":")
            return (0x91, slot - 1, int(partes[0]), int(partes[1]))
        if resto:
            raise ErroCodigo(
                f"Variante de macro desconhecida: '{texto}'. "
                f"Use macroN, macroN:until, macroN:while ou "
                f"macroN:repeticoes=X:Y")
        return (0x91, slot - 1, 1, 0)

    # ALIASES_BOTAO (ex.: button_dpi_up -> dpi+)
    if t in ALIASES_BOTAO and ALIASES_BOTAO[t] in dados.KEYCODES:
        return dados.KEYCODES[ALIASES_BOTAO[t]]

    # Nome canônico em KEYCODES
    if t in dados.KEYCODES:
        return dados.KEYCODES[t]

    # Modificadores + tecla (ex.: ctrl_l+a ou ctrl_l++a)
    if "+" in t:
        partes = t.split("+")
        # Remove strings vazias causadas por ++ (ex.: ctrl_l++a -> ['ctrl_l', '', 'a'])
        partes = [p for p in partes if p]
        if len(partes) < 2:
            raise ErroCodigo(f"Formato de modificador inválido: '{texto}'")
        mods = 0
        for p in partes[:-1]:
            # Aceita tanto 'ctrl_l' quanto 'ctrl_l+' (com ou sem o + no nome)
            key = p if p in dados.MODIFICADORES else p + "+"
            if key not in dados.MODIFICADORES:
                raise ErroCodigo(f"Modificador desconhecido: '{p}'")
            mods |= dados.MODIFICADORES[key]
        tecla_nome = partes[-1]
        if tecla_nome not in dados.TECLAS:
            raise ErroCodigo(f"Tecla desconhecida: '{tecla_nome}'")
        return (0x8F, mods, dados.TECLAS[tecla_nome], 0)

    raise ErroCodigo(f"Botão/tecla desconhecido: '{texto}'")


# ---------------------------------------------------------------------------
# Cores
# ---------------------------------------------------------------------------

_CORES_NOMES = {
    "vermelho": (255, 0, 0),
    "verde": (0, 255, 0),
    "azul": (0, 0, 255),
    "amarelo": (255, 255, 0),
    "ciano": (0, 255, 255),
    "magenta": (255, 0, 255),
    "branco": (255, 255, 255),
    "preto": (0, 0, 0),
    "laranja": (255, 165, 0),
    "rosa": (255, 192, 203),
    "roxo": (128, 0, 128),
    "cinza": (128, 128, 128),
}


def cor_para_bytes(texto: str) -> tuple[int, int, int]:
    """Hex RRGGBB ou nome de cor → (R, G, B)."""
    t = texto.strip().lstrip("#").lower()
    if t in _CORES_NOMES:
        return _CORES_NOMES[t]
    if len(t) == 6:
        try:
            r = int(t[0:2], 16)
            g = int(t[2:4], 16)
            b = int(t[4:6], 16)
            return (r, g, b)
        except ValueError:
            pass
    raise ErroCodigo(f"Cor inválida: '{texto}'. Use RRGGBB ou nome: {', '.join(sorted(_CORES_NOMES))}")


def bytes_para_cor(r: int, g: int, b: int) -> str:
    """(R, G, B) → hex RRGGBB minúsculo."""
    return f"{r:02x}{g:02x}{b:02x}"


def cor_nome_para_rgb(texto: str) -> tuple[int, int, int]:
    """Nome de cor → (R, G, B)."""
    t = texto.strip().lower()
    if t not in _CORES_NOMES:
        raise ErroCodigo(f"Cor desconhecida: '{texto}'. Válidas: {', '.join(sorted(_CORES_NOMES))}")
    return _CORES_NOMES[t]
