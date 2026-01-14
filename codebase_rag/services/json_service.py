from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from .. import constants as cs
from .. import logs as ls
from ..types_defs import GraphData, GraphMetadata, PropertyDict, PropertyValue

PATH_BASED_LABELS = frozenset({cs.NodeLabel.FOLDER, cs.NodeLabel.FILE})
NAME_BASED_LABELS = frozenset({cs.NodeLabel.EXTERNAL_PACKAGE, cs.NodeLabel.PROJECT})


class JsonFileIngestor:
    """
    Thread-safe JSON file ingestor for graph data.

    Uses a lock to protect concurrent access to internal data structures,
    enabling parallel file processing and call resolution.
    """

    def __init__(self, output_path: str):
        self.output_path = Path(output_path)
        self._nodes: dict[str, dict] = {}
        self._relationships: list[dict] = []
        self._node_counter = 0
        self._node_id_lookup: dict[str, int] = {}
        self._lock = threading.Lock()
        logger.info(ls.JSON_INIT.format(path=self.output_path))

    def _get_node_id_key(self, label: str, properties: PropertyDict) -> str:
        try:
            node_label = cs.NodeLabel(label)
        except ValueError:
            return f"{label}:{properties.get(cs.KEY_QUALIFIED_NAME, properties.get(cs.KEY_NAME, ''))}"

        if node_label in PATH_BASED_LABELS:
            return f"{label}:{properties.get(cs.KEY_PATH, '')}"
        if node_label in NAME_BASED_LABELS:
            return f"{label}:{properties.get(cs.KEY_NAME, '')}"
        return f"{label}:{properties.get(cs.KEY_QUALIFIED_NAME, '')}"

    def ensure_node_batch(self, label: str, properties: PropertyDict) -> None:
        node_key = self._get_node_id_key(label, properties)
        if not node_key:
            return

        with self._lock:
            if node_key in self._nodes:
                return

            node_id = self._node_counter
            self._node_counter += 1
            self._node_id_lookup[node_key] = node_id

            self._nodes[node_key] = {
                cs.KEY_NODE_ID: node_id,
                cs.KEY_LABELS: [label],
                cs.KEY_PROPERTIES: {k: v for k, v in properties.items() if v is not None},
            }

    def ensure_relationship_batch(
        self,
        from_spec: tuple[str, str, PropertyValue],
        rel_type: str,
        to_spec: tuple[str, str, PropertyValue],
        properties: PropertyDict | None = None,
    ) -> None:
        from_label, _, from_val = from_spec
        to_label, _, to_val = to_spec

        from_node_key = f"{from_label}:{from_val}"
        to_node_key = f"{to_label}:{to_val}"

        with self._lock:
            self._relationships.append({
                "from_key": from_node_key,
                "to_key": to_node_key,
                cs.KEY_TYPE: rel_type,
                cs.KEY_PROPERTIES: dict(properties) if properties else {},
            })

    def flush_all(self) -> None:
        logger.info(ls.JSON_FLUSHING.format(path=self.output_path))

        # Sort nodes by qualified_name for deterministic output
        nodes_list = sorted(
            self._nodes.values(),
            key=lambda n: (
                n.get(cs.KEY_LABELS, [""])[0],
                n.get(cs.KEY_PROPERTIES, {}).get("qualified_name", "")
                or n.get(cs.KEY_PROPERTIES, {}).get("name", "")
                or n.get(cs.KEY_PROPERTIES, {}).get("path", ""),
            ),
        )

        resolved_relationships = []
        for rel in self._relationships:
            from_id = self._node_id_lookup.get(rel["from_key"])
            to_id = self._node_id_lookup.get(rel["to_key"])

            if from_id is not None and to_id is not None:
                resolved_relationships.append({
                    cs.KEY_FROM_ID: from_id,
                    cs.KEY_TO_ID: to_id,
                    cs.KEY_TYPE: rel[cs.KEY_TYPE],
                    cs.KEY_PROPERTIES: rel[cs.KEY_PROPERTIES],
                })
            else:
                logger.debug(
                    ls.JSON_SKIPPING_REL.format(
                        from_key=rel["from_key"], to_key=rel["to_key"]
                    )
                )

        # Sort relationships for deterministic output
        resolved_relationships.sort(
            key=lambda r: (r[cs.KEY_FROM_ID], r[cs.KEY_TYPE], r[cs.KEY_TO_ID])
        )

        metadata: GraphMetadata = {
            cs.KEY_TOTAL_NODES: len(nodes_list),
            cs.KEY_TOTAL_RELATIONSHIPS: len(resolved_relationships),
            cs.KEY_EXPORTED_AT: datetime.now(UTC).isoformat(),
        }

        graph_data: GraphData = {
            cs.KEY_NODES: nodes_list,
            cs.KEY_RELATIONSHIPS: resolved_relationships,
            cs.KEY_METADATA: metadata,
        }

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.output_path, "w", encoding=cs.ENCODING_UTF8) as f:
            json.dump(graph_data, f, indent=cs.JSON_INDENT, ensure_ascii=False, sort_keys=True)

        logger.success(
            ls.JSON_FLUSH_SUCCESS.format(
                nodes=len(nodes_list),
                rels=len(resolved_relationships),
                path=self.output_path,
            )
        )
