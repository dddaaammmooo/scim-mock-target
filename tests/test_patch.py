"""PatchOp engine unit tests: every path form, operation and error class."""
import copy

import pytest

from app.errors import ScimError
from app.patch import apply_patch

CORE = "urn:ietf:params:scim:schemas:core:2.0:User"
ENT = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"


def patch(doc, *ops, core=CORE):
    return apply_patch(doc, {"Operations": list(ops)}, core)


def fail(doc, *ops, status=400, scim_type=None):
    with pytest.raises(ScimError) as ei:
        patch(doc, *ops)
    assert ei.value.status == status
    if scim_type:
        assert ei.value.scim_type == scim_type, ei.value.detail
    return ei.value


def base():
    return {
        "userName": "u", "title": "T", "name": {"givenName": "G", "familyName": "F"},
        "emails": [{"value": "w@x.io", "type": "work", "primary": True}, {"value": "h@x.io", "type": "home"}],
        "roles": [{"value": "r1"}, {"value": "r2"}],
    }


# ---------------------------------------------------------------- request validation
@pytest.mark.parametrize("req", [None, [], {}, {"Operations": []}, {"Operations": "x"}])
def test_requires_operations(req):
    with pytest.raises(ScimError) as ei:
        apply_patch({}, req, CORE)
    assert ei.value.scim_type == "invalidSyntax"


def test_operation_must_be_object_with_known_op():
    fail({}, "nope", scim_type="invalidSyntax")
    fail({}, {"op": "move", "path": "a", "value": 1}, scim_type="invalidSyntax")
    fail({}, {"path": "a", "value": 1}, scim_type="invalidSyntax")  # op missing


def test_op_is_case_insensitive():
    assert patch({}, {"op": "ADD", "path": "title", "value": "x"})["title"] == "x"
    assert patch({"title": "a"}, {"op": "Replace", "path": "title", "value": "x"})["title"] == "x"
    assert "title" not in patch({"title": "a"}, {"op": "REMOVE", "path": "title"})


def test_path_must_be_string():
    fail({}, {"op": "add", "path": 5, "value": 1}, scim_type="invalidPath")


def test_value_required_when_path_given():
    err = fail({}, {"op": "add", "path": "title"}, scim_type="invalidSyntax")
    assert "requires a value" in err.detail
    fail({}, {"op": "replace", "path": "title"}, scim_type="invalidSyntax")


def test_pathless_ops():
    fail({}, {"op": "remove"}, scim_type="noTarget")
    fail({}, {"op": "add", "value": "scalar"}, scim_type="invalidSyntax")
    fail({}, {"op": "replace"}, scim_type="invalidSyntax")
    out = patch({"title": "a"}, {"op": "add", "value": {"title": "b", "nickName": "n"}})
    assert out["title"] == "b" and out["nickName"] == "n"
    out = patch({"name": {"givenName": "G"}}, {"op": "replace", "value": {"name.givenName": "Z"}})
    assert out["name"] == {"givenName": "Z"}


def test_input_is_not_mutated():
    d = base()
    before = copy.deepcopy(d)
    patch(d, {"op": "replace", "path": "title", "value": "new"}, {"op": "remove", "path": "emails"})
    assert d == before


def test_empty_extension_containers_are_pruned():
    out = patch({"userName": "u"}, {"op": "add", "path": f"{ENT}:department", "value": "x"},
                {"op": "remove", "path": f"{ENT}:department"})
    assert ENT not in out


# ---------------------------------------------------------------- simple / complex paths
def test_add_replace_remove_simple():
    d = base()
    assert patch(d, {"op": "add", "path": "nickName", "value": "n"})["nickName"] == "n"
    assert patch(d, {"op": "replace", "path": "TITLE", "value": "new"})["title"] == "new"  # case-insensitive key
    assert "title" not in patch(d, {"op": "remove", "path": "title"})
    assert patch(d, {"op": "remove", "path": "doesNotExist"}) == d  # removing nothing is a no-op


def test_replace_creates_missing_attribute():
    assert patch({}, {"op": "replace", "path": "title", "value": "x"})["title"] == "x"


def test_complex_sub_attribute_set_and_create():
    d = base()
    assert patch(d, {"op": "replace", "path": "name.givenName", "value": "N"})["name"]["givenName"] == "N"
    assert patch(d, {"op": "replace", "path": "name.GIVENNAME", "value": "N"})["name"] == {"givenName": "N", "familyName": "F"}
    out = patch(d, {"op": "add", "path": "name.middleName", "value": "M"})
    assert out["name"]["middleName"] == "M"
    out = patch({}, {"op": "add", "path": "name.givenName", "value": "N"})  # parent absent -> created
    assert out["name"] == {"givenName": "N"}


def test_sub_attribute_set_on_every_element_of_multivalued():
    out = patch(base(), {"op": "replace", "path": "emails.type", "value": "other"})
    assert [e["type"] for e in out["emails"]] == ["other", "other"]
    d = {"emails": [{"value": "a"}, "junk"]}  # non-dict elements are skipped
    assert patch(d, {"op": "replace", "path": "emails.type", "value": "t"})["emails"] == [{"value": "a", "type": "t"}, "junk"]


def test_remove_sub_attribute():
    out = patch(base(), {"op": "remove", "path": "name.givenName"})
    assert out["name"] == {"familyName": "F"}
    out = patch(base(), {"op": "remove", "path": "emails.primary"})  # across list elements
    assert all("primary" not in e for e in out["emails"])
    assert patch(base(), {"op": "remove", "path": "name.nothing"})["name"] == base()["name"]
    assert patch(base(), {"op": "remove", "path": "title.sub"})["title"] == "T"  # scalar target: nothing to do
    d = {"emails": [{"value": "a"}, "junk"]}
    assert patch(d, {"op": "remove", "path": "emails.value"})["emails"] == [{}, "junk"]


# ---------------------------------------------------------------- multi-valued attributes
def test_add_multivalued_appends_and_dedupes():
    d = base()
    out = patch(d, {"op": "add", "path": "emails", "value": {"value": "n@x.io"}})  # single object accepted
    assert len(out["emails"]) == 3
    out = patch(d, {"op": "add", "path": "emails", "value": [{"value": "w@x.io", "type": "work", "primary": True}]})
    assert len(out["emails"]) == 2  # identical element not duplicated
    out = patch({}, {"op": "add", "path": "emails", "value": [{"value": "a"}]})  # absent -> created
    assert out["emails"] == [{"value": "a"}]


def test_add_members_dedupes_on_value_only():
    d = {"displayName": "G", "members": [{"value": "u1", "type": "User"}]}
    out = apply_patch(d, {"Operations": [{"op": "add", "path": "members", "value": [{"value": "u1"}, {"value": "u2"}]}]},
                      "urn:ietf:params:scim:schemas:core:2.0:Group")
    assert [m["value"] for m in out["members"]] == ["u1", "u2"]


def test_replace_multivalued_wholesale():
    out = patch(base(), {"op": "replace", "path": "emails", "value": [{"value": "only@x.io"}]})
    assert out["emails"] == [{"value": "only@x.io"}]
    out = patch(base(), {"op": "replace", "path": "emails", "value": {"value": "single@x.io"}})
    assert out["emails"] == [{"value": "single@x.io"}]
    out = patch({}, {"op": "replace", "path": "roles", "value": [{"value": "r"}]})  # known multi attr, absent
    assert out["roles"] == [{"value": "r"}]


def test_remove_multivalued_whole_and_by_value():
    assert "emails" not in patch(base(), {"op": "remove", "path": "emails"})
    out = patch(base(), {"op": "remove", "path": "roles", "value": [{"value": "r1"}]})
    assert out["roles"] == [{"value": "r2"}]
    out = patch(base(), {"op": "remove", "path": "roles", "value": [{"value": "r1"}, {"value": "r2"}]})
    assert "roles" not in out  # emptied list is dropped
    out = patch(base(), {"op": "remove", "path": "roles", "value": [{"value": "zzz"}]})
    assert len(out["roles"]) == 2
    out = patch(base(), {"op": "remove", "path": "title", "value": "ignored"})  # non-list value -> plain remove
    assert "title" not in out


def test_remove_members_by_value_compares_value_only():
    d = {"members": [{"value": "u1", "type": "User"}, {"value": "u2", "type": "User"}]}
    out = apply_patch(d, {"Operations": [{"op": "remove", "path": "members", "value": [{"value": "u1"}]}]},
                      "urn:ietf:params:scim:schemas:core:2.0:Group")
    assert out["members"] == [{"value": "u2", "type": "User"}]


def test_add_merges_into_existing_complex_attribute():
    out = patch(base(), {"op": "add", "path": "name", "value": {"middleName": "M"}})
    assert out["name"] == {"givenName": "G", "familyName": "F", "middleName": "M"}
    out = patch(base(), {"op": "replace", "path": "name", "value": {"formatted": "X"}})
    assert out["name"] == {"formatted": "X"}  # replace swaps the whole object


# ---------------------------------------------------------------- value-filter paths
def test_filter_replace_sub_attribute():
    out = patch(base(), {"op": "replace", "path": 'emails[type eq "work"].value', "value": "new@x.io"})
    assert out["emails"][0]["value"] == "new@x.io" and out["emails"][1]["value"] == "h@x.io"


def test_filter_replace_whole_element_clears_then_applies():
    out = patch(base(), {"op": "replace", "path": 'emails[type eq "home"]', "value": {"value": "z@x.io"}})
    assert out["emails"][1] == {"value": "z@x.io"}  # old keys (type) dropped
    out = patch(base(), {"op": "add", "path": 'emails[type eq "home"]', "value": {"primary": True}})
    assert out["emails"][1] == {"value": "h@x.io", "type": "home", "primary": True}  # add merges


def test_filter_with_non_object_value_is_rejected():
    fail(base(), {"op": "replace", "path": 'emails[type eq "work"]', "value": "str"}, scim_type="invalidValue")


def test_filter_remove_element_and_sub_attribute():
    out = patch(base(), {"op": "remove", "path": 'emails[type eq "home"]'})
    assert [e["type"] for e in out["emails"]] == ["work"]
    out = patch(base(), {"op": "remove", "path": 'emails[type eq "work"].primary'})
    assert "primary" not in out["emails"][0] and out["emails"][0]["value"] == "w@x.io"
    out = patch(base(), {"op": "remove", "path": 'emails[type eq "work"].nothing'})
    assert out["emails"][0] == base()["emails"][0]
    out = patch(base(), {"op": "remove", "path": 'emails[value pr]'})
    assert "emails" not in out  # removing every element drops the attribute
    out = patch(base(), {"op": "remove", "path": 'emails[type eq "nomatch"]'})
    assert len(out["emails"]) == 2  # no match on remove is a no-op
    assert patch(base(), {"op": "remove", "path": 'phoneNumbers[type eq "x"]'}) == base()  # attribute absent


def test_filter_remove_with_non_dict_elements_present():
    d = {"tags": ["scalar", {"k": "v"}]}
    out = patch(d, {"op": "remove", "path": 'tags[k eq "v"]'})
    assert out["tags"] == ["scalar"]


def test_filter_create_when_no_match():
    # equality filter + sub-attribute: the missing element is created from the filter
    out = patch(base(), {"op": "add", "path": 'emails[type eq "other"].value', "value": "o@x.io"})
    assert out["emails"][-1] == {"type": "other", "value": "o@x.io"}
    # whole-element form with an object value
    out = patch(base(), {"op": "add", "path": 'emails[type eq "other"]', "value": {"value": "o@x.io"}})
    assert out["emails"][-1] == {"type": "other", "value": "o@x.io"}
    # equality filter with a non-object value and no sub-attribute still creates the stub element
    out = patch(base(), {"op": "replace", "path": 'emails[type eq "other"]', "value": "ignored"})
    assert out["emails"][-1] == {"type": "other"}
    # attribute absent altogether -> created
    out = patch({}, {"op": "add", "path": 'phoneNumbers[type eq "work"].value', "value": "123"})
    assert out["phoneNumbers"] == [{"type": "work", "value": "123"}]


def test_filter_no_target_errors():
    # non-equality filter can't be materialised
    err = fail(base(), {"op": "replace", "path": 'emails[type ne "work" and type ne "home"].value', "value": "x"},
               scim_type="noTarget")
    assert "No target matches" in err.detail
    fail(base(), {"op": "replace", "path": 'emails[value co "zzz"].type', "value": "x"}, scim_type="noTarget")
    fail(base(), {"op": "add", "path": 'emails[type eq "x" or type eq "y"].value', "value": "x"}, scim_type="noTarget")
    # dotted attribute inside the filter can't be materialised either
    fail(base(), {"op": "replace", "path": 'name[a.b eq "x"].c', "value": "x"}, scim_type="noTarget")
    # attribute absent + non-creatable filter: error and the placeholder list is not left behind
    err = fail({}, {"op": "add", "path": 'emails[value pr].type', "value": "w"}, scim_type="noTarget")
    assert "No target matches" in err.detail


def test_filter_on_non_list_attribute():
    out = patch({"title": "x"}, {"op": "remove", "path": 'title[a eq "b"]'})
    assert out == {"title": "x"}


def test_invalid_paths():
    fail(base(), {"op": "replace", "path": "emails[", "value": 1}, scim_type="invalidPath")
    fail(base(), {"op": "replace", "path": "[type eq 1]", "value": 1}, scim_type="invalidPath")
    fail(base(), {"op": "replace", "path": "emails[type eq]", "value": 1}, scim_type="invalidFilter")
    fail(base(), {"op": "replace", "path": "emails[type zz 1].value", "value": 1}, scim_type="invalidFilter")


# ---------------------------------------------------------------- read-only attributes
@pytest.mark.parametrize("path", ["id", "meta", "groups", "meta.created", "ID", 'groups[value eq "x"]'])
def test_readonly_attributes(path):
    err = fail(base(), {"op": "replace", "path": path, "value": "x"}, scim_type="mutability")
    assert "read-only" in err.detail


def test_readonly_check_applies_to_pathless_and_core_urn_forms():
    fail(base(), {"op": "replace", "value": {"id": "hacked"}}, scim_type="mutability")
    fail(base(), {"op": "remove", "path": "meta"}, scim_type="mutability")
    fail(base(), {"op": "add", "path": f"{CORE}:id", "value": "x"}, scim_type="mutability")


def test_readonly_name_is_fine_inside_extension_objects():
    out = patch({}, {"op": "add", "path": ENT, "value": {"meta": "m", "id": "i"}})
    assert out[ENT] == {"meta": "m", "id": "i"}


# ---------------------------------------------------------------- URN paths
def test_extension_attribute_paths():
    d = {"userName": "u"}
    out = patch(d, {"op": "add", "path": f"{ENT}:department", "value": "Eng"})
    assert out[ENT] == {"department": "Eng"}
    out = patch(out, {"op": "replace", "path": f"{ENT}:manager.value", "value": "m1"})
    assert out[ENT]["manager"] == {"value": "m1"}
    out = patch(out, {"op": "replace", "path": f"{ENT.lower()}:DEPARTMENT", "value": "Ops"})
    assert out[ENT]["department"] == "Ops"  # URN and attribute matching are case-insensitive
    out = patch(out, {"op": "remove", "path": f"{ENT}:manager"})
    assert out[ENT] == {"department": "Ops"}


def test_extension_whole_object_ops():
    out = patch({"userName": "u"}, {"op": "add", "path": ENT, "value": {"department": "Eng", "division": "D"}})
    assert out[ENT] == {"department": "Eng", "division": "D"}
    out = patch(out, {"op": "replace", "path": ENT, "value": {"costCenter": "C"}})
    assert out[ENT]["costCenter"] == "C" and out[ENT]["department"] == "Eng"  # merged key by key
    out = patch(out, {"op": "remove", "path": ENT})
    assert ENT not in out
    fail({"userName": "u"}, {"op": "add", "path": ENT, "value": "not-an-object"}, scim_type="invalidValue")


def test_remove_on_absent_extension_is_noop():
    d = {"userName": "u"}
    assert patch(d, {"op": "remove", "path": f"{ENT}:department"}) == d
    assert patch(d, {"op": "remove", "path": ENT}) == d


def test_core_urn_prefix_is_transparent():
    out = patch({"userName": "u"}, {"op": "replace", "path": f"{CORE}:title", "value": "T"})
    assert out["title"] == "T"
    out = patch(out, {"op": "replace", "path": CORE, "value": {"nickName": "n"}})
    assert out["nickName"] == "n"
    assert "nickName" not in patch(out, {"op": "remove", "path": f"{CORE.lower()}:nickName"})
    out = patch(out, {"op": "remove", "path": CORE})  # removing the core "extension" is a no-op
    assert out["title"] == "T"


def test_extension_key_that_is_not_an_object():
    fail({ENT: "scalar"}, {"op": "add", "path": f"{ENT}:department", "value": "x"}, scim_type="invalidPath")


def test_urn_keys_in_value_object():
    out = patch({}, {"op": "add", "value": {ENT: {"department": "Eng"}}})
    assert out[ENT] == {"department": "Eng"}
    out = patch({}, {"op": "add", "value": {f"{ENT}:department": "Eng"}})
    assert out[ENT] == {"department": "Eng"}


# ---------------------------------------------------------------- over HTTP
def test_http_error_mapping(api):
    u = api.user("p")
    base_path = f"/Users/{u['id']}"
    r = api.patch(base_path, [{"op": "replace", "path": 'emails[type eq "x"', "value": 1}])
    assert r.status_code == 400 and r.json()["scimType"] == "invalidPath"
    r = api.patch(base_path, [{"op": "replace", "path": 'emails[value co "zz"].type', "value": "w"}])
    assert r.status_code == 400 and r.json()["scimType"] == "noTarget"
    r = api.patch(base_path, [{"op": "replace", "path": "id", "value": "x"}])
    assert r.status_code == 400 and r.json()["scimType"] == "mutability"
    r = api.patch(base_path, [{"op": "replace", "path": "name", "value": "not-an-object"}])
    assert r.status_code == 400 and r.json()["scimType"] == "invalidValue"  # post-patch validation
    # a failed patch leaves the resource untouched (rolled back)
    assert api.get(base_path).json()["meta"]["version"] == 'W/"1"'


def test_http_value_filter_creation_roundtrip(api):
    u = api.user("q")
    r = api.patch(f"/Users/{u['id']}", [{"op": "add", "path": 'emails[type eq "work"].value', "value": "w@x.io"}])
    assert r.status_code == 200 and r.json()["emails"] == [{"type": "work", "value": "w@x.io"}]
    r = api.patch(f"/Users/{u['id']}", [{"op": "replace", "path": 'emails[type eq "work"].value', "value": "w2@x.io"}])
    assert r.json()["emails"] == [{"type": "work", "value": "w2@x.io"}]
