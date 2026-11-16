"""The SessionManager contract must not drift from the mixins it describes.

`coworker/server/manager_base.py` declares the state and the cross-mixin methods that
*some* mixin relies on but does not define. It is the reason the Python tree can be
type-checked at all: without it a mixin looks like it is calling names that do not
exist, and mypy reports ~1000 errors instead of ~440.

These tests keep the contract honest. If someone adds a method to a mixin and deletes
its declaration here, or renames shared state, this fails -- which is the whole point,
because the type checker cannot tell "declared in the contract" from "actually
implemented somewhere".
"""

from __future__ import annotations

import ast
import importlib
import pathlib


from coworker.server.manager import SessionManager
from coworker.server.manager_base import ManagerBase

SERVER_DIR = pathlib.Path(__file__).resolve().parents[1] / "coworker" / "server"


def _contract_members() -> tuple[set[str], set[str]]:
    """(method names, attribute names) declared by ManagerBase, read from the AST.

    Read from source rather than `vars()`: the attributes are annotation-only, so they
    do not exist in the class dict at runtime.
    """
    tree = ast.parse((SERVER_DIR / "manager_base.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ManagerBase")
    methods = {
        n.name for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    attrs = {
        n.target.id
        for n in cls.body
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
    }
    return methods, attrs


def _mixin_modules() -> list[str]:
    return sorted(p.stem for p in SERVER_DIR.glob("manager_*.py") if p.stem != "manager_base")


def test_every_mixin_inherits_the_contract() -> None:
    """All mixins must see the same declarations, or the checker reports phantom errors."""
    seen = 0
    for mod_name in _mixin_modules():
        mod = importlib.import_module("coworker.server." + mod_name)
        for attr in vars(mod).values():
            if (
                isinstance(attr, type)
                and attr.__name__.endswith("Mixin")
                and attr.__module__ == mod.__name__
            ):
                seen += 1
                assert issubclass(attr, ManagerBase), (
                    "%s.%s does not inherit ManagerBase" % (mod_name, attr.__name__)
                )
    assert seen >= 15, "expected at least 15 mixins, found %d" % seen


def test_contract_methods_are_all_implemented_somewhere() -> None:
    """A declared method must have a real implementation on SessionManager.

    Otherwise the contract describes something that does not exist, and the type checker
    would be content while the call fails at runtime.
    """
    methods, _ = _contract_members()
    assert methods, "contract declares no methods"

    missing = []
    for name in sorted(methods):
        impl = getattr(SessionManager, name, None)
        if impl is None:
            missing.append(name)
            continue
        # still resolving to the contract's own body means no mixin overrode it
        if getattr(impl, "__qualname__", "").startswith("ManagerBase."):
            missing.append(name)
    assert not missing, "declared in ManagerBase but implemented by no mixin: %s" % missing


def test_cross_mixin_calls_are_declared() -> None:
    """The other direction: a mixin calling a method it does not define must find it
    declared in the contract.

    `test_contract_methods_are_all_implemented_somewhere` catches a contract that
    promises too much. This catches one that promises too little -- which is the
    failure mode a negative control actually hit: deleting a declaration left every
    test green while mypy silently regressed.
    """
    methods, attrs = _contract_members()

    # What each class actually implements, and what `self.<name>(` it calls.
    defined: dict[str, set[str]] = {}
    called: dict[str, set[str]] = {}
    # manager.py holds the facade itself, so its __init__ is where a lot of the shared
    # state is assigned. Leave it out and those attributes look undefined.
    for path in SERVER_DIR.glob("manager*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
            cls_defs: set[str] = set()
            cls_calls: set[str] = set()
            for node in ast.walk(cls):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    cls_defs.add(node.name)
                # Class-body constants / annotations (`TEAM_WAKE_CAP_PER_HOUR = 12`,
                # `X: int`). They are attributes of the class, so `self.X` is fine.
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name):
                            cls_defs.add(target.id)
                # `self.X = ...` anywhere in the class defines instance state.
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if (
                            isinstance(target, ast.Attribute)
                            and isinstance(target.value, ast.Name)
                            and target.value.id == "self"
                        ):
                            cls_defs.add(target.attr)
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "self"
                ):
                    cls_calls.add(node.func.attr)
                # self.<attr> reads also need declaring when the class has no assignment
                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "self"
                ):
                    cls_calls.add(node.attr)
            defined[cls.name] = cls_defs
            called[cls.name] = cls_calls

    # The facade inherits everything; a name is "available" if any manager class
    # defines it or the contract declares it.
    all_defs = set().union(*defined.values()) if defined else set()
    all_declared = methods | attrs

    undeclared: dict[str, list[str]] = {}
    for cls_name, calls in called.items():
        for name in sorted(calls - defined[cls_name] - all_defs - all_declared):
            if name.startswith("__"):
                continue
            undeclared.setdefault(cls_name, []).append(name)

    assert not undeclared, (
        "names used via self.* that no manager class defines and ManagerBase does not "
        "declare: %s" % undeclared
    )


def test_contract_state_is_assigned_somewhere() -> None:
    """Declared attributes must actually be assigned.

    This is what stops the contract from accumulating hopeful annotations: a name nothing
    ever assigns is a latent AttributeError, not a contract.
    """
    _, attrs = _contract_members()
    assert attrs, "contract declares no state"

    assigned: set[str] = set()
    for path in SERVER_DIR.glob("manager*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                    ):
                        assigned.add(target.attr)
            elif isinstance(node, ast.For) and isinstance(node.target, ast.Attribute):
                target = node.target
                if isinstance(target.value, ast.Name) and target.value.id == "self":
                    assigned.add(target.attr)
            elif isinstance(node, ast.withitem) and isinstance(node.optional_vars, ast.Attribute):
                target = node.optional_vars
                if isinstance(target.value, ast.Name) and target.value.id == "self":
                    assigned.add(target.attr)

    # Uppercase class-level constants are legitimately declared once and never
    # re-assigned on an instance.
    unassigned = sorted(a for a in attrs - assigned if not a.isupper())
    assert not unassigned, (
        "declared in ManagerBase but never assigned in any manager module: %s" % unassigned
    )


def _is_nie(node) -> bool:
    """True for `raise NotImplementedError` and `raise NotImplementedError(...)`."""
    if node is None:
        return False
    target = node.func if isinstance(node, ast.Call) else node
    return getattr(target, "id", "") == "NotImplementedError"


def test_contract_methods_raise_rather_than_return_none() -> None:
    """`raise NotImplementedError` bodies are deliberate.

    A `...`/`pass` body is an *empty* body: mypy reports [empty-body] for each
    non-None-returning method (24 errors), and a wiring mistake would silently return
    None instead of failing loudly.
    """
    tree = ast.parse((SERVER_DIR / "manager_base.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ManagerBase")
    offenders = []
    for node in cls.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = [
            b
            for b in node.body
            if not (isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant))
        ]
        is_raise = len(body) == 1 and isinstance(body[0], ast.Raise) and _is_nie(body[0].exc)
        if not is_raise:
            offenders.append(node.name)
    assert not offenders, "contract methods must raise NotImplementedError: %s" % offenders


def test_facade_still_constructs(tmp_path) -> None:
    """The contract is annotation-only, so building a real manager must be unaffected.

    Uses pytest's tmp_path rather than tempfile.TemporaryDirectory: on Windows the
    manager keeps SQLite handles open, and an eager rmtree collides with them.
    """
    mgr = SessionManager(data_dir=str(tmp_path), model="gpt-5.6-sol")
    assert mgr.model == "gpt-5.6-sol"
    assert isinstance(mgr, ManagerBase)


def test_sibling_methods_are_declared_on_the_contract() -> None:
    """A mixin calling a sibling mixin's method must find it on ManagerBase.

    Weaker rule, stronger guarantee: `test_cross_mixin_calls_are_declared` accepts a
    name defined by *any* manager class, so `InboxMixin` calling
    `self.promote_workspace` (defined in `WorkspacesMixin`) slipped through -- and mypy
    reported `"InboxMixin" has no attribute "promote_workspace"`. Here a name only
    counts if this very class defines it or the contract declares it.

    Calls made from the facade (`manager.py` -> SessionManager) are exempt: it inherits
    every mixin, so those names genuinely resolve.
    """
    methods, attrs = _contract_members()
    declared = methods | attrs

    def own_members(tree: ast.Module, cls: ast.ClassDef) -> set[str]:
        names: set[str] = set()
        for node in ast.walk(cls):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(node.name)
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
                    elif (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                    ):
                        names.add(target.attr)
        return names

    undeclared: dict[str, list[str]] = {}
    for path in SERVER_DIR.glob("manager*.py"):
        if path.name == "manager_base.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
            # The facade inherits everything; skip it.
            if cls.name == "SessionManager":
                continue
            own = own_members(tree, cls)
            used: set[str] = set()
            for node in ast.walk(cls):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "self"
                ):
                    used.add(node.func.attr)
                elif (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "self"
                ):
                    used.add(node.attr)
            for name in sorted(used - own - declared):
                if name.startswith("__"):
                    continue
                undeclared.setdefault(cls.name, []).append(name)

    assert not undeclared, (
        "these mixins call methods they do not define and ManagerBase does not declare, "
        "so each fails to type-check on its own: %s" % undeclared
    )
