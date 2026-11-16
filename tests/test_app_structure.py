"""PasteFlowApp 믹스인 구조 시험 — 파일을 가져오지 않고 ast로 읽는다(Qt·Windows 없이 돈다).

믹스인은 같은 이름을 두 곳에 정의하면 오류 없이 한쪽이 가려진다. 그래서 이름이 겹치지 않는지 본다.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "pasteflow" / "main.py"
APP = ROOT / "pasteflow" / "app"


def _parse(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _app_classes():
    """pasteflow/app/의 클래스 이름 → ClassDef"""
    out = {}
    for path in sorted(APP.glob("*.py")):
        for node in _parse(path).body:
            if isinstance(node, ast.ClassDef):
                out[node.name] = node
    return out


def _paste_flow_app():
    for node in _parse(MAIN).body:
        if isinstance(node, ast.ClassDef) and node.name == "PasteFlowApp":
            return node
    raise AssertionError("pasteflow/main.py에 PasteFlowApp이 없다")


def _member_names(cls):
    """클래스 몸통에 정의된 메서드·클래스 상수 이름"""
    names = []
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names += [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return names


def _mixins():
    app_classes = _app_classes()
    bases = [b.id for b in _paste_flow_app().bases if isinstance(b, ast.Name)]
    return [app_classes[b] for b in bases if b in app_classes]


def test_all_bases_are_app_mixins():
    app_classes = _app_classes()
    bases = [b.id if isinstance(b, ast.Name) else ast.dump(b) for b in _paste_flow_app().bases]
    assert bases, "PasteFlowApp에 믹스인이 없다"
    missing = [b for b in bases if b not in app_classes]
    assert not missing, f"pasteflow/app/에 없는 부모 클래스: {missing}"


def test_every_mixin_in_app_is_used():
    used = {c.name for c in _mixins()}
    defined = {n for n in _app_classes() if n.endswith("Mixin")}
    assert defined == used, f"부모 목록에 없는 믹스인: {sorted(defined - used)}"


def test_no_name_defined_twice():
    owners = {}
    for cls in [_paste_flow_app(), *_mixins()]:
        for name in _member_names(cls):
            owners.setdefault(name, []).append(cls.name)
    dup = {n: o for n, o in owners.items() if len(o) > 1}
    assert not dup, f"두 곳 이상에 정의된 이름: {dup}"


def test_mixins_have_no_init():
    with_init = [c.name for c in _mixins() if "__init__" in _member_names(c)]
    assert not with_init, f"__init__이 있는 믹스인: {with_init}"


def test_app_does_not_import_main():
    bad = []
    for path in sorted(APP.glob("*.py")):
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.ImportFrom) and node.module == "pasteflow.main":
                bad.append(path.name)
            elif isinstance(node, ast.Import) and any(a.name == "pasteflow.main" for a in node.names):
                bad.append(path.name)
    assert not bad, f"pasteflow.main을 가져오는 파일(순환 import): {bad}"
