

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
