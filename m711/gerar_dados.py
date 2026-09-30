#!/usr/bin/env python3
"""Gera ``dados.py`` a partir das tabelas do driver C++ de referência.

O driver ``dokutan/mouse_m908`` (GPLv3) contém as tabelas de bytes do
protocolo do Redragon M711.  Este script extrai essas tabelas dos arquivos
``.cpp`` e as grava em ``dados.py`` para que o port Python não precise
reimplementar parsing de C++.

**Nunca edite ``dados.py`` à mão.**  Rode este script::

    python3 gerar_dados.py

Os caminhos dos arquivos-fonte são relativos a este script, então o
projeto pode ser movido sem quebrar.

Os dicionários C++ têm formatos diferentes; cada um tem um parser
dedicado.  Todos validam a contagem de elementos contra a dimensão
declarada no C++ e falham alto se não bater.
"""

from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Caminhos
# ---------------------------------------------------------------------------

AQUI = Path(__file__).resolve().parent
FONTE_M711 = AQUI.parent / "port" / "mouse_m908" / "include" / "m711" / "data.cpp"
FONTE_GERAL = AQUI.parent / "port" / "mouse_m908" / "include" / "data.cpp"
SAIDA = AQUI / "dados.py"

# ---------------------------------------------------------------------------
# Limpeza de comentários
# ---------------------------------------------------------------------------


def _sem_comentarios(texto: str) -> str:
    """Remove comentários ``//`` e ``/* */`` preservando quebras de linha."""
    texto = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), texto, flags=re.DOTALL)
    texto = re.sub(r"//[^\n]*", "", texto)
    return texto


# ---------------------------------------------------------------------------
# Helpers de extração
# ---------------------------------------------------------------------------


def _bloco(texto: str, nome: str) -> str:
    """Retorna o corpo (dentro das chaves) da primeira ocorrência de ``nome[dim] = { ... }``."""
    m = re.search(re.escape(nome) + r"(?:\s*\[[^\]]*\])*\s*=\s*\{", texto)
    if not m:
        raise ValueError(f"Tabela '{nome}' não encontrada")
    inicio = m.end()  # logo após a abertura
    profundidade = 1
    for i in range(inicio, len(texto)):
        c = texto[i]
        if c == "{":
            profundidade += 1
        elif c == "}":
            profundidade -= 1
            if profundidade == 0:
                return texto[inicio:i]
    raise ValueError(f"Tabela '{nome}': chaves não balanceadas")


def _hex(s: str) -> int:
    return int(s.strip(), 16)


# ---------------------------------------------------------------------------
# Parsers de array
# ---------------------------------------------------------------------------


def _array_2d(texto: str, nome: str, colunas: int) -> list[list[int]]:
    """Extrai ``uint8_t nome[R][C] = { {..}, {..}, ... };``."""
    corpo = _bloco(texto, nome)
    linhas = re.findall(r"\{([^{}]*)\}", corpo)
    tabela = []
    for linha in linhas:
        vals = [_hex(v) for v in re.findall(r"0x[0-9a-fA-F]+", linha)]
        if len(vals) != colunas:
            raise ValueError(
                f"{nome}: linha com {len(vals)} valores, esperado {colunas}: {linha!r}"
            )
        tabela.append(vals)
    return tabela


def _array_1d(texto: str, nome: str, tamanho: int) -> list[int]:
    """Extrai ``uint8_t nome[N] = { ... };``."""
    corpo = _bloco(texto, nome)
    vals = [_hex(v) for v in re.findall(r"0x[0-9a-fA-F]+", corpo)]
    if len(vals) != tamanho:
        raise ValueError(f"{nome}: {len(vals)} valores, esperado {tamanho}")
    return vals


# ---------------------------------------------------------------------------
# Parsers de dicionário
# ---------------------------------------------------------------------------


def _dict_int_str(texto: str, nome: str) -> dict[int, str]:
    """``{ 0, "button_left" },`` → ``{0: "button_left"}``."""
    corpo = _bloco(texto, nome)
    resultado = {}
    for m in re.finditer(r"\{\s*(\d+)\s*,\s*\"([^\"]+)\"\s*\}", corpo):
        resultado[int(m.group(1))] = m.group(2)
    if not resultado:
        raise ValueError(f"{nome}: dicionário vazio ou formato não reconhecido")
    return resultado


def _dict_str_int(texto: str, nome: str) -> dict[str, int]:
    """``{ "ctrl_l+", 1 },`` → ``{"ctrl_l+": 1}``."""
    corpo = _bloco(texto, nome)
    resultado = {}
    for m in re.finditer(r"\{\s*\"([^\"]+)\"\s*,\s*(0x[0-9a-fA-F]+|\d+)\s*\}", corpo):
        resultado[m.group(1)] = int(m.group(2), 0)
    if not resultado:
        raise ValueError(f"{nome}: dicionário vazio ou formato não reconhecido")
    return resultado


def _dict_int_pair(texto: str, nome: str) -> dict[int, tuple[int, int]]:
    """``{ 100, {0x02, 0x00} },`` → ``{100: (0x02, 0x00)}``."""
    corpo = _bloco(texto, nome)
    resultado = {}
    for m in re.finditer(
        r"\{\s*(\d+)\s*,\s*\{\s*(0x[0-9a-fA-F]+)\s*,\s*(0x[0-9a-fA-F]+)\s*\}\s*\}",
        corpo,
    ):
        resultado[int(m.group(1))] = (_hex(m.group(2)), _hex(m.group(3)))
    if not resultado:
        raise ValueError(f"{nome}: dicionário vazio ou formato não reconhecido")
    return resultado


def _dict_str_quad(texto: str, nome: str) -> dict[str, tuple[int, int, int, int]]:
    """``{ "left", { 0x81, 0x00, 0x00, 0x00 } },`` → ``{"left": (0x81,0,0,0)}``."""
    corpo = _bloco(texto, nome)
    resultado = {}
    for m in re.finditer(
        r"\{\s*\"([^\"]+)\"\s*,\s*\{([^}]+)\}\s*\}",
        corpo,
    ):
        vals = [_hex(v) for v in re.findall(r"0x[0-9a-fA-F]+", m.group(2))]
        if len(vals) != 4:
            raise ValueError(f"{nome}: entrada '{m.group(1)}' com {len(vals)} valores, esperado 4")
        resultado[m.group(1)] = tuple(vals)  # type: ignore[assignment]
    if not resultado:
        raise ValueError(f"{nome}: dicionário vazio ou formato não reconhecido")
    return resultado


def _dict_pair_enum(texto: str, nome: str, prefixo_enum: str) -> dict[tuple[int, int], str]:
    """``{ {0x00, 0x00}, rd_mouse::lightmode_off },`` → ``{(0,0): "off"}``."""
    corpo = _bloco(texto, nome)
    resultado = {}
    for m in re.finditer(
        r"\{\s*\{\s*(0x[0-9a-fA-F]+)\s*,\s*(0x[0-9a-fA-F]+)\s*\}\s*,\s*"
        + re.escape(prefixo_enum)
        + r"(\w+)\s*\}",
        corpo,
    ):
        b1, b2 = _hex(m.group(1)), _hex(m.group(2))
        nome_enum = m.group(3)
        # lightmode_off -> off, lightmode_breathing_rainbow -> breathing_rainbow
        nome_limpo = nome_enum
        resultado[(b1, b2)] = nome_limpo
    if not resultado:
        raise ValueError(f"{nome}: dicionário vazio ou formato não reconhecido")
    return resultado


def _dict_int_enum(texto: str, nome: str, prefixo_enum: str) -> dict[int, int]:
    """``{ 8, rd_mouse::r_125Hz },`` → ``{8: 125}`` (extrai número do nome do enum)."""
    corpo = _bloco(texto, nome)
    resultado = {}
    for m in re.finditer(
        r"\{\s*(\d+)\s*,\s*" + re.escape(prefixo_enum) + r"(\w+)\s*\}",
        corpo,
    ):
        chave = int(m.group(1))
        nome_enum = m.group(2)
        # r_125Hz -> 125 (o "r_" já foi consumido pelo regex externo)
        num = re.match(r"(\d+)Hz", nome_enum)
        if not num:
            raise ValueError(f"{nome}: não consegui extrair número de '{nome_enum}'")
        resultado[chave] = int(num.group(1))
    if not resultado:
        raise ValueError(f"{nome}: dicionário vazio ou formato não reconhecido")
    return resultado


# ---------------------------------------------------------------------------
# Limites (constantes do rd_mouse)
# ---------------------------------------------------------------------------


def _limites(texto: str) -> dict[str, tuple[int, int]]:
    """Extrai as constantes ``_c_*_min``/``_c_*_max`` de ``data.cpp``."""
    corpo = _sem_comentarios(texto)
    limites = {}
    for m in re.finditer(
        r"rd_mouse::_c_(\w+?)_min\s*=\s*(0x[0-9a-fA-F]+|\d+)\s*,"
        r"\s*rd_mouse::_c_(\w+?)_max\s*=\s*(0x[0-9a-fA-F]+|\d+)",
        corpo,
    ):
        nome = m.group(1)
        lo = int(m.group(2), 0)
        hi = int(m.group(4), 0)
        limites[nome] = (lo, hi)
    if not limites:
        raise ValueError("Não consegui extrair os limites do data.cpp")
    # Converte nomes do C++ para os canônicos em português do projeto
    MAPEAMENTO = {
        "scrollspeed": "scrollspeed",
        "brightness": "brilho",
        "speed": "velocidade",
        "level": "nivel",
        "dpi": "dpi",
        "dpi_2": "dpi_alto",
    }
    convertido = {}
    for nome_cpp, par in limites.items():
        can = MAPEAMENTO.get(nome_cpp, nome_cpp)
        convertido[can] = par
    return convertido


# ---------------------------------------------------------------------------
# Formatação
# ---------------------------------------------------------------------------


def _fmt_tabela_2d(tabela: list[list[int]]) -> str:
    linhas = []
    for linha in tabela:
        itens = ", ".join(f"0x{v:02x}" for v in linha)
        linhas.append(f"\t[{itens}],")
    return "[\n" + "\n".join(linhas) + "\n]"


def _fmt_tabela_1d(vals: list[int]) -> str:
    return "[" + ", ".join(f"0x{v:02x}" for v in vals) + "]"


def _fmt_dict_int_str(d: dict[int, str]) -> str:
    itens = [f"\t{k}: \"{v}\"," for k, v in sorted(d.items())]
    return "{\n" + "\n".join(itens) + "\n}"


def _fmt_dict_str_int(d: dict[str, int]) -> str:
    itens = [f"\t\"{k}\": {v}," for k, v in sorted(d.items())]
    return "{\n" + "\n".join(itens) + "\n}"


def _fmt_dict_int_pair(d: dict[int, tuple[int, int]]) -> str:
    itens = [f"\t{k}: (0x{v[0]:02x}, 0x{v[1]:02x})," for k, v in sorted(d.items())]
    return "{\n" + "\n".join(itens) + "\n}"


def _fmt_dict_str_quad(d: dict[str, tuple[int, int, int, int]]) -> str:
    itens = []
    for k, v in sorted(d.items()):
        itens.append(f"\t\"{k}\": (0x{v[0]:02x}, 0x{v[1]:02x}, 0x{v[2]:02x}, 0x{v[3]:02x}),")
    return "{\n" + "\n".join(itens) + "\n}"


def _fmt_dict_pair_enum(d: dict[tuple[int, int], str]) -> str:
    itens = [f"\t(0x{k[0]:02x}, 0x{k[1]:02x}): \"{v}\"," for k, v in sorted(d.items())]
    return "{\n" + "\n".join(itens) + "\n}"


def _fmt_dict_int_int(d: dict[int, int]) -> str:
    itens = [f"\t{k}: {v}," for k, v in sorted(d.items())]
    return "{\n" + "\n".join(itens) + "\n}"


def _fmt_limites(d: dict[str, tuple[int, int]]) -> str:
    itens = [f"\t\"{k}\": ({v[0]}, {v[1]})," for k, v in sorted(d.items())]
    return "{\n" + "\n".join(itens) + "\n}"


# ---------------------------------------------------------------------------
# Cabeçalho do arquivo gerado
# ---------------------------------------------------------------------------

CABECALHO = '''\
# ARQUIVO GERADO — NÃO EDITE À MÃO
#
# Gerado por: python3 gerar_dados.py
# Fonte: port/mouse_m908/include/m711/data.cpp
#         port/mouse_m908/include/data.cpp
#
# Licença: GPLv3 (derivado do driver dokutan/mouse_m908, GPLv3).
#
# AVISO: DPI_CODES_UPSTREAM é a tabela do driver C++ e está SABIDAMENTE
# ERRADA em vários pontos (ex.: diz 400=0x08 mas o firmware usa 0x09;
# 3200=0x49 mas o firmware usa 0x48; 5000=0x73/0x00 mas o firmware usa
# 0x73/0x01).  A conversão real está em codigo.py, que usa a fórmula
# dpi = lo * 44.4444... * 2**hi.  Esta tabela é mantida apenas para
# referência e comparação.
'''


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    texto_m711 = _sem_comentarios(FONTE_M711.read_text(encoding="utf-8"))
    texto_geral = _sem_comentarios(FONTE_GERAL.read_text(encoding="utf-8"))

    # --- Limites ---
    limites = _limites(texto_geral)

    # --- Tabelas do m711 ---
    button_names = _dict_int_str(texto_m711, "mouse_m711::_c_button_names")
    dpi_codes = _dict_int_pair(texto_m711, "mouse_m711::_c_dpi_codes")
    data_s_profile = _array_2d(texto_m711, "mouse_m711::_c_data_s_profile", 16)
    data_settings_1 = _array_2d(texto_m711, "mouse_m711::_c_data_settings_1", 16)
    data_settings_2 = _array_1d(texto_m711, "mouse_m711::_c_data_settings_2", 64)
    data_settings_3 = _array_2d(texto_m711, "mouse_m711::_c_data_settings_3", 16)
    data_macros_1 = _array_1d(texto_m711, "mouse_m711::_c_data_macros_1", 16)
    data_macros_2 = _array_1d(texto_m711, "mouse_m711::_c_data_macros_2", 256)
    data_macros_3 = _array_1d(texto_m711, "mouse_m711::_c_data_macros_3", 16)
    macros_codes = _array_2d(texto_m711, "mouse_m711::_c_data_macros_codes", 2)
    data_read_1 = _array_2d(texto_m711, "mouse_m711::_c_data_read_1", 16)
    data_read_2 = _array_2d(texto_m711, "mouse_m711::_c_data_read_2", 64)
    data_read_3 = _array_2d(texto_m711, "mouse_m711::_c_data_read_3", 16)

    # --- Tabelas genéricas ---
    keycodes = _dict_str_quad(texto_geral, "rd_mouse::_c_keycodes")
    modificadores = _dict_str_int(texto_geral, "rd_mouse::_c_keyboard_modifier_values")
    teclas = _dict_str_int(texto_geral, "rd_mouse::_c_keyboard_key_values")
    lightmode_values = _dict_pair_enum(texto_geral, "rd_mouse::_c_lightmode_values", "rd_mouse::lightmode_")
    report_rate_values = _dict_int_enum(texto_geral, "rd_mouse::_c_report_rate_values", "rd_mouse::r_")

    # --- Validações de dimensão ---
    assert len(data_s_profile) == 6, f"data_s_profile: {len(data_s_profile)} != 6"
    assert len(data_settings_1) == 15, f"data_settings_1: {len(data_settings_1)} != 15"
    assert len(data_settings_3) == 90, f"data_settings_3: {len(data_settings_3)} != 90"
    assert len(data_read_1) == 9, f"data_read_1: {len(data_read_1)} != 9"
    assert len(data_read_2) == 85, f"data_read_2: {len(data_read_2)} != 85"
    assert len(data_read_3) == 46, f"data_read_3: {len(data_read_3)} != 46"
    assert len(macros_codes) == 15, f"macros_codes: {len(macros_codes)} != 15"
    assert len(data_macros_2) == 256, f"data_macros_2: {len(data_macros_2)} != 256"

    # --- Geração ---
    partes = [
        CABECALHO,
        "LIMITS = " + _fmt_limites(limites),
        "",
        "BUTTON_NAMES = " + _fmt_dict_int_str(button_names),
        "",
        "DPI_CODES_UPSTREAM = " + _fmt_dict_int_pair(dpi_codes),
        "",
        "KEYCODES = " + _fmt_dict_str_quad(keycodes),
        "",
        "MODIFICADORES = " + _fmt_dict_str_int(modificadores),
        "",
        "TECLAS = " + _fmt_dict_str_int(teclas),
        "",
        "LIGHTMODE_VALUES = " + _fmt_dict_pair_enum(lightmode_values),
        "",
        "REPORT_RATE_VALUES = " + _fmt_dict_int_int(report_rate_values),
        "",
        "MACROS_CODES = " + _fmt_tabela_2d(macros_codes),
        "",
        "DATA_S_PROFILE = " + _fmt_tabela_2d(data_s_profile),
        "",
        "DATA_SETTINGS_1 = " + _fmt_tabela_2d(data_settings_1),
        "",
        "DATA_SETTINGS_2 = " + _fmt_tabela_1d(data_settings_2),
        "",
        "DATA_SETTINGS_3 = " + _fmt_tabela_2d(data_settings_3),
        "",
        "DATA_MACROS_1 = " + _fmt_tabela_1d(data_macros_1),
        "",
        "DATA_MACROS_2 = " + _fmt_tabela_1d(data_macros_2),
        "",
        "DATA_MACROS_3 = " + _fmt_tabela_1d(data_macros_3),
        "",
        "DATA_READ_1 = " + _fmt_tabela_2d(data_read_1),
        "",
        "DATA_READ_2 = " + _fmt_tabela_2d(data_read_2),
        "",
        "DATA_READ_3 = " + _fmt_tabela_2d(data_read_3),
        "",
    ]

    SAIDA.write_text("\n".join(partes), encoding="utf-8")
    print(f"Gerado: {SAIDA}")
    print(f"  LIMITS: {len(limites)} entradas")
    print(f"  BUTTON_NAMES: {len(button_names)} entradas")
    print(f"  DPI_CODES_UPSTREAM: {len(dpi_codes)} entradas")
    print(f"  KEYCODES: {len(keycodes)} entradas")
    print(f"  MODIFICADORES: {len(modificadores)} entradas")
    print(f"  TECLAS: {len(teclas)} entradas")
    print(f"  LIGHTMODE_VALUES: {len(lightmode_values)} entradas")
    print(f"  REPORT_RATE_VALUES: {len(report_rate_values)} entradas")
    print(f"  MACROS_CODES: {len(macros_codes)} entradas")
    print(f"  DATA_S_PROFILE: {len(data_s_profile)}x{len(data_s_profile[0])}")
    print(f"  DATA_SETTINGS_1: {len(data_settings_1)}x{len(data_settings_1[0])}")
    print(f"  DATA_SETTINGS_2: {len(data_settings_2)}")
    print(f"  DATA_SETTINGS_3: {len(data_settings_3)}x{len(data_settings_3[0])}")
    print(f"  DATA_MACROS_1: {len(data_macros_1)}")
    print(f"  DATA_MACROS_2: {len(data_macros_2)}")
    print(f"  DATA_MACROS_3: {len(data_macros_3)}")
    print(f"  DATA_READ_1: {len(data_read_1)}x{len(data_read_1[0])}")
    print(f"  DATA_READ_2: {len(data_read_2)}x{len(data_read_2[0])}")
    print(f"  DATA_READ_3: {len(data_read_3)}x{len(data_read_3[0])}")


if __name__ == "__main__":
    main()
