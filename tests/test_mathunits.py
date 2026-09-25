import re

from papassist.render.mathunits import analyze_math, normalize_tex, strip_tags


def units(tex):
    return [(u.tex, u.key, u.parent) for u in analyze_math(tex).units]


def test_simple_subscript():
    assert units("M_t") == [("M_t", "M_{t}", None), ("t", "t", 1)]


def test_styled_and_operator_units():
    u = units(r"\mathbb D^q_{i,b}(f) \le \operatorname{lk}_\Delta(i)")
    keys = [k for _, k, _ in u]
    assert "\\mathbb{D}_{i,b}^{q}" in keys
    assert "\\operatorname{lk}_{\\Delta}" in keys
    assert "f" in keys and "i" in keys


def test_mathop_operator_normalizes():
    assert units(r"\mathop{\mathrm{Dgm}}(M)")[0][1] == "\\operatorname{Dgm}"


def test_scripts_order_is_canonical():
    assert normalize_tex(r"\mathbb D^q_{i,b}") == normalize_tex(r"\mathbb{D}_{i,b}^{q}") == "\\mathbb{D}_{i,b}^{q}"


def test_text_and_row_spacing_are_not_tagged():
    a = analyze_math(r"\begin{cases} a & \text{if } x>0 \\[2pt] b & c \end{cases}")
    assert r"\\[2pt]" in a.tagged
    assert not re.search(r"\\text\{[^}]*\\class", a.tagged)
    assert [u.tex for u in a.units] == ["a", "x", "b", "c"]


def test_tagging_roundtrip_preserves_tex():
    tex = r"\bigoplus_{j\in T} M_{x_j}\to\bigoplus_{\substack{F\subseteq T\\|F|=2}} M_{x_F}"
    a = analyze_math(tex)
    back = strip_tags(a.tagged)
    norm = lambda s: s.replace("{", "").replace("}", "").replace(" ", "")
    assert norm(back) == norm(tex)


def test_unbraced_script_argument_is_braced():
    a = analyze_math(r"x_i^2")
    assert a.tagged == r"\class{pa-u-1}{x_{\class{pa-u-2}{i}}^{2}}"


def test_prime_is_part_of_the_unit():
    assert units("f'(x)")[0] == ("f'", "f'", None)


def test_garbage_does_not_raise():
    a = analyze_math(r"\frac{a}{")  # unbalanced
    assert a.tex == r"\frac{a}{"
