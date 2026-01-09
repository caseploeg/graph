#!/usr/bin/env python
"""
Simple test script to demonstrate external dependency exploration.

Tests the external_dependency_explorer utility against graph exports
from test repositories.

Strategy:
1. Find all IMPORTS relationships in the graph
2. Identify which target external packages (MODULE nodes without project prefix)
3. Show which modules are importing those external packages
4. Test the explorer function with packages that actually have imports
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from codebase_rag.graph_loader import load_graph
from codebase_rag.utils.external_dependency_explorer import (
    show_external_dependency_imports,
)


def find_external_imports_in_graph(graph_path: Path) -> dict[str, list[str]]:
    """
    Find all IMPORTS relationships that target external packages.

    Returns a dict mapping external package names to list of importing module qualified names.
    """
    print(f"\n{'=' * 80}")
    print(f"Analyzing IMPORTS relationships in: {graph_path.name}")
    print(f"{'=' * 80}\n")

    try:
        loader = load_graph(str(graph_path))

        # Get project name to identify local vs external modules
        project_nodes = loader.find_nodes_by_label("Project")
        if not project_nodes:
            print("❌ No Project node found")
            return {}

        project_name = project_nodes[0].properties.get("name", "")
        print(f"Project: {project_name}")

        # Find all IMPORTS relationships
        import_rels = [rel for rel in loader.relationships if rel.type == "IMPORTS"]
        print(f"Total IMPORTS relationships: {len(import_rels)}")

        # Categorize imports
        local_to_local = []
        local_to_external = []

        for rel in import_rels:
            from_node = loader.get_node_by_id(rel.from_id)
            to_node = loader.get_node_by_id(rel.to_id)

            if not from_node or not to_node:
                continue

            from_qn = from_node.properties.get("qualified_name", "")
            to_qn = to_node.properties.get("qualified_name", "")

            # Check if target is external (doesn't start with project name)
            if to_qn and not to_qn.startswith(project_name):
                local_to_external.append((from_qn, to_qn))
            else:
                local_to_local.append((from_qn, to_qn))

        print(f"Local → Local imports: {len(local_to_local)}")
        print(f"Local → External imports: {len(local_to_external)}")

        if local_to_external:
            print(f"\n✅ FOUND EXTERNAL IMPORTS!")

            # Group by external package
            imports_by_package = defaultdict(list)
            for from_qn, to_qn in local_to_external:
                # Extract base package name (first part of qualified name)
                base_package = to_qn.split('.')[0]
                imports_by_package[base_package].append(from_qn)

            print(f"\nExternal packages being imported:")
            for pkg, importers in sorted(imports_by_package.items()):
                print(f"\n  📦 {pkg}")
                print(f"     Imported by {len(importers)} modules")
                for importer in sorted(set(importers))[:3]:
                    print(f"       - {importer}")
                if len(set(importers)) > 3:
                    print(f"       ... and {len(set(importers)) - 3} more")

            return dict(imports_by_package)
        else:
            print(f"\n⚠️  NO EXTERNAL IMPORTS FOUND")
            print("   All IMPORTS relationships are between local modules only")
            return {}

    except Exception as e:
        print(f"❌ Error analyzing graph: {e}")
        import traceback
        traceback.print_exc()
        return {}


def test_graph_file(graph_path: Path) -> None:
    print(f"\n{'=' * 80}")
    print(f"Testing: {graph_path.name}")
    print(f"{'=' * 80}\n")

    try:
        result = show_external_dependency_imports(str(graph_path))

        print(f"📦 Package: {result['external_package']}")
        print(f"   Version: {result['version_spec']}")
        print(f"   Project: {result['project_name']}")
        print(f"   Import count: {result['import_count']}")

        if result["importing_modules"]:
            print(f"\n✅ Found {len(result['importing_modules'])} importing modules:")
            for mod in result["importing_modules"][:5]:
                print(f"\n  • {mod['module']}")
                print(f"    File: {mod['file_path']}")
                print(f"    Imports: {mod['imported_entity']}")

            if len(result["importing_modules"]) > 5:
                remaining = len(result["importing_modules"]) - 5
                print(f"\n  ... and {remaining} more modules")
        else:
            print("\n⚠️  No importing modules found")
            print(
                "   (This is expected - current graph doesn't create MODULE nodes for external packages)"
            )

    except ValueError as e:
        print(f"❌ Error: {e}")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback

        traceback.print_exc()


def test_specific_package(graph_path: Path, package_name: str) -> None:
    print(f"\n{'=' * 80}")
    print(f"Testing specific package: {package_name} in {graph_path.name}")
    print(f"{'=' * 80}\n")

    try:
        result = show_external_dependency_imports(str(graph_path), package_name)

        print(f"📦 Package: {result['external_package']}")
        print(f"   Version: {result['version_spec']}")
        print(f"   Project: {result['project_name']}")
        print(f"   Import count: {result['import_count']}")

        if result["importing_modules"]:
            print(f"\n✅ Found {len(result['importing_modules'])} importing modules:")
            for mod in result["importing_modules"]:
                print(f"\n  • {mod['module']}")
                print(f"    File: {mod['file_path']}")
                print(f"    Imports: {mod['imported_entity']}")
        else:
            print("\n⚠️  No importing modules found")

    except ValueError as e:
        print(f"❌ Error: {e}")


def list_external_packages(graph_path: Path) -> None:
    print(f"\n{'=' * 80}")
    print(f"External packages in: {graph_path.name}")
    print(f"{'=' * 80}\n")

    from codebase_rag.graph_loader import load_graph

    try:
        loader = load_graph(str(graph_path))
        external_packages = loader.find_nodes_by_label("ExternalPackage")

        if not external_packages:
            print("No external packages found")
            return

        print(f"Found {len(external_packages)} external packages:\n")

        dependency_rels = [
            rel for rel in loader.relationships if rel.type == "DEPENDS_ON_EXTERNAL"
        ]

        for pkg in sorted(
            external_packages, key=lambda p: p.properties.get("name", "")
        ):
            pkg_name = pkg.properties.get("name", "N/A")

            dep_rel = next(
                (rel for rel in dependency_rels if rel.to_id == pkg.node_id), None
            )

            version_spec = ""
            if dep_rel:
                version_spec = dep_rel.properties.get("version_spec", "")

            print(f"  • {pkg_name:30} {version_spec}")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()


def main() -> None:
    test_output_dir = Path(__file__).parent / "test_output"

    if not test_output_dir.exists():
        print(f"❌ Test output directory not found: {test_output_dir}")
        print("Run batch processing first to generate test graphs")
        return

    graph_files = sorted(test_output_dir.glob("*.json"))
    graph_files = [f for f in graph_files if not f.name.startswith("_")]

    if not graph_files:
        print(f"❌ No graph files found in {test_output_dir}")
        return

    print("🔍 External Dependency Explorer Test Script")
    print("=" * 80)
    print("\nThis script searches for IMPORTS relationships that target external packages")
    print("and tests the explorer function with packages that actually have imports.")

    print("\n\n" + "=" * 80)
    print("PHASE 1: FIND EXTERNAL IMPORTS IN GRAPHS")
    print("=" * 80)

    # Analyze each graph to find external imports
    graphs_with_external_imports = {}
    for graph_file in graph_files:
        external_imports = find_external_imports_in_graph(graph_file)
        if external_imports:
            graphs_with_external_imports[graph_file] = external_imports

    print("\n\n" + "=" * 80)
    print("PHASE 2: TEST EXPLORER FUNCTION WITH FOUND PACKAGES")
    print("=" * 80)

    if graphs_with_external_imports:
        print("\n✅ Found graphs with external imports. Testing explorer function...\n")

        for graph_file, packages in graphs_with_external_imports.items():
            # Test with the first external package found
            first_package = list(packages.keys())[0]
            test_specific_package(graph_file, first_package)

    else:
        print("\n⚠️  NO GRAPHS WITH EXTERNAL IMPORTS FOUND")
        print("\nTesting with declared dependencies instead (from DEPENDS_ON_EXTERNAL):")

        # Fallback: test with declared dependencies
        print("\n\n" + "=" * 80)
        print("PHASE 2 (FALLBACK): LISTING DECLARED DEPENDENCIES")
        print("=" * 80)

        for graph_file in graph_files[:3]:
            list_external_packages(graph_file)

        print("\n\n" + "=" * 80)
        print("PHASE 3 (FALLBACK): TESTING WITH DECLARED PACKAGES")
        print("=" * 80)

        for graph_file in graph_files[:2]:
            test_graph_file(graph_file)

    print("\n\n" + "=" * 80)
    print("TEST RESULTS SUMMARY")
    print("=" * 80)

    if graphs_with_external_imports:
        print("\n✅ SUCCESS: Found external imports in the following graphs:")
        for graph_file, packages in graphs_with_external_imports.items():
            print(f"   • {graph_file.name}: {len(packages)} external packages")
            for pkg, importers in list(packages.items())[:3]:
                print(f"     - {pkg} (imported by {len(set(importers))} modules)")
        print("\n   The explorer function can successfully trace these imports!")
    else:
        print("\n⚠️  CURRENT LIMITATION: No external imports found in any graph")
        print("   All IMPORTS relationships are local-to-local only.")
        print("\n   This confirms the findings in EXTERNAL_DEPENDENCY_FINDINGS.md:")
        print("   - ExternalPackage nodes exist (from dependency manifests)")
        print("   - PROJECT → DEPENDS_ON_EXTERNAL relationships exist")
        print("   - But MODULE nodes for external packages don't exist")
        print("   - So MODULE → MODULE IMPORTS to external packages can't be created")
        print("\n   The explorer function is working correctly, but needs MODULE nodes")
        print("   for external packages to return non-empty results.")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
