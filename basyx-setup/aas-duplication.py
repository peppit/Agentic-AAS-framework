"""Create a station's conveyor, robot and factory model from an existing station.

The instance packages contain shared IDTA/eCl@ss identifiers as well as
station-specific identifiers. Only the latter are replaced here so that the
clones continue to refer to the same type AASs and semantic definitions.

Example:
    python aas-duplication.py 2
    python aas-duplication.py 16 --source-station 1 --force
"""

from __future__ import annotations

import argparse
import codecs
import copy
import os
import tempfile
import zipfile
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from xml.etree import ElementTree


TEXT_PART_SUFFIXES = {".xml", ".rels"}
AAS_NAMESPACE = "https://admin-shell.io/aas/3/1"
ElementTree.register_namespace("", AAS_NAMESPACE)


def station_number(value: str) -> int:
    """Parse a positive station number for argparse."""
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("station number must be an integer") from exc

    if number < 1:
        raise argparse.ArgumentTypeError("station number must be greater than zero")
    return number


def build_replacement_plans(
    source_station: int, target_station: int
) -> dict[str, dict[str, str]]:
    """Return replacements for the current conveyor and robot instance models."""
    if source_station == target_station:
        raise ValueError("source and target stations must be different")

    source = f"{source_station:02d}"
    target = f"{target_station:02d}"
    source_serial = f"{source_station:03d}"
    target_serial = f"{target_station:03d}"

    conveyor = {
        f"urn:agent-aas:aas:conveyor{source}": f"urn:agent-aas:aas:conveyor{target}",
        f"urn:agent-aas:asset-instance:conveyor{source}": (
            f"urn:agent-aas:asset-instance:conveyor{target}"
        ),
        f"urn:agent-aas:conveyor{source}": f"urn:agent-aas:conveyor{target}",
        f"ConveyorBelt{source}": f"ConveyorBelt{target}",
        f"conveyorbelt-{source}": f"conveyorbelt-{target}",
        f"SIM-CONV-{source_serial}": f"SIM-CONV-{target_serial}",
        f"Conveyor_{source}": f"Conveyor_{target}",
    }

    robot = {
        f"urn:agent-aas:aas:robot{source}": f"urn:agent-aas:aas:robot{target}",
        f"urn:agent-aas:asset-instance:robot{source}": (
            f"urn:agent-aas:asset-instance:robot{target}"
        ),
        f"urn:agent-aas:robot{source}": f"urn:agent-aas:robot{target}",
        f"Robot{source}": f"Robot{target}",
        f"robot-{source}": f"robot-{target}",
        f"Robot_{source}": f"Robot_{target}",
        # A robot clone belongs to the corresponding conveyor station. The
        # factory pallet remains shared, so its pallet01 reference is retained.
        f"urn:agent-aas:asset-instance:conveyor{source}": (
            f"urn:agent-aas:asset-instance:conveyor{target}"
        ),
        f"MoveBoxConveyor{source}": f"MoveBoxConveyor{target}",
    }

    return {"conveyor": conveyor, "robot": robot}


def _is_text_part(name: str) -> bool:
    return Path(name).suffix.lower() in TEXT_PART_SUFFIXES


def _aas_tag(local_name: str) -> str:
    return f"{{{AAS_NAMESPACE}}}{local_name}"


def _id_short(element: ElementTree.Element) -> str | None:
    child = element.find(_aas_tag("idShort"))
    return child.text if child is not None else None


def _find_named_child(
    parent: ElementTree.Element, local_name: str, id_short: str
) -> ElementTree.Element:
    for child in parent.findall(_aas_tag(local_name)):
        if _id_short(child) == id_short:
            return child
    raise ValueError(f"factory model element {local_name}/{id_short} was not found")


def _find_submodel(
    root: ElementTree.Element, id_short: str
) -> ElementTree.Element | None:
    submodels = root.find(_aas_tag("submodels"))
    if submodels is None:
        return None
    for submodel in submodels.findall(_aas_tag("submodel")):
        if _id_short(submodel) == id_short:
            return submodel
    return None


def _clone_element(
    source: ElementTree.Element, replacements: Mapping[str, str]
) -> ElementTree.Element:
    cloned = copy.deepcopy(source)
    ordered_replacements = sorted(
        replacements.items(), key=lambda item: len(item[0]), reverse=True
    )
    for element in cloned.iter():
        if element.text is None:
            continue
        for old, new in ordered_replacements:
            element.text = element.text.replace(old, new)
    return cloned


def _upsert_factory_element(
    parent: ElementTree.Element,
    local_name: str,
    source_id_short: str,
    target_id_short: str,
    replacements: Mapping[str, str],
    *,
    overwrite: bool,
) -> None:
    source = _find_named_child(parent, local_name, source_id_short)
    target = next(
        (
            child
            for child in parent.findall(_aas_tag(local_name))
            if _id_short(child) == target_id_short
        ),
        None,
    )
    cloned = _clone_element(source, replacements)

    if target is not None:
        if not overwrite:
            raise FileExistsError(
                f"factory model element already exists: {target_id_short} "
                "(use --force to replace it)"
            )
        index = list(parent).index(target)
        parent.remove(target)
        parent.insert(index, cloned)
        return

    same_type_indices = [
        index
        for index, child in enumerate(parent)
        if child.tag == _aas_tag(local_name)
    ]
    insertion_index = max(same_type_indices) + 1 if same_type_indices else len(parent)
    parent.insert(insertion_index, cloned)


def _replace_text(
    data: bytes, replacements: Mapping[str, str]
) -> tuple[bytes, Counter[str]]:
    """Replace identifiers in a UTF-8 package part while preserving its BOM."""
    has_bom = data.startswith(codecs.BOM_UTF8)
    text = data.decode("utf-8-sig")
    counts: Counter[str] = Counter()

    # Longest first makes behavior deterministic if a future replacement plan
    # contains one identifier that is a prefix of another.
    ordered_replacements = sorted(
        replacements.items(), key=lambda item: len(item[0]), reverse=True
    )
    for old, new in ordered_replacements:
        count = text.count(old)
        if count:
            text = text.replace(old, new)
            counts[old] += count

    encoding = "utf-8-sig" if has_bom else "utf-8"
    return text.encode(encoding), counts


def validate_source_package(
    source_file: Path, replacements: Mapping[str, str]
) -> None:
    """Ensure a source package contains every expected current-model token."""
    if not source_file.is_file():
        raise FileNotFoundError(f"source AASX package does not exist: {source_file}")

    counts: Counter[str] = Counter()
    with zipfile.ZipFile(source_file, "r") as source_zip:
        for member in source_zip.infolist():
            if not _is_text_part(member.filename):
                continue
            text = source_zip.read(member).decode("utf-8-sig")
            counts.update(
                {old: text.count(old) for old in replacements if old in text}
            )

    missing = [old for old in replacements if counts[old] == 0]
    if missing:
        missing_list = "\n  - ".join(missing)
        raise ValueError(
            f"{source_file} does not match the expected current AAS structure; "
            f"these identifiers were not found:\n  - {missing_list}"
        )


def clone_aasx_with_replacements(
    source_file: Path,
    target_file: Path,
    replacements: Mapping[str, str],
    *,
    overwrite: bool = False,
) -> Counter[str]:
    """Copy an AASX package and replace identifiers in its XML parts.

    Package paths, relationships, binary supplementary files, ZIP metadata and
    the source package itself are preserved. The destination is written via a
    temporary file so a failed operation cannot leave a partial AASX package.
    """
    source_file = source_file.resolve()
    target_file = target_file.resolve()

    if not source_file.is_file():
        raise FileNotFoundError(f"source AASX package does not exist: {source_file}")
    if source_file == target_file:
        raise ValueError("source and target AASX paths must be different")
    if target_file.exists() and not overwrite:
        raise FileExistsError(
            f"target already exists: {target_file} (use --force to replace it)"
        )

    target_file.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    counts: Counter[str] = Counter()

    try:
        with tempfile.NamedTemporaryFile(
            dir=target_file.parent,
            prefix=f".{target_file.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)

        with zipfile.ZipFile(source_file, "r") as source_zip, zipfile.ZipFile(
            temp_path, "w"
        ) as target_zip:
            target_zip.comment = source_zip.comment
            for member in source_zip.infolist():
                data = source_zip.read(member)
                if _is_text_part(member.filename):
                    data, part_counts = _replace_text(data, replacements)
                    counts.update(part_counts)
                    ElementTree.fromstring(data)
                target_zip.writestr(member, data)

        missing = [old for old in replacements if counts[old] == 0]
        if missing:
            missing_list = "\n  - ".join(missing)
            raise ValueError(
                "source package does not match the expected current AAS structure; "
                f"these identifiers were not found:\n  - {missing_list}"
            )

        with zipfile.ZipFile(temp_path, "r") as completed_zip:
            corrupt_member = completed_zip.testzip()
            if corrupt_member is not None:
                raise zipfile.BadZipFile(
                    f"verification failed for package member {corrupt_member}"
                )

        os.replace(temp_path, target_file)
        temp_path = None
        return counts
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _update_factory_xml(
    data: bytes,
    source_station: int,
    target_station: int,
    *,
    overwrite: bool,
) -> bytes | None:
    """Add one station to an AAS environment XML part, if it is the factory."""
    root = ElementTree.fromstring(data)
    hierarchy = _find_submodel(root, "HierarchicalStructures")
    requirements = _find_submodel(root, "ProcessRequirements")
    if hierarchy is None and requirements is None:
        return None
    if hierarchy is None or requirements is None:
        raise ValueError(
            "factory package must contain HierarchicalStructures and "
            "ProcessRequirements submodels"
        )

    source = f"{source_station:02d}"
    target = f"{target_station:02d}"
    robot_replacements = {
        f"Robot{source}": f"Robot{target}",
        f"urn:agent-aas:asset-instance:robot{source}": (
            f"urn:agent-aas:asset-instance:robot{target}"
        ),
    }
    conveyor_replacements = {
        f"Conveyor{source}": f"Conveyor{target}",
        f"urn:agent-aas:asset-instance:conveyor{source}": (
            f"urn:agent-aas:asset-instance:conveyor{target}"
        ),
    }
    requirement_replacements = {
        f"Transfer{source}": f"Transfer{target}",
        f"urn:agent-aas:asset-instance:conveyor{source}": (
            f"urn:agent-aas:asset-instance:conveyor{target}"
        ),
    }

    hierarchy_elements = hierarchy.find(_aas_tag("submodelElements"))
    requirements_elements = requirements.find(_aas_tag("submodelElements"))
    if hierarchy_elements is None or requirements_elements is None:
        raise ValueError("factory submodelElements container was not found")

    factory_entity = _find_named_child(
        hierarchy_elements, "entity", "OIPFactory"
    )
    statements = factory_entity.find(_aas_tag("statements"))
    if statements is None:
        raise ValueError("OIPFactory statements container was not found")

    _upsert_factory_element(
        statements,
        "entity",
        f"Robot{source}",
        f"Robot{target}",
        robot_replacements,
        overwrite=overwrite,
    )
    _upsert_factory_element(
        statements,
        "entity",
        f"Conveyor{source}",
        f"Conveyor{target}",
        conveyor_replacements,
        overwrite=overwrite,
    )
    _upsert_factory_element(
        statements,
        "relationshipElement",
        f"HasPartRobot{source}",
        f"HasPartRobot{target}",
        robot_replacements,
        overwrite=overwrite,
    )
    _upsert_factory_element(
        statements,
        "relationshipElement",
        f"HasPartConveyor{source}",
        f"HasPartConveyor{target}",
        conveyor_replacements,
        overwrite=overwrite,
    )
    _upsert_factory_element(
        requirements_elements,
        "submodelElementCollection",
        f"Transfer{source}",
        f"Transfer{target}",
        requirement_replacements,
        overwrite=overwrite,
    )

    original_without_bom = data.removeprefix(codecs.BOM_UTF8).lstrip()
    had_xml_declaration = original_without_bom.startswith(b"<?xml")
    updated = ElementTree.tostring(
        root, encoding="utf-8", xml_declaration=had_xml_declaration
    )
    if data.startswith(codecs.BOM_UTF8):
        updated = codecs.BOM_UTF8 + updated
    return updated


def prepare_factory_update(
    factory_file: Path,
    source_station: int,
    target_station: int,
    *,
    overwrite: bool,
) -> dict[str, bytes]:
    """Validate the factory package and prepare its modified XML part."""
    if not factory_file.is_file():
        raise FileNotFoundError(f"factory AASX package does not exist: {factory_file}")

    updated_parts: dict[str, bytes] = {}
    with zipfile.ZipFile(factory_file, "r") as factory_zip:
        for member in factory_zip.infolist():
            if Path(member.filename).suffix.lower() != ".xml":
                continue
            updated = _update_factory_xml(
                factory_zip.read(member),
                source_station,
                target_station,
                overwrite=overwrite,
            )
            if updated is not None:
                updated_parts[member.filename] = updated

    if len(updated_parts) != 1:
        raise ValueError(
            "expected exactly one factory AAS environment XML part, found "
            f"{len(updated_parts)}"
        )
    return updated_parts


def update_factory_package(
    factory_file: Path, updated_parts: Mapping[str, bytes]
) -> None:
    """Atomically rewrite an AASX package with prepared factory XML."""
    factory_file = factory_file.resolve()
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=factory_file.parent,
            prefix=f".{factory_file.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)

        with zipfile.ZipFile(factory_file, "r") as source_zip, zipfile.ZipFile(
            temp_path, "w"
        ) as target_zip:
            target_zip.comment = source_zip.comment
            for member in source_zip.infolist():
                data = updated_parts.get(member.filename)
                if data is None:
                    data = source_zip.read(member)
                target_zip.writestr(member, data)

        with zipfile.ZipFile(temp_path, "r") as completed_zip:
            corrupt_member = completed_zip.testzip()
            if corrupt_member is not None:
                raise zipfile.BadZipFile(
                    f"verification failed for package member {corrupt_member}"
                )

        os.replace(temp_path, factory_file)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def duplicate_station(
    aas_dir: Path,
    source_station: int,
    target_station: int,
    *,
    overwrite: bool = False,
) -> list[Path]:
    """Duplicate station instances and add them to the factory model."""
    source = f"{source_station:02d}"
    target = f"{target_station:02d}"
    plans = build_replacement_plans(source_station, target_station)
    package_names = {
        "conveyor": f"conveyorbelt{source}.aasx",
        "robot": f"robot{source}.aasx",
    }
    target_names = {
        "conveyor": f"conveyorbelt{target}.aasx",
        "robot": f"robot{target}.aasx",
    }
    factory_path = aas_dir / "OIPFactory.aasx"

    targets = [aas_dir / target_names[kind] for kind in package_names]
    existing = [path for path in targets if path.exists()]
    if existing and not overwrite:
        existing_list = "\n  - ".join(str(path.resolve()) for path in existing)
        raise FileExistsError(
            f"target package(s) already exist (use --force to replace them):\n"
            f"  - {existing_list}"
        )

    # Check every input before writing output so a stale source or factory model
    # cannot leave a half-created station behind.
    for kind, source_name in package_names.items():
        validate_source_package(aas_dir / source_name, plans[kind])
    factory_update = prepare_factory_update(
        factory_path,
        source_station,
        target_station,
        overwrite=overwrite,
    )

    created: list[Path] = []
    for kind, source_name in package_names.items():
        source_path = aas_dir / source_name
        target_path = aas_dir / target_names[kind]
        print(f"[PROCESS] {source_path.name} -> {target_path.name}")
        counts = clone_aasx_with_replacements(
            source_path,
            target_path,
            plans[kind],
            overwrite=overwrite,
        )
        print(
            f"[SUCCESS] Updated {sum(counts.values())} identifier occurrence(s) "
            f"in {target_path}"
        )
        created.append(target_path)

    print(f"[PROCESS] Adding station {target} to {factory_path.name}")
    update_factory_package(factory_path, factory_update)
    print(
        f"[SUCCESS] Added Robot{target}, Conveyor{target}, their HasPart "
        f"relationships and Transfer{target} to {factory_path}"
    )
    created.append(factory_path)

    return created


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Duplicate the current conveyor and robot AASX instances and add "
            "the station hierarchy and transfer requirement to OIPFactory.aasx."
        )
    )
    parser.add_argument("target_station", type=station_number)
    parser.add_argument(
        "--source-station",
        type=station_number,
        default=1,
        help="station to use as the source (default: 1)",
    )
    parser.add_argument(
        "--aas-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "aas",
        help="directory containing the AASX packages",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace target packages if they already exist",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        duplicate_station(
            args.aas_dir,
            args.source_station,
            args.target_station,
            overwrite=args.force,
        )
    except (
        ElementTree.ParseError,
        OSError,
        UnicodeError,
        ValueError,
        zipfile.BadZipFile,
    ) as exc:
        print(f"[ERROR] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
