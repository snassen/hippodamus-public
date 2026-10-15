import pytest

from hippodamus import theophrastus as th
from hippodamus.theophrastus import Column, Exists

FIELDS = {
    "name": Column("t.name"),
    "size": Column("t.size"),
    "tag": Exists("unnest(t.tags) tg", "{cmp}", "tg"),
}


@pytest.fixture
def things(db):
    db.execute("create table things (id int primary key, name text, size int, tags text[])")
    db.execute("insert into things values (1, 'Avtal Acme.pdf', 10, '{red}'), (2, 'notes.txt', 500, '{}'),"
               " (3, 'Acme offer.docx', 50, '{blue,red}')")
    return db


def ids(db, conds):
    where, p = th.compile_conditions(conds, FIELDS)
    return sorted(r["id"] for r in db.execute(f"select id from things t where {where}", p))


def test_conditions_are_anded_and_an_any_group_is_ored(things):
    assert ids(things, [{"field": "name", "op": "contains", "value": "acme"}]) == [1, 3]
    assert ids(things, [{"field": "name", "op": "contains", "value": "acme"},
                        {"any": [{"field": "size", "op": "lt", "value": 20},
                                 {"field": "tag", "op": "is", "value": "blue"}]}]) == [1, 3]
    assert ids(things, [{"field": "name", "op": "contains", "value": "acme"},
                        {"field": "tag", "op": "is", "value": "blue"}]) == [3]


def test_an_empty_rule_matches_nothing(things):
    assert ids(things, []) == []


def test_values_go_as_parameters_never_into_the_sql():
    where, p = th.compile_conditions([{"field": "name", "op": "is", "value": "x'; drop table things; --"}], FIELDS)
    assert "drop" not in where and p == ["x'; drop table things; --"]


def test_like_wildcards_in_a_value_are_taken_literally(things):
    assert ids(things, [{"field": "name", "op": "contains", "value": "%"}]) == []


def test_an_unknown_field_or_operator_says_what_is_known():
    with pytest.raises(th.RuleError, match="known: name, size, tag"):
        th.check([{"field": "colour", "op": "is", "value": "x"}], FIELDS)
    with pytest.raises(th.RuleError):
        th.check([{"field": "name", "op": "near", "value": "x"}], FIELDS)


def test_the_same_rules_compile_as_talos_rules_did():
    # The talos shape, unchanged: an any of a list and a plain condition.
    conds = [{"any": [{"field": "name", "op": "in", "value": ["A", "b"]},
                      [{"field": "name", "op": "is", "value": "c"}, {"field": "size", "op": "gt", "value": 3}]]}]
    where, p = th.compile_conditions(conds, FIELDS)
    # talos's own test suite compares this with talos.rules.compile_conditions, character for character.
    assert where == "(((lower(t.name) = any(%s))) or ((lower(t.name) = %s) and (t.size > %s)))"
    assert p == [["a", "b"], "c", 3]


LEAF = {"field": "name", "op": "is", "value": "x"}


def nested(depth: int) -> list:
    """A rule whose innermost condition sits ``depth`` any-groups down."""
    rule: list = [LEAF]
    for _ in range(depth):
        rule = [{"any": [rule]}]
    return rule


def test_groups_nest_eight_deep_and_no_deeper():
    where, p = th.compile_conditions(nested(8), FIELDS)
    assert where.count("lower(t.name) = %s") == 1 and p == ["x"]
    assert th.leaves(nested(8)) == [LEAF] and th.check(nested(8), FIELDS) == [LEAF]
    for f in (lambda r: th.compile_conditions(r, FIELDS), th.leaves, lambda r: th.check(r, FIELDS)):
        with pytest.raises(th.RuleError, match="too deep"):
            f(nested(9))


def test_a_rule_a_thousand_groups_deep_is_a_rule_error_not_a_recursion_error():
    for f in (lambda r: th.compile_conditions(r, FIELDS), th.leaves):
        with pytest.raises(th.RuleError):
            f(nested(1100))
    deep_lists: list = [LEAF]
    for _ in range(5000):
        deep_lists = [deep_lists]
    with pytest.raises(th.RuleError):
        th.leaves(deep_lists)
    with pytest.raises(th.RuleError):
        th.compile_conditions(deep_lists, FIELDS)


def test_a_cyclic_rule_is_refused():
    group = {"any": []}
    group["any"].append(group)
    loop: list = [LEAF]
    loop.append(loop)
    for rule in ([group], loop):
        with pytest.raises(th.RuleError):
            th.compile_conditions(rule, FIELDS)
        with pytest.raises(th.RuleError):
            th.leaves(rule)


def test_two_hundred_conditions_are_allowed_and_the_two_hundred_and_first_is_not():
    assert len(th.leaves([LEAF] * 200)) == 200
    th.compile_conditions([LEAF] * 200, FIELDS)
    for f in (lambda r: th.compile_conditions(r, FIELDS), th.leaves):
        with pytest.raises(th.RuleError, match="at most 200"):
            f([LEAF] * 201)
        with pytest.raises(th.RuleError, match="at most 200"):
            f([{"any": [LEAF] * 150}, {"any": [LEAF] * 60}])


class Huge(list):
    """A list that counts how many of its items were taken: the walk must stop on its length alone."""
    taken = 0

    def __iter__(self):
        for x in super().__iter__():
            Huge.taken += 1
            yield x

    def __reversed__(self):
        for x in super().__reversed__():
            Huge.taken += 1
            yield x


def test_a_huge_list_is_refused_on_its_length_before_its_items_are_taken():
    empty_groups = Huge([{"any": [[]]}] * 100_000)
    for f in (lambda r: th.compile_conditions(r, FIELDS), th.leaves):
        Huge.taken = 0
        with pytest.raises(th.RuleError, match="too large"):
            f(empty_groups)
        assert Huge.taken == 0
    wide_any = [{"any": Huge([[]] * 100_000)}]
    Huge.taken = 0
    with pytest.raises(th.RuleError, match="too large"):
        th.leaves(wide_any)
    assert Huge.taken == 0
    with pytest.raises(th.RuleError):          # many empty branches, each small: the node budget still holds
        th.leaves([{"any": [[{"any": [[] for _ in range(50)]}] for _ in range(50)]}])
    with pytest.raises(th.RuleError, match="at most"):
        th.compile_conditions([{"field": "name", "op": "in", "value": ["a"] * (th.MAX_NODES + 1)}], FIELDS)


@pytest.mark.parametrize("rule", [
    "name is x", {"field": "name", "op": "is", "value": "x"}, [None], [3], ["x"], [[LEAF]], [{"any": None}],
    [{"any": []}], [{"any": [[]]}], [{"any": "x"}], [{"any": [3]}], [{"field": "name", "op": ["is"], "value": 1}],
    [{"field": ["name"], "op": "is", "value": 1}], [{"field": "name", "op": {"is": 1}, "value": 1}],
    [{"field": "size", "op": "gt", "value": [1, 2]}], [{"field": "name", "op": "in", "value": "abc"}]])
def test_a_malformed_rule_is_a_rule_error_never_a_type_error(rule):
    with pytest.raises(th.RuleError):
        th.compile_conditions(rule, FIELDS)
    with pytest.raises(th.RuleError):
        th.check(rule, FIELDS)


@pytest.mark.parametrize("not_a_rule", [None, {}, False, 0, "", "text", (), 42])
def test_only_a_list_is_a_rule_and_only_the_empty_list_matches_nothing(not_a_rule):
    with pytest.raises(th.RuleError):
        th.compile_conditions(not_a_rule, FIELDS)
    assert th.compile_conditions([], FIELDS) == ("false", [])


@pytest.mark.parametrize("malformed", [
    [42], [None], ["x"], [[LEAF]], [LEAF, [LEAF]], {"any": 42}, {"any": []}, {"any": None}, [{"any": "x"}],
    [{"any": [42]}], [{"any": [[]]}], [{"any": [LEAF, [LEAF, 7]]}], None, "text", 42, {},
    [{"field": "name"}], [{"op": "is", "value": 1}], [{"field": 3, "op": "is"}], [{"field": "name", "op": "near"}]])
def test_leaves_refuses_what_is_malformed_instead_of_skipping_it(malformed):
    with pytest.raises(th.RuleError):
        th.leaves(malformed)


def test_leaves_takes_a_rule_or_one_condition_and_alternatives_given_as_lists():
    assert th.leaves(LEAF) == [LEAF]                                    # a single condition gives itself
    assert th.leaves([]) == []
    other = {"field": "size", "op": "gt", "value": 3}
    rule = [LEAF, {"any": [other, [LEAF, other]]}]
    assert th.leaves(rule) == [LEAF, other, LEAF, other]
    assert th.leaves({"any": [[LEAF], other]}) == [LEAF, other]
    where, p = th.compile_conditions(rule, FIELDS)
    assert where == "(lower(t.name) = %s) and (((t.size > %s)) or ((lower(t.name) = %s) and (t.size > %s)))"
    assert p == ["x", 3, "x", 3]
