from hooks import compose_greeting, send_main_menu, build_info_servicios_message, build_direccion_gye_message, build_direccion_milagro_message
def test_compose_greeting_no_dup():
    saludo = compose_greeting()
    assert "Soy Ana" not in saludo
    assert "buen" in saludo.lower() or "tarde" in saludo.lower() or "noche" in saludo.lower()
    menu = send_main_menu(None, saludo)
    assert menu.count("Soy Ana") == 1
    assert menu.count("asistente virtual") == 1
    assert menu.count("1️⃣") == 1

def test_info_servicios_contains_copy():
    msg = build_info_servicios_message()
    assert "$45" in msg
    assert "60 minutos" in msg
    for bullet in ["Electrocardiograma", "nutricional", "Educación en diabetes", "Neuropatía Diabética", "riesgo cardiovascular"]:
        assert bullet in msg

def test_info_servicios_navigation():
    # Simular navegación: opción 1 y 2
    gye = build_direccion_gye_message()
    mil = build_direccion_milagro_message()
    assert "Guayaquil" in gye
    assert "Milagro" in mil
    # Simular atrás/inicio en el panel principal
    msg = build_info_servicios_message()
    assert "Atrás" in msg or "Inicio" in msg
from hooks import reset_to_main, Hooks
import types

def test_any_input_routes_to_main_menu():
    session = {"is_new": True}
    h = Hooks()
    out = h.route_input(session, "hola")
    assert "Soy Ana" in out and "1️⃣" in out
    session = {"is_new": False, "state": None}
    out2 = h.route_input(session, "x")
    assert "Soy Ana" in out2 and "1️⃣" in out2

def test_global_9_goes_home():
    session = {"is_new": False, "state": "MENU_PRINCIPAL"}
    h = Hooks()
    out = h.route_input(session, "9")
    assert "Soy Ana" in out

def test_idempotency_blocks_duplicate():
    from utils.idempotency import mark_processed, is_processed
    mid = "testmsgid-123"
    platform = "wa"
    assert not is_processed(mid, platform)
    mark_processed(mid, platform)
    assert is_processed(mid, platform)


import pytest

from hooks import get_daypart_greeting, is_greeting, format_main_menu, RED_FLAG_TERMS, es_bandera_roja, is_red_flag

def test_is_greeting():
    assert is_greeting("hola")
    assert is_greeting("buenas")
    assert is_greeting("qué tal")
    assert not is_greeting("no es un saludo")

def test_get_daypart_greeting():
    assert get_daypart_greeting(9) == "¡Buenos días 🌞!"
    assert get_daypart_greeting(15) == "¡Buenas tardes ☀️!"
    assert get_daypart_greeting(20) == "¡Buenas noches 🌙!"

def test_format_main_menu():
    menu = format_main_menu()
    assert "Soy Ana 🤖" in menu
    for n in ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]:
        assert n in menu

def test_red_flag_terms():
    terms = ["quemazón", "hormigueo", "descargas eléctricas", "dolor", "frialdad", "calentura", "fiebre", "herida"]
    for t in terms:
        assert any(t in rf for rf in RED_FLAG_TERMS)

def test_es_bandera_roja_and_is_red_flag():
    positivos = ["tengo dolor", "quemazón en la pierna", "hormigueo", "descargas eléctricas", "frialdad", "calentura", "fiebre", "herida"]
    negativos = ["hola", "quiero una cita", "no tengo síntomas", "todo bien"]
    for texto in positivos:
        assert es_bandera_roja(texto) is True
        assert is_red_flag(texto) is True
    for texto in negativos:
        assert es_bandera_roja(texto) is False
        assert is_red_flag(texto) is False
