from __future__ import annotations

import argparse
import ast
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT_PACKAGES = {"app", "tests", "tools"}
SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "graphify-out",
    "logs",
    "project_analysis",
    "storage",
}
LARGE_MODULE_LOC = 250
LARGE_CLASS_LOC = 120
LARGE_FUNCTION_LOC = 60


@dataclass(frozen=True)
class FunctionInfo:
    name: str
    qualname: str
    module: str
    lineno: int
    loc: int
    is_async: bool
    calls: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class ClassInfo:
    name: str
    qualname: str
    module: str
    lineno: int
    loc: int
    methods: tuple[str, ...]


@dataclass
class ModuleInfo:
    module: str
    path: Path
    package: str
    loc: int
    internal_imports: set[str] = field(default_factory=set)
    external_imports: set[str] = field(default_factory=set)
    classes: list[ClassInfo] = field(default_factory=list)
    functions: list[FunctionInfo] = field(default_factory=list)


@dataclass
class Analysis:
    root: Path
    modules: dict[str, ModuleInfo]
    import_edges: set[tuple[str, str]]
    package_edges: Counter[tuple[str, str]]
    call_refs: Counter[str]


class ModuleVisitor(ast.NodeVisitor):
    def __init__(self, module: str, path: Path, tree: ast.AST) -> None:
        self.module = module
        self.path = path
        self.tree = tree
        self.classes: list[ClassInfo] = []
        self.functions: list[FunctionInfo] = []
        self.imports: set[str] = set()
        self.external_imports: set[str] = set()
        self._scope: list[str] = []
        self._class_stack: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._add_import(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        imported = self._resolve_from_import(node)
        if imported:
            self._add_import(imported)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualname = self._qualname(node.name)
        methods = tuple(
            child.name
            for child in node.body
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
        )
        self.classes.append(
            ClassInfo(
                name=node.name,
                qualname=qualname,
                module=self.module,
                lineno=node.lineno,
                loc=_node_loc(node),
                methods=methods,
            )
        )
        self._scope.append(node.name)
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node, is_async=True)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, is_async: bool) -> None:
        qualname = self._qualname(node.name)
        calls = _calls_in(node)
        self.functions.append(
            FunctionInfo(
                name=node.name,
                qualname=qualname,
                module=self.module,
                lineno=node.lineno,
                loc=_node_loc(node),
                is_async=is_async,
                calls=calls,
            )
        )
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def _qualname(self, name: str) -> str:
        return ".".join([self.module, *self._scope, name])

    def _add_import(self, imported: str) -> None:
        root = imported.split(".", 1)[0]
        if root in ROOT_PACKAGES:
            self.imports.add(imported)
        else:
            self.external_imports.add(root)

    def _resolve_from_import(self, node: ast.ImportFrom) -> str | None:
        module = node.module or ""
        if node.level == 0:
            return module or None

        parts = self.module.split(".")
        package_parts = parts[:-1]
        keep = max(0, len(package_parts) - node.level + 1)
        base = package_parts[:keep]
        if module:
            base.extend(module.split("."))
        return ".".join(base) if base else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate project architecture graphs and review.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path("project_analysis"))
    parser.add_argument(
        "--render", action="store_true", help="Render DOT files to SVG when graphviz is installed."
    )
    args = parser.parse_args()

    root = args.root.resolve()
    output = args.output if args.output.is_absolute() else root / args.output
    analysis = analyze(root)
    write_outputs(analysis, output, render=args.render)
    return 0


def analyze(root: Path) -> Analysis:
    modules: dict[str, ModuleInfo] = {}
    for path in iter_python_files(root):
        module = module_name(root, path)
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        visitor = ModuleVisitor(module, path, tree)
        visitor.visit(tree)
        modules[module] = ModuleInfo(
            module=module,
            path=path,
            package=package_name(module),
            loc=count_loc(source),
            internal_imports=visitor.imports,
            external_imports=visitor.external_imports,
            classes=visitor.classes,
            functions=visitor.functions,
        )

    module_names = set(modules)
    import_edges: set[tuple[str, str]] = set()
    package_edges: Counter[tuple[str, str]] = Counter()
    for module, info in modules.items():
        for imported in info.internal_imports:
            target = nearest_module(imported, module_names)
            if target and target != module:
                import_edges.add((module, target))
                source_package = package_name(module)
                target_package = package_name(target)
                if source_package != target_package:
                    package_edges[(source_package, target_package)] += 1

    call_refs = Counter[str]()
    function_index = {
        func.name: func.qualname for info in modules.values() for func in info.functions
    }
    for info in modules.values():
        for func in info.functions:
            for call in func.calls:
                if call in function_index:
                    call_refs[function_index[call]] += 1

    return Analysis(
        root=root,
        modules=modules,
        import_edges=import_edges,
        package_edges=package_edges,
        call_refs=call_refs,
    )


def iter_python_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*.py"):
        relative_parts = path.relative_to(root).parts
        if any(part in SKIP_DIRS for part in relative_parts):
            continue
        if relative_parts[0] not in ROOT_PACKAGES:
            continue
        files.append(path)
    return sorted(files)


def module_name(root: Path, path: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    return ".".join(relative.parts)


def package_name(module: str) -> str:
    parts = module.split(".")
    if len(parts) >= 2 and parts[0] == "app":
        return ".".join(parts[:2])
    return parts[0]


def nearest_module(imported: str, module_names: set[str]) -> str | None:
    candidate = imported
    while candidate:
        if candidate in module_names:
            return candidate
        candidate = candidate.rsplit(".", 1)[0] if "." in candidate else ""
    return None


def count_loc(source: str) -> int:
    return sum(
        1 for line in source.splitlines() if line.strip() and not line.lstrip().startswith("#")
    )


def _node_loc(node: ast.AST) -> int:
    end = getattr(node, "end_lineno", None)
    start = getattr(node, "lineno", None)
    if end is None or start is None:
        return 0
    return max(0, end - start + 1)


def _calls_in(node: ast.AST) -> set[str]:
    calls: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        if isinstance(child.func, ast.Name):
            calls.add(child.func.id)
        elif isinstance(child.func, ast.Attribute):
            calls.add(child.func.attr)
    return calls


def write_outputs(analysis: Analysis, output: Path, render: bool) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "architecture_graph.dot").write_text(architecture_dot(analysis), encoding="utf-8")
    (output / "imports_graph.dot").write_text(imports_dot(analysis), encoding="utf-8")
    (output / "dependency_graph.dot").write_text(dependency_dot(analysis), encoding="utf-8")
    (output / "findings.md").write_text(findings_markdown(analysis), encoding="utf-8")
    (output / "architecture_review.md").write_text(
        architecture_review_markdown(analysis), encoding="utf-8"
    )
    if render:
        render_dot_files(output)


def architecture_dot(analysis: Analysis) -> str:
    lines = [
        "digraph architecture {",
        '  graph [rankdir=LR, bgcolor="white", splines=true];',
        '  node [shape=box, style="rounded,filled", fillcolor="#f8fafc", color="#94a3b8", fontname="Inter"];',
        '  edge [color="#64748b", arrowsize=0.7];',
    ]
    packages = sorted({info.package for info in analysis.modules.values()})
    for package in packages:
        lines.append(f'  "{package}" [shape=folder, fillcolor="#e2e8f0"];')
    for module, info in sorted(analysis.modules.items()):
        label = (
            f"{module}\\n{info.loc} LOC • {len(info.classes)} classes • {len(info.functions)} funcs"
        )
        lines.append(f'  "{module}" [label="{_dot_escape(label)}"];')
        lines.append(f'  "{info.package}" -> "{module}" [style=dotted, label="contains"];')
        for cls in info.classes:
            lines.append(
                f'  "{cls.qualname}" [shape=component, label="{_dot_escape(cls.name)}\\n{cls.loc} LOC"];'
            )
            lines.append(f'  "{module}" -> "{cls.qualname}" [label="class"];')
    lines.append("}")
    return "\n".join(lines) + "\n"


def imports_dot(analysis: Analysis) -> str:
    lines = [
        "digraph imports {",
        '  graph [rankdir=LR, bgcolor="white", splines=true];',
        '  node [shape=box, style="rounded,filled", fillcolor="#f8fafc", color="#94a3b8", fontname="Inter"];',
        '  edge [color="#2563eb", arrowsize=0.7];',
    ]
    for module in sorted(analysis.modules):
        lines.append(f'  "{module}";')
    for source, target in sorted(analysis.import_edges):
        lines.append(f'  "{source}" -> "{target}";')
    lines.append("}")
    return "\n".join(lines) + "\n"


def dependency_dot(analysis: Analysis) -> str:
    lines = [
        "digraph dependencies {",
        '  graph [rankdir=LR, bgcolor="white", splines=true];',
        '  node [shape=box, style="rounded,filled", fillcolor="#f8fafc", color="#94a3b8", fontname="Inter"];',
        '  edge [color="#0f766e", arrowsize=0.7];',
    ]
    packages = sorted({info.package for info in analysis.modules.values()})
    for package in packages:
        lines.append(f'  "{package}";')
    for (source, target), count in sorted(analysis.package_edges.items()):
        lines.append(f'  "{source}" -> "{target}" [label="{count}"];')
    lines.append("}")
    return "\n".join(lines) + "\n"


def findings_markdown(analysis: Analysis) -> str:
    cycles = import_cycles(analysis.import_edges)
    fan_in, fan_out = fan_counts(analysis)
    large_modules = sorted(
        (info for info in analysis.modules.values() if info.loc >= LARGE_MODULE_LOC),
        key=lambda info: info.loc,
        reverse=True,
    )
    large_classes = sorted(all_classes(analysis), key=lambda cls: cls.loc, reverse=True)
    large_functions = sorted(all_functions(analysis), key=lambda func: func.loc, reverse=True)
    unused_files = unused_modules(analysis, fan_in)
    unused_functions = unused_function_candidates(analysis)
    external = external_dependencies(analysis)

    sections = [
        "# Findings",
        "",
        "Generated by `tools.architecture_review` from Python AST. Dead-code results are static heuristics and should be verified before deletion.",
        "",
        "## Cyclic Imports",
        _cycle_lines(cycles),
        "",
        "## Strong Coupling",
        _coupling_lines(fan_in, fan_out),
        "",
        "## Potential Scaling Problems",
        _scaling_lines(analysis, large_modules),
        "",
        "## Suspicious Dependencies",
        _external_lines(external),
        "",
        "## Unused Files",
        _module_lines(unused_files[:20]),
        "",
        "## Unused Functions",
        _function_lines(unused_functions[:30]),
        "",
        "## Large Classes",
        _class_lines([cls for cls in large_classes if cls.loc >= LARGE_CLASS_LOC][:20]),
        "",
        "## Large Functions",
        _function_lines([func for func in large_functions if func.loc >= LARGE_FUNCTION_LOC][:20]),
        "",
    ]
    return "\n".join(sections)


def architecture_review_markdown(analysis: Analysis) -> str:
    cycles = import_cycles(analysis.import_edges)
    fan_in, fan_out = fan_counts(analysis)
    package_rows = sorted(analysis.package_edges.items(), key=lambda item: item[1], reverse=True)
    top_modules = sorted(
        analysis.modules.values(),
        key=lambda info: fan_in.get(info.module, 0) + fan_out.get(info.module, 0),
        reverse=True,
    )[:10]
    lines = [
        "# Architecture Review",
        "",
        "## Summary",
        "",
        f"- Modules analyzed: {len(analysis.modules)}",
        f"- Internal import edges: {len(analysis.import_edges)}",
        f"- Package dependency edges: {len(analysis.package_edges)}",
        f"- Cyclic import groups: {len(cycles)}",
        "",
        "The project is split into clear domains: exchange adapters, market feature collection, signal detection, paper trading, data persistence, and Telegram UX. The highest architectural risk is the Telegram layer depending directly on repositories, market features, paper statistics, formatting, and mutable config writes. This makes Telegram callbacks a broad integration surface and raises regression risk when adding new UI actions.",
        "",
        "## Dependency Hotspots",
        "",
        _module_table(top_modules, fan_in, fan_out),
        "",
        "## Package Dependencies",
        "",
        _package_table(package_rows),
        "",
        "## Review Notes",
        "",
        "- Keep exchange clients free of private trading endpoints while paper mode is enabled.",
        "- Keep Telegram callback handlers thin; move settings writes and signal mutations behind application services.",
        "- Keep rolling market buffers bounded and verify stale-data handling when adding Hyperliquid.",
        "- Treat unused-file and unused-function results as review candidates, not automatic deletion targets.",
        "",
        "## Recommended Next Steps",
        "",
        "1. Break the `app.telegram.commands` to `app.telegram.bot` type-import cycle with `TYPE_CHECKING`.",
        "2. Move settings mutation/persistence into a small config service with explicit save-result handling.",
        "3. Add integration tests for Telegram callbacks that mutate signal status or config.",
        "4. Add architecture guardrails once module boundaries stabilize, for example no imports from `app.telegram` into market/data/signal modules.",
        "",
    ]
    return "\n".join(lines)


def import_cycles(edges: set[tuple[str, str]]) -> list[list[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    nodes: set[str] = set()
    for source, target in edges:
        graph[source].add(target)
        nodes.add(source)
        nodes.add(target)

    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    cycles: list[list[str]] = []

    def strongconnect(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)

        for target in graph[node]:
            if target not in indices:
                strongconnect(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[target])

        if lowlinks[node] == indices[node]:
            component: list[str] = []
            while stack:
                item = stack.pop()
                on_stack.remove(item)
                component.append(item)
                if item == node:
                    break
            if len(component) > 1:
                cycles.append(sorted(component))

    for node in sorted(nodes):
        if node not in indices:
            strongconnect(node)
    return sorted(cycles, key=lambda items: (len(items), items))


def fan_counts(analysis: Analysis) -> tuple[Counter[str], Counter[str]]:
    fan_in: Counter[str] = Counter()
    fan_out: Counter[str] = Counter()
    for source, target in analysis.import_edges:
        fan_out[source] += 1
        fan_in[target] += 1
    return fan_in, fan_out


def all_classes(analysis: Analysis) -> list[ClassInfo]:
    return [cls for info in analysis.modules.values() for cls in info.classes]


def all_functions(analysis: Analysis) -> list[FunctionInfo]:
    return [func for info in analysis.modules.values() for func in info.functions]


def unused_modules(analysis: Analysis, fan_in: Counter[str]) -> list[ModuleInfo]:
    candidates = []
    for module, info in analysis.modules.items():
        if (
            module.endswith(".__init__")
            or module in {"app.main"}
            or module.startswith("tests.")
            or module.startswith("tools.")
        ):
            continue
        if fan_in.get(module, 0) == 0:
            candidates.append(info)
    return sorted(candidates, key=lambda info: info.module)


def unused_function_candidates(analysis: Analysis) -> list[FunctionInfo]:
    ignored = {"main", "start", "stop", "__init__", "__aenter__", "__aexit__"}
    candidates = []
    for func in all_functions(analysis):
        if func.name in ignored or func.name.startswith("test_"):
            continue
        if func.qualname in analysis.call_refs:
            continue
        if func.name.startswith("_") and not func.name.startswith("__"):
            continue
        if func.module.startswith("tests."):
            continue
        candidates.append(func)
    return sorted(candidates, key=lambda func: (func.module, func.lineno))


def external_dependencies(analysis: Analysis) -> Counter[str]:
    counter: Counter[str] = Counter()
    for info in analysis.modules.values():
        counter.update(
            name
            for name in info.external_imports
            if name not in sys.stdlib_module_names and name != "__future__"
        )
    return counter


def render_dot_files(output: Path) -> None:
    dot = shutil.which("dot")
    if dot is None:
        return
    for path in output.glob("*_graph.dot"):
        svg_path = path.with_suffix(".svg")
        subprocess.run([dot, "-Tsvg", str(path), "-o", str(svg_path)], check=False)  # noqa: S603


def _cycle_lines(cycles: list[list[str]]) -> str:
    if not cycles:
        return "No cyclic imports detected."
    return "\n".join("- " + " -> ".join(cycle) for cycle in cycles)


def _coupling_lines(fan_in: Counter[str], fan_out: Counter[str]) -> str:
    modules = sorted(
        set(fan_in) | set(fan_out), key=lambda item: fan_in[item] + fan_out[item], reverse=True
    )
    if not modules:
        return "No internal coupling detected."
    rows = ["| Module | Fan-in | Fan-out |", "|---|---:|---:|"]
    for module in modules[:15]:
        rows.append(f"| `{module}` | {fan_in[module]} | {fan_out[module]} |")
    return "\n".join(rows)


def _scaling_lines(analysis: Analysis, large_modules: list[ModuleInfo]) -> str:
    rows = [
        f"- Current graph has {len(analysis.modules)} Python modules and {len(analysis.import_edges)} internal import edges.",
    ]
    if large_modules:
        rows.append("- Large modules may become harder to test and review:")
        for info in large_modules[:10]:
            rows.append(f"  - `{info.module}`: {info.loc} LOC")
    else:
        rows.append("- No module exceeds the large-module threshold.")
    return "\n".join(rows)


def _external_lines(external: Counter[str]) -> str:
    if not external:
        return "No non-stdlib external imports detected."
    rows = ["| Dependency | Import Count |", "|---|---:|"]
    for name, count in external.most_common(20):
        rows.append(f"| `{name}` | {count} |")
    return "\n".join(rows)


def _module_lines(modules: list[ModuleInfo]) -> str:
    if not modules:
        return "No unused-file candidates detected."
    return "\n".join(f"- `{info.module}` ({info.path.name}, {info.loc} LOC)" for info in modules)


def _function_lines(functions: list[FunctionInfo]) -> str:
    if not functions:
        return "No function candidates detected."
    return "\n".join(
        f"- `{func.qualname}` ({func.module}:{func.lineno}, {func.loc} LOC)"
        for func in functions
    )


def _class_lines(classes: list[ClassInfo]) -> str:
    if not classes:
        return "No large classes detected."
    return "\n".join(
        f"- `{cls.qualname}` ({cls.module}:{cls.lineno}, {cls.loc} LOC)" for cls in classes
    )


def _module_table(modules: list[ModuleInfo], fan_in: Counter[str], fan_out: Counter[str]) -> str:
    if not modules:
        return "No module hotspots detected."
    rows = [
        "| Module | LOC | Classes | Functions | Fan-in | Fan-out |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for info in modules:
        rows.append(
            f"| `{info.module}` | {info.loc} | {len(info.classes)} | {len(info.functions)} | {fan_in[info.module]} | {fan_out[info.module]} |"
        )
    return "\n".join(rows)


def _package_table(rows: list[tuple[tuple[str, str], int]]) -> str:
    if not rows:
        return "No cross-package dependencies detected."
    table = ["| From | To | Imports |", "|---|---|---:|"]
    for (source, target), count in rows:
        table.append(f"| `{source}` | `{target}` | {count} |")
    return "\n".join(table)


def _dot_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


if __name__ == "__main__":
    raise SystemExit(main())
