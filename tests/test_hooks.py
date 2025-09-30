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
from hooks import get_daypart_greeting, is_greeting, format_main_menu, RED_FLAG_TERMS

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
