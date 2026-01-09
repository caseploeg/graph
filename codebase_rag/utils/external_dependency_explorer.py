from __future__ import annotations

import random
from pathlib import Path
from typing import TypedDict

from codebase_rag.graph_loader import GraphLoader, load_graph


class ImportingModule(TypedDict):
    module: str
    file_path: str
    imported_entity: str


class ExternalDependencyImports(TypedDict):
    external_package: str
    version_spec: str
    project_name: str
    importing_modules: list[ImportingModule]
    import_count: int


def show_external_dependency_imports(
    json_file_path: str | Path,
    package_name: str | None = None,
) -> ExternalDependencyImports:
    """
    Show which modules import a random external dependency.

    NOTE: The current graph implementation does not create MODULE nodes for
    external imports. This function will show empty results unless the graph
    is created with a version that generates MODULE nodes for external packages.

    The function looks for IMPORTS relationships where the target MODULE's
    qualified_name matches an external package name (without project prefix).
    """
    loader = load_graph(str(json_file_path))

    external_packages = loader.find_nodes_by_label("ExternalPackage")
    if not external_packages:
        msg = "No external dependencies found in graph"
        raise ValueError(msg)

    dependency_rels = [
        rel for rel in loader.relationships if rel.type == "DEPENDS_ON_EXTERNAL"
    ]

    if package_name:
        selected = next(
            (
                pkg
                for pkg in external_packages
                if pkg.properties.get("name") == package_name
            ),
            None,
        )
        if not selected:
            available = [pkg.properties["name"] for pkg in external_packages]
            msg = f"Package '{package_name}' not found. Available packages: {', '.join(available)}"
            raise ValueError(msg)
    else:
        selected = random.choice(external_packages)

    selected_package_name = selected.properties["name"]

    dep_rel = next(
        (rel for rel in dependency_rels if rel.to_id == selected.node_id),
        None,
    )

    if not dep_rel:
        version_spec = ""
        project_name = ""
    else:
        version_spec = dep_rel.properties.get("version_spec", "")
        project_node = loader.get_node_by_id(dep_rel.from_id)
        project_name = project_node.properties.get("name", "") if project_node else ""

    import_rels = [rel for rel in loader.relationships if rel.type == "IMPORTS"]

    importing_data: dict[int, str] = {}
    for rel in import_rels:
        imported_node = loader.get_node_by_id(rel.to_id)
        if not imported_node:
            continue
        imported_qn = imported_node.properties.get("qualified_name", "")

        if imported_qn == selected_package_name or imported_qn.startswith(
            selected_package_name + "."
        ):
            importing_data[rel.from_id] = imported_qn

    importing_modules: list[ImportingModule] = []
    for module_id, imported_qn in importing_data.items():
        module = loader.get_node_by_id(module_id)
        if not module:
            continue
        importing_modules.append({
            "module": module.properties.get("qualified_name", ""),
            "file_path": module.properties.get("path", ""),
            "imported_entity": imported_qn,
        })

    importing_modules.sort(key=lambda x: x["module"])

    return {
        "external_package": selected_package_name,
        "version_spec": version_spec,
        "project_name": project_name,
        "importing_modules": importing_modules,
        "import_count": len(importing_modules),
    }
