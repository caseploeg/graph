#!/usr/bin/env python
"""
Comprehensive test to verify external MODULE node creation works
across all supported languages.
"""

import json
import subprocess
import sys
from pathlib import Path

from codebase_rag.graph_loader import load_graph


def test_repo(repo_name: str, expected_language: str) -> dict:
    """Test a single repository and return results."""
    base_path = Path("/Users/caseploeg/code-graph-rag")
    repo_path = base_path / "batch" / "test_repos" / repo_name
    output_path = base_path / "batch" / "test_output" / f"{repo_name}_lang_test.json"

    if not repo_path.exists():
        return {
            "repo": repo_name,
            "language": expected_language,
            "status": "SKIP",
            "reason": "Repository not found",
        }

    print(f"\n{'=' * 80}")
    print(f"Testing: {repo_name} ({expected_language})")
    print(f"{'=' * 80}\n")

    try:
        cmd = [
            "uv",
            "run",
            "cgr",
            "export-json",
            "--repo-path",
            str(repo_path),
            "-o",
            str(output_path),
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120
        )

        if result.returncode != 0:
            return {
                "repo": repo_name,
                "language": expected_language,
                "status": "ERROR",
                "reason": f"Graph generation failed: {result.stderr[:200]}",
            }

        loader = load_graph(str(output_path))

        all_modules = loader.find_nodes_by_label("Module")
        external_modules = [m for m in all_modules if "path" not in m.properties]
        local_modules = [m for m in all_modules if "path" in m.properties]

        import_rels = [rel for rel in loader.relationships if rel.type == "IMPORTS"]

        local_to_local = 0
        local_to_external = 0

        for rel in import_rels:
            to_node = loader.get_node_by_id(rel.to_id)
            if to_node:
                is_external = "path" not in to_node.properties
                if is_external:
                    local_to_external += 1
                else:
                    local_to_local += 1

        external_package_names = set()
        for mod in external_modules:
            qn = mod.properties.get("qualified_name", "")
            base = qn.split(".")[0] if "." in qn else qn.split("::")[0] if "::" in qn else qn.split("/")[0] if "/" in qn else qn
            external_package_names.add(base)

        print(f"✅ MODULE Nodes:")
        print(f"   Total: {len(all_modules)}")
        print(f"   Local: {len(local_modules)}")
        print(f"   External: {len(external_modules)}")

        print(f"\n✅ IMPORTS Relationships:")
        print(f"   Total: {len(import_rels)}")
        print(f"   Local → Local: {local_to_local}")
        print(f"   Local → External: {local_to_external}")

        if external_modules:
            print(f"\n✅ External Packages (top 10):")
            for pkg in sorted(external_package_names)[:10]:
                count = sum(
                    1
                    for m in external_modules
                    if m.properties.get("qualified_name", "").startswith(pkg)
                )
                print(f"   - {pkg} ({count} modules)")

            if len(external_package_names) > 10:
                print(f"   ... and {len(external_package_names) - 10} more")

        status = "PASS" if external_modules else "WARN"
        reason = (
            f"{len(external_modules)} external nodes created"
            if external_modules
            else "No external imports found (may be expected)"
        )

        return {
            "repo": repo_name,
            "language": expected_language,
            "status": status,
            "total_modules": len(all_modules),
            "local_modules": len(local_modules),
            "external_modules": len(external_modules),
            "total_imports": len(import_rels),
            "local_to_local": local_to_local,
            "local_to_external": local_to_external,
            "unique_external_packages": len(external_package_names),
            "reason": reason,
        }

    except subprocess.TimeoutExpired:
        return {
            "repo": repo_name,
            "language": expected_language,
            "status": "ERROR",
            "reason": "Timeout (120s exceeded)",
        }
    except Exception as e:
        return {
            "repo": repo_name,
            "language": expected_language,
            "status": "ERROR",
            "reason": f"Exception: {str(e)[:200]}",
        }


def main():
    test_cases = [
        ("click", "Python"),
        ("django", "Python"),
        ("got", "TypeScript"),
        ("go-cmp", "Go"),
        ("log", "Rust"),
        ("okio", "Java"),
    ]

    print("\n" + "=" * 80)
    print("COMPREHENSIVE LANGUAGE TEST: External MODULE Node Creation")
    print("=" * 80)
    print("\nTesting external import tracking across all supported languages.")
    print("Each test verifies that external MODULE nodes are created correctly.\n")

    results = []
    for repo_name, language in test_cases:
        result = test_repo(repo_name, language)
        results.append(result)

    print("\n\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)

    passed = sum(1 for r in results if r["status"] == "PASS")
    warned = sum(1 for r in results if r["status"] == "WARN")
    failed = sum(1 for r in results if r["status"] == "ERROR")
    skipped = sum(1 for r in results if r["status"] == "SKIP")

    print(f"\n📊 Results: {passed} PASS, {warned} WARN, {failed} FAIL, {skipped} SKIP")
    print()

    for result in results:
        status_icon = {
            "PASS": "✅",
            "WARN": "⚠️ ",
            "ERROR": "❌",
            "SKIP": "⏭️ ",
        }[result["status"]]

        print(f"{status_icon} {result['language']:12} ({result['repo']:15}): {result['reason']}")

        if result["status"] == "PASS":
            print(
                f"   External: {result['external_modules']:3} nodes, "
                f"{result['local_to_external']:3} imports, "
                f"{result['unique_external_packages']:2} unique packages"
            )

    print("\n" + "=" * 80)

    languages_tested = set(r["language"] for r in results if r["status"] in ("PASS", "WARN"))
    languages_passed = set(r["language"] for r in results if r["status"] == "PASS")

    print(f"\n✅ Languages Verified: {', '.join(sorted(languages_passed))}")

    all_supported = {
        "Python",
        "JavaScript",
        "TypeScript",
        "Rust",
        "Go",
        "Scala",
        "Java",
        "C++",
        "C#",
        "PHP",
        "Lua",
    }
    not_tested = all_supported - languages_tested

    if not_tested:
        print(f"⚠️  Languages Not Tested: {', '.join(sorted(not_tested))}")
        print("   (No test repositories available)")

    if failed > 0:
        print(f"\n❌ {failed} tests failed - see errors above")
        sys.exit(1)
    elif warned > 0:
        print(f"\n⚠️  {warned} tests had warnings - verify expected")
        sys.exit(0)
    else:
        print(f"\n✅ All {passed} tests passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
