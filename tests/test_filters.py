"""Filter parser / evaluator: unit tests plus the HTTP error mapping."""
from urllib.parse import quote

import pytest

from app import filters
from app.errors import ScimError

CORE = "urn:ietf:params:scim:schemas:core:2.0:User"
ENT = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"


def ok(expr, obj):
    return filters.match(filters.parse(expr), obj)


def invalid(expr):
    with pytest.raises(ScimError) as ei:
        filters.parse(expr)
    assert ei.value.status == 400 and ei.value.scim_type == "invalidFilter"
    return ei.value.detail


# ---------------------------------------------------------------- tokenizer / parser errors
def test_tokenize_keeps_quoted_strings_whole():
    assert filters.tokenize('a eq "x y (z)"') == ["a", "eq", '"x y (z)"']
    assert filters.tokenize('emails[type eq "w"]') == ["emails", "[", "type", "eq", '"w"', "]"]


def test_unterminated_string_is_invalid():
    assert "unexpected character" in invalid('userName eq "abc')


def test_trailing_tokens_rejected():
    assert "unexpected token 'extra'" in invalid('userName pr extra')


def test_missing_close_paren_rejected():
    assert "unexpected end" in invalid('(userName pr')
    assert "expected ')'" in invalid('(userName pr userName')


def test_not_requires_parenthesis():
    assert "'not' must be followed by '('" in invalid('not userName pr')
    assert "'not' must be followed by '('" in invalid('not')


@pytest.mark.parametrize("expr", [')', ']', '"lit" eq 1', 'userName eq )'])
def test_unexpected_leading_token(expr):
    assert invalid(expr)


def test_value_path_brackets():
    assert "expected ']'" in invalid('emails[type eq "work" userName')
    assert "unexpected end" in invalid('emails[type eq "work"')


def test_unknown_operator_and_missing_pieces():
    assert "unknown operator 'zz'" in invalid('a zz 1')
    assert "unexpected end" in invalid('a')
    assert "unexpected end" in invalid('a eq')
    assert "unexpected end" in invalid('')


@pytest.mark.parametrize("expr", ['a eq 1.2.3', 'a eq abc', 'a eq "\\q"'])
def test_bad_literals(expr):
    d = invalid(expr)
    assert "bad" in d


def test_literal_types():
    assert filters.parse("a eq 5") == ("cmp", "a", "eq", 5)
    assert filters.parse("a eq -5") == ("cmp", "a", "eq", -5)
    assert filters.parse("a eq 2.5") == ("cmp", "a", "eq", 2.5)
    assert filters.parse("a eq TRUE") == ("cmp", "a", "eq", True)
    assert filters.parse("a eq false") == ("cmp", "a", "eq", False)
    assert filters.parse("a eq null") == ("cmp", "a", "eq", None)
    assert filters.parse('a eq "q\\"uote"') == ("cmp", "a", "eq", 'q"uote')


def test_keywords_case_insensitive_and_precedence():
    obj = {"a": 1, "b": 2}
    assert ok("a eq 1 AND b eq 2", obj)
    assert ok("a eq 9 Or b eq 2", obj)
    assert ok("NOT (a eq 9)", obj)
    # and binds tighter than or: a=9 or (b=2 and a=1)
    assert ok("a eq 9 or b eq 2 and a eq 1", obj)
    assert not ok("(a eq 9 or b eq 2) and a eq 9", obj)


# ---------------------------------------------------------------- find_key / resolve
def test_find_key_case_insensitive():
    assert filters.find_key({"userName": 1}, "userName") == "userName"
    assert filters.find_key({"userName": 1}, "USERNAME") == "userName"
    assert filters.find_key({"userName": 1}, "nope") is None


def test_resolve_plain_nested_and_multivalued():
    doc = {"name": {"givenName": "A"}, "emails": [{"value": "x"}, {"value": "y"}, "junk"], "tags": ["t1", "t2"]}
    assert filters.resolve(doc, "name.givenName") == ["A"]
    assert filters.resolve(doc, "NAME.GIVENNAME") == ["A"]
    assert filters.resolve(doc, "emails.value") == ["x", "y"]
    assert filters.resolve(doc, "tags") == ["t1", "t2"]
    assert filters.resolve(doc, "missing.deeper") == []
    assert filters.resolve(doc, "name.givenName.toDeep") == []  # scalar has no sub-attribute


def test_resolve_urn_paths():
    doc = {"userName": "u", ENT: {"department": "Eng", "manager": {"value": "m1"}}}
    assert filters.resolve(doc, f"{ENT}:department") == ["Eng"]
    assert filters.resolve(doc, f"{ENT}:manager.value") == ["m1"]
    assert filters.resolve(doc, ENT.upper().replace("URN", "urn")) == [doc[ENT]]  # whole extension, any case
    assert filters.resolve(doc, f"{CORE}:userName") == ["u"]  # core URN prefix resolves on the root
    assert filters.resolve(doc, f"{CORE}") == [doc]
    assert filters.resolve({"userName": "u"}, f"{ENT}:department") == []
    assert filters.resolve(["not", "a", "dict"], f"{ENT}:department") == []


# ---------------------------------------------------------------- comparison semantics
def test_string_comparisons_case_insensitive():
    o = {"s": "Hello World"}
    assert ok('s eq "hello world"', o)
    assert ok('s co "O W"', o) and ok('s sw "HELLO"', o) and ok('s ew "WORLD"', o)
    assert not ok('s sw "world"', o) and not ok('s ew "hello"', o) and not ok('s co "zzz"', o)
    assert ok('s gt "A"', o) and ok('s ge "hello world"', o) and ok('s lt "Z"', o) and ok('s le "HELLO WORLD"', o)
    assert not ok('s gt "zzz"', o)
    assert ok('s ne "other"', o) and not ok('s ne "HELLO WORLD"', o)


def test_numeric_comparisons():
    o = {"n": 5, "f": 2.5}
    assert ok("n eq 5", o) and ok("n gt 4", o) and ok("n ge 5", o) and ok("n lt 6", o) and ok("n le 5", o)
    assert not ok("n gt 5", o) and not ok("n lt 5", o)
    assert ok("f lt 3", o) and ok("f eq 2.5", o)
    assert ok("n ne 4", o)


def test_boolean_comparisons():
    o = {"b": True}
    assert ok("b eq true", o) and ok("b ne false", o) and not ok("b eq false", o)
    # bool never equals a string/number
    assert not ok('b eq "true"', o) and not ok("b eq 1", o)
    assert not ok("n eq true", {"n": 1})
    assert ok("n ne true", {"n": 1})  # ne == "not any eq"


def test_type_mismatch_never_matches():
    o = {"s": "5", "n": 5}
    assert not ok("s eq 5", o)
    assert not ok('n eq "5"', o)
    assert not ok("s gt 1", o)


def test_string_operators_on_non_strings():
    assert ok("n co 5", {"n": 15})  # numbers are stringified
    assert not ok('x co "a"', {"x": {"a": 1}})  # complex values never match
    assert not ok('x co "a"', {"x": [{"a": 1}]})
    assert not ok("x co null", {"x": "abc"})
    assert not filters._cmp(None, "co", "a")


def test_null_handling():
    assert ok("x eq null", {})  # absent attribute equals null
    assert not ok("x ne null", {})
    assert not ok("x eq null", {"x": "v"})
    assert ok("x eq null", {"x": None})
    assert not ok("x gt null", {"x": None})
    assert not ok('x eq "a"', {})  # absent never equals a value
    assert ok('x ne "a"', {})
    assert not ok("x gt 1", {})
    assert not filters._cmp("v", "eq", None)


def test_presence():
    o = {"a": "x", "e": "", "n": None, "l": [], "d": {}, "arr": [{"v": 1}]}
    assert ok("a pr", o) and ok("arr pr", o) and ok("arr.v pr", o)
    for k in ("e", "n", "l", "d", "missing"):
        assert not ok(f"{k} pr", o), k


def test_multivalued_any_semantics():
    o = {"emails": [{"value": "a@x.io", "type": "work"}, {"value": "b@y.io", "type": "home"}]}
    assert ok('emails.value eq "b@y.io"', o)
    assert ok('emails.value co "x.io"', o)
    assert ok('emails.value ne "a@x.io"', o) is False  # ne = none equal
    assert ok('emails[type eq "home" and value ew "y.io"]', o)
    assert not ok('emails[type eq "home" and value ew "x.io"]', o)
    assert not ok('emails[type eq "other"]', o)
    assert not ok('name[x eq 1]', o)  # absent complex attribute
    assert not ok('tags[x eq 1]', {"tags": ["scalar"]})  # scalars are not dicts


def test_value_path_with_nested_logic():
    o = {"emails": [{"type": "work", "primary": True}, {"type": "home", "primary": False}]}
    assert ok('emails[(type eq "home" or type eq "x") and not (primary eq true)]', o)
    assert not ok('emails[type eq "work" and primary eq false]', o)


# ---------------------------------------------------------------- over HTTP
def test_http_invalid_filters(api):
    api.user("alice")
    for expr in ['userName eq "abc', 'not userName pr', '(userName pr', 'userName eq abc', 'emails[type eq "x"']:
        r = api.get("/Users?filter=" + quote(expr))
        assert r.status_code == 400, expr
        assert r.json()["scimType"] == "invalidFilter" and r.json()["detail"].startswith("Invalid filter")


def test_http_blank_filter_is_ignored(api):
    api.user("alice")
    assert api.get("/Users?filter=").json()["totalResults"] == 1
    assert api.get("/Users?filter=" + quote("   ")).json()["totalResults"] == 1


def test_http_enterprise_and_extension_filters(api):
    api.user("alice", **{ENT: {"department": "Eng", "manager": {"value": "m1"}}})
    api.user("bob", **{ENT: {"department": "Ops"}})
    api.user("carol")

    def f(expr):
        return sorted(r["userName"] for r in api.get("/Users?filter=" + quote(expr)).json()["Resources"])

    assert f(f'{ENT}:department eq "eng"') == ["alice"]
    assert f(f'{ENT}:manager.value eq "m1"') == ["alice"]
    assert f(f'{ENT}:department pr') == ["alice", "bob"]
    assert f(f'{CORE}:userName sw "c"') == ["carol"]
    assert f('userName ne "alice" and userName ne "bob"') == ["carol"]
    assert f("meta.version gt \"W/\\\"0\\\"\"") == ["alice", "bob", "carol"]


def test_group_filters(api):
    a = api.user("a")
    api.group("Eng", [a["id"]], externalId="g1")
    api.group("Ops")
    r = api.get("/Groups?filter=" + quote('members.value eq "%s"' % a["id"])).json()
    assert [g["displayName"] for g in r["Resources"]] == ["Eng"]
    r = api.get("/Groups?filter=" + quote('externalId pr')).json()
    assert r["totalResults"] == 1
    r = api.get("/Users?filter=" + quote(f'groups.display eq "eng"')).json()
    assert [u["userName"] for u in r["Resources"]] == ["a"]
