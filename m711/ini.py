"""Leitura e escrita de arquivos .ini no formato do driver mouse_m908 (M711).

Este módulo converte entre o dicionário de estado definido na seção 4 do
ARQUITETURA.md e o arquivo de configuração texto usado pelo driver C++
``dokutan/mouse_m908`` (veja ``port/mouse_m908/examples/example_m711.ini``).

Formato (idêntico ao do driver C++):
    - Seções ``[profile1]`` .. ``[profile5]``;
    - Chaves ``lightmode``, ``color``, ``brightness``, ``speed``,
      ``scrollspeed``, ``dpi1_enable``..``dpi5_enable``, ``dpi1``..``dpi5``,
      ``report_rate`` e as 10 chaves de botão
      (``button_left``..``button_scroll_down``);
    - Macros em seções ``;## macroN`` (1..15). Nesta fase o bruto da macro é
      gravado como uma única linha ``;# bruto=<hex>`` — a sequência de
      ações ``;# down<TAB>tecla`` do C++ é aceita na leitura (é dado, não
      comentário), mas não é emitida;
    - Comentários com ``#`` e ``;``.

O parser é minimalista e tolerante (aceita ``=`` ou ``:`` como separador,
ignora linhas em branco e comentários no início da linha).  Somente a
sintaxe é validada aqui — a validação de faixa é responsabilidade de
``codigo.py`` e acontece na escrita.

Compatibilidade com o driver C++: a saída de ``para_texto`` pode ser
enviada ao mouse com ``mouse_m908 -c <arquivo>``; os valores padrão
quando uma chave está ausente são os mesmos de
``port/mouse_m908/include/m711/constructor.cpp``.
"""

from __future__ import annotations

import re

import codigo
import dados

__all__ = [
    "ErroIni",
    "DPI_PADRAO",
    "para_texto",
    "de_texto",
    "ler_arquivo",
    "salvar_arquivo",
]

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

PERFIS = 5
NIVEIS_DPI = 5
MACROS = 15

# Valores padrão — transcritos de include/m711/constructor.cpp (não inventar).
# _s_colors {0xff, 0xff, 0xff}, _s_lightmodes lightmode_static,
# _s_brightness_levels 0x03, _s_speed_levels 0x08, _s_scrollspeeds 0x01,
# _s_report_rates r_125Hz, _s_dpi_enabled {true x5}.
COR_PADRAO = "ffffff"
MODO_PADRAO = "estatico"
BRILHO_PADRAO = 3
VELOCIDADE_PADRAO = 8
SCROLLSPEED_PADRAO = 1
REPORTRATE_PADRAO = 125  # r_125Hz

# _s_dpi_levels do constructor.cpp, em bytes: {lo, hi} para X e Y iguais.
# Convertidos para DPI real com a fórmula de codigo.py (bytes_para_dpi),
# que é a fórmula correta — não a tabela DPI_CODES_UPSTREAM do C++.
_DPI_PADRAO_BYTES = ((0x04, 0x00), (0x16, 0x00), (0x2d, 0x00), (0x43, 0x00), (0x8c, 0x00))
DPI_PADRAO = [codigo.bytes_para_dpi(lo, hi) for lo, hi in _DPI_PADRAO_BYTES]

# Ordem de emissão das chaves de botão — a mesma de examples/example_m711.ini.
_ORDEM_BOTOES = (
    "button_left", "button_right", "button_middle",
    "button_backward", "button_forward",
    "button_dpi_up", "button_dpi_down",
    "button_lightmode", "scroll_up", "scroll_down",
)

# Chave do .ini → chave do estado (nomes de codigo.NOMES_BOTAO, sem prefixo).
_INI_PARA_ESTADO = {
    "button_left": "left",
    "button_right": "right",
    "button_middle": "middle",
    "button_backward": "backward",
    "button_forward": "forward",
    "button_dpi_up": "dpi_up",
    "button_dpi_down": "dpi_down",
    "button_lightmode": "lightmode",
    "scroll_up": "scroll_up",
    "scroll_down": "scroll_down",
}

# Chaves aceitas dentro de [profileN].
_CHAVES_PERFIL = (
    {"lightmode", "color", "brightness", "speed", "scrollspeed", "report_rate"}
    | {f"dpi{n}_enable" for n in range(1, NIVEIS_DPI + 1)}
    | {f"dpi{n}" for n in range(1, NIVEIS_DPI + 1)}
    | set(_ORDEM_BOTOES)
)

# Valores booleanos aceitos para dpiN_enable (case-insensitive).
_VERDADEIROS = {"true", "1", "sim", "yes"}
_FALSOS = {"false", "0", "nao", "no"}

# O C++ imprime "# Currently active profile: N" no cabeçalho (helpers.cpp e
# readers.cpp).  Mantemos a string em inglês por compatibilidade com o driver.
_RE_PERFIL_ATIVO = re.compile(r"Currently active profile:\s*(\d+)")
_RE_LINHA_CHAVE = re.compile(r"([^=:]+)[=:](.*)")


class ErroIni(ValueError):
    """Arquivo .ini inválido (sintaxe, chave desconhecida, seção ou perfil)."""


# ---------------------------------------------------------------------------
# Parser minimalista
# ---------------------------------------------------------------------------

def _parse(texto: str):
    """Quebra o .ini em partes brutas.

    Retorna ``(perfil_ativo, secoes, macros)`` onde:
      - ``perfil_ativo``: int 1..5 (do comentário "# Currently active
        profile: N"; 1 se ausente);
      - ``secoes``: ``{numero_do_perfil: {chave: (valor, linha)}}``;
      - ``macros``: ``{slot: [linhas de dados]}`` — as linhas ``;# ...``
        dentro de ``;## macroN``, sem o prefixo ``;# ``.
    """
    perfil_ativo = 1
    secoes: dict[int, dict[str, tuple[str, int]]] = {}
    macros: dict[int, list[str]] = {}
    onde = None  # ("perfil", n) ou ("macro", slot)
    for n, linha in enumerate(texto.splitlines(), 1):
        s = linha.strip()
        if not s:
            continue
        if s.startswith("[") and s.endswith("]"):
            nome = s[1:-1]
            if not (nome.startswith("profile") and nome[len("profile"):].isdigit()):
                raise ErroIni(f"linha {n}: seção inválida: '[{nome}]'")
            num = int(nome[len("profile"):])
            if not 1 <= num <= PERFIS:
                raise ErroIni(f"linha {n}: perfil fora de 1..{PERFIS}: {num}")
            onde = ("perfil", num)
            secoes[num] = {}
        elif s.startswith(";##"):
            # Seção de macro: ";## macroN" (1..15).
            resto = s[len(";##"):].strip()
            if not (resto.startswith("macro") and resto[len("macro"):].isdigit()):
                raise ErroIni(f"linha {n}: seção de macro inválida: '{s}'")
            slot = int(resto[len("macro"):])
            if not 1 <= slot <= MACROS:
                raise ErroIni(f"linha {n}: macro fora de 1..{MACROS}: {slot}")
            onde = ("macro", slot)
            macros.setdefault(slot, [])
        elif s.startswith(";#"):
            # Dentro de uma macro, ";# ..." é dado (ex.: ";# down<TAB>tecla").
            # Fora de macro é comentário comum.
            if onde is not None and onde[0] == "macro":
                macros[onde[1]].append(s[len(";#"):].lstrip())
        elif s.startswith("#") or s.startswith(";"):
            m = _RE_PERFIL_ATIVO.search(s)
            if m:
                perfil_ativo = int(m.group(1))
                if not 1 <= perfil_ativo <= PERFIS:
                    raise ErroIni(
                        f"linha {n}: perfil ativo fora de 1..{PERFIS}: {perfil_ativo}"
                    )
        elif onde is not None and onde[0] == "perfil":
            m = _RE_LINHA_CHAVE.match(s)
            if m is None:
                raise ErroIni(f"linha {n}: esperado 'chave=valor' ou 'chave:valor': '{s}'")
            chave = m.group(1).strip()
            valor = m.group(2).strip()
            secoes[onde[1]][chave] = (valor, n)
        else:
            raise ErroIni(f"linha {n}: chave fora de seção: '{s}'")
    return perfil_ativo, secoes, macros


# ---------------------------------------------------------------------------
# Auxiliares de conversão (ErroIni com número da linha)
# ---------------------------------------------------------------------------

def _int(texto: str, linha: int) -> int:
    """Inteiro decimal, ou hexadecimal se prefixado com 0x/0X (como o C++)."""
    t = texto.strip()
    try:
        return int(t, 16) if t.lower().startswith("0x") else int(t)
    except ValueError:
        raise ErroIni(f"linha {linha}: valor não numérico: '{texto}'")


def _bool(texto: str, linha: int) -> bool:
    """Booleano tolerante: true/false, 1/0, sim/nao, yes/no (case-insensitive)."""
    t = texto.strip().lower()
    if t in _VERDADEIROS:
        return True
    if t in _FALSOS:
        return False
    raise ErroIni(
        f"linha {linha}: valor booleano inválido: '{texto}' "
        "(use true/false, 1/0, sim/nao, yes/no)"
    )


def _dpi_hex(t: str, linha: int) -> tuple[int, int]:
    """Formato bruto do C++: 0xLOHI (X=Y) ou 0xXLOXHIYLOYHI (4 bytes)."""
    h = t[2:]
    try:
        if len(h) == 4:  # 0xLOHI — X e Y iguais
            v = codigo.bytes_para_dpi(int(h[0:2], 16), int(h[2:4], 16))
            return (v, v)
        if len(h) == 8:  # 0xXLOXHIYLOYHI — X e Y diferentes
            x = codigo.bytes_para_dpi(int(h[0:2], 16), int(h[2:4], 16))
            y = codigo.bytes_para_dpi(int(h[4:6], 16), int(h[6:8], 16))
            return (x, y)
    except ValueError:
        pass
    raise ErroIni(
        f"linha {linha}: DPI hexadecimal inválido: '{t}' "
        "(use 0xLOHI ou 0xXLOXHIYLOYHI)"
    )


def _dpi(texto: str, linha: int) -> tuple[int, int]:
    """String de DPI → (X, Y).  Aceita "800", "X800Y400" e o 0x... do C++."""
    t = texto.strip()
    try:
        if t.lower().startswith("0x"):
            return _dpi_hex(t, linha)
        return codigo.texto_para_dpi(t)
    except codigo.ErroCodigo as e:
        raise ErroIni(f"linha {linha}: DPI inválido '{texto}': {e}")
    except ValueError as e:
        if isinstance(e, ErroIni):
            raise
        raise ErroIni(f"linha {linha}: DPI inválido '{texto}': {e}")


# ---------------------------------------------------------------------------
# Estado padrão (constructor.cpp)
# ---------------------------------------------------------------------------

def _keymap_padrao() -> dict[str, str]:
    """Keymap padrão: DATA_SETTINGS_3[35 + 10*i + j][8:12] (passo 10 por perfil).

    O passo 10 é o correto (ARQUITETURA.md seção 4); o loop do constructor.cpp
    usa passo 8, que erra do perfil 2 em diante.
    """
    mapa: dict[str, str] = {}
    for j in range(10):
        quad = tuple(dados.DATA_SETTINGS_3[35 + j][8:12])
        mapa[codigo.NOMES_BOTAO[j]] = codigo.botao_para_texto(quad)
    return mapa


def _perfil_padrao(numero: int) -> dict:
    """Perfil com todos os valores padrão do constructor.cpp."""
    return {
        "numero": numero,
        "led": {
            "cor": COR_PADRAO,
            "modo": MODO_PADRAO,
            "brilho": BRILHO_PADRAO,
            "velocidade": VELOCIDADE_PADRAO,
        },
        "scrollspeed": SCROLLSPEED_PADRAO,
        "report_rate": REPORTRATE_PADRAO,
        "dpi": [
            {"ativo": True, "x": DPI_PADRAO[i], "y": DPI_PADRAO[i]}
            for i in range(NIVEIS_DPI)
        ],
        "botoes": dict(_keymap_padrao()),
    }


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def para_texto(estado: dict) -> str:
    """Dicionário de estado (modelo da seção 4 do ARQUITETURA.md) → texto .ini.

    A saída segue a estrutura de ``examples/example_m711.ini``: mesma ordem
    de seções e chaves, mesmas linhas em branco entre seções e o mesmo estilo
    de comentários.  ``lightmode`` é emitido com o nome canônico em português
    de ``codigo.py``.  Macros são emitidas como ``;## macroN`` com o bruto em
    hex numa linha ``;# bruto=<hex>`` (sem as linhas ``;# down<TAB>...``).
    """
    perfis = estado.get("perfis", [])
    ativo = estado.get("perfil_ativo", 1)
    linhas = [
        "# Gerado por: m711 exportar",
        "# Arquivo editavel — modifique os valores e aplique com: m711 restaurar <arquivo>",
        "# Formato compativel com o driver mouse_m908 (mouse_m908 -c)",
        "# 5 perfis (1-5) sao armazenados no mouse; opcoes ausentes usam o padrao do driver",
        "#",
        f"# Currently active profile: {ativo}",
        "",
    ]
    for i, perfil in enumerate(perfis, 1):
        led = perfil["led"]
        linhas.append(f"[profile{i}]")
        linhas.append("# led mode:")
        linhas.append("# breathing, breathing_rainbow, rainbow, static, wave, "
                       "alternating, reactive, reactive_button, flashing, off, random")
        linhas.append(f"lightmode={led['modo']}")
        linhas.append("# led color")
        linhas.append(f"color={led['cor']}")
        linhas.append("# led brightness level (1-3)")
        linhas.append(f"brightness={led['brilho']}")
        linhas.append("# led animation speed (1-8)")
        linhas.append(f"speed={led['velocidade']}")
        linhas.append("")
        linhas.append("# scrollspeed (1-3f)")
        linhas.append(f"scrollspeed={perfil['scrollspeed']}")
        linhas.append("")
        linhas.append("# Each profile has 5 dpi levels (1-5) that can be "
                       "individually set and enabled/disabled")
        linhas.append("# Set dpiX_enable=0 to disable a particular dpi level")
        linhas.append("# It is not possible to disable all DPI levels, at "
                       "least one will remain enabled")
        for n, dpi in enumerate(perfil["dpi"], 1):
            linhas.append(f"dpi{n}_enable={1 if dpi['ativo'] else 0}")
        linhas.append("# dpi")
        linhas.append("# 100-10000 in steps of 100 (200 above 5000)")
        linhas.append("# X<100-10000>Y<100-10000>")
        linhas.append("# 0x[04-8c][00-01] (same value for x and y)")
        linhas.append("# 0x[04-8c][00-01][04-8c][00-01] (different values for x and y)")
        for n, dpi in enumerate(perfil["dpi"], 1):
            linhas.append(f"dpi{n}={codigo.dpi_para_texto((dpi['x'], dpi['y']))}")
        linhas.append("")
        linhas.append("# usb report rate (125, 250, 500, 1000) Hz")
        linhas.append(f"report_rate={perfil['report_rate']}")
        linhas.append("")
        linhas.append("# button mapping: there is a total of 10 programmable buttons.")
        linhas.append("# keymap.md for full details")
        for chave in _ORDEM_BOTOES:
            linhas.append(f"{chave}={perfil['botoes'][_INI_PARA_ESTADO[chave]]}")
        linhas.append("")
    macros = [m for m in estado.get("macros", []) if m.get("bruto")]
    if macros:
        linhas.append("# Macro definitions")
        linhas.append("# To transfer macros, use the -m option")
        linhas.append("# Each macro (1-15) is defined in its own section, "
                       "starting with \";## macro\"")
        linhas.append("# Each action is prefixed by \";# \"")
        linhas.append("# A tab ('\\t') is used to separate the action from the value")
        linhas.append("# There are 4 different possible actions:")
        linhas.append("# down\t(key)")
        linhas.append("# up\t(key)")
        linhas.append("# delay\t(time in 10ms)")
        linhas.append("# move_(left/right/up/down)\t(1-120)")
        for m in macros:
            linhas.append("")
            linhas.append(f";## macro{m['slot']}")
            bruto = m["bruto"]
            if "\n" in bruto:
                # Macro lida de um .ini legado (linhas ";# down<TAB>tecla"):
                # re-emite as linhas de dados originais.
                for linha_dado in bruto.split("\n"):
                    linhas.append(f";# {linha_dado}")
            else:
                linhas.append(f";# bruto={bruto}")
    return "\n".join(linhas) + "\n"


def de_texto(texto: str) -> dict:
    """Texto .ini → dicionário de estado, com defaults para o que faltar.

    ``ErroIni`` com número da linha em: chave desconhecida, seção inválida,
    valor não numérico, booleano inválido, DPI inválido ou perfil fora de 1..5.
    Faixas de brilho/velocidade/scrollspeed/report_rate não são validadas —
    isso é de ``codigo.py`` na escrita.
    """
    perfil_ativo, secoes, macros = _parse(texto)
    estado: dict = {
        "perfil_ativo": perfil_ativo,
        "desconhecidos": {},
        "perfis": [_perfil_padrao(i) for i in range(1, PERFIS + 1)],
        "macros": [],
    }
    for num in sorted(secoes):
        _aplicar_secao(estado["perfis"][num - 1], secoes[num])
    for slot in sorted(macros):
        dados_macro = macros[slot]
        # Formato desta fase: ";# bruto=<hex>".  Formato legado do C++: várias
        # linhas ";# down<TAB>tecla" — concatenadas no campo "bruto".
        if len(dados_macro) == 1 and dados_macro[0].startswith("bruto="):
            bruto = dados_macro[0][len("bruto="):]
        else:
            bruto = "\n".join(dados_macro)
        estado["macros"].append({"slot": slot, "bruto": bruto, "eventos": []})
    return estado


def _aplicar_secao(perfil: dict, valores: dict[str, tuple[str, int]]) -> None:
    """Aplica as chaves de uma seção [profileN] ao perfil (validando tudo)."""
    for chave, (texto, linha) in valores.items():
        if chave not in _CHAVES_PERFIL:
            raise ErroIni(f"linha {linha}: chave desconhecida: '{chave}'")
        if chave == "lightmode":
            try:
                perfil["led"]["modo"] = codigo.normaliza_modo(texto)
            except codigo.ErroCodigo as e:
                raise ErroIni(f"linha {linha}: {e}")
        elif chave == "color":
            try:
                r, g, b = codigo.cor_para_bytes(texto)
            except codigo.ErroCodigo as e:
                raise ErroIni(f"linha {linha}: {e}")
            perfil["led"]["cor"] = codigo.bytes_para_cor(r, g, b)
        elif chave == "brightness":
            perfil["led"]["brilho"] = _int(texto, linha)
        elif chave == "speed":
            perfil["led"]["velocidade"] = _int(texto, linha)
        elif chave == "scrollspeed":
            perfil["scrollspeed"] = _int(texto, linha)
        elif chave == "report_rate":
            perfil["report_rate"] = _int(texto, linha)
        elif chave.endswith("_enable"):
            n = int(chave[3:-len("_enable")])  # dpiN_enable → N
            perfil["dpi"][n - 1]["ativo"] = _bool(texto, linha)
        elif chave.startswith("dpi"):
            n = int(chave[3:])  # dpiN → N
            x, y = _dpi(texto, linha)
            perfil["dpi"][n - 1]["x"] = x
            perfil["dpi"][n - 1]["y"] = y
        else:  # botão
            if not texto:
                raise ErroIni(f"linha {linha}: valor vazio para '{chave}'")
            perfil["botoes"][_INI_PARA_ESTADO[chave]] = texto


def ler_arquivo(caminho) -> dict:
    """Lê um .ini do disco e devolve o dicionário de estado."""
    with open(caminho, "r", encoding="utf-8") as f:
        return de_texto(f.read())


def salvar_arquivo(caminho, estado: dict) -> None:
    """Grava o dicionário de estado como .ini no disco."""
    with open(caminho, "w", encoding="utf-8") as f:
        f.write(para_texto(estado))
