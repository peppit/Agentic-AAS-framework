import os
import shutil
import tempfile
import zipfile
from pathlib import Path


def clone_aasx_with_replacements(source_file: Path, target_file: Path, replacements: dict[str, str]) -> None:
    """
    Unpack an AASX package, apply explicit text replacements, and repack it.
    Using an explicit replacement map avoids accidental changes in unrelated IDs.
    """
    with tempfile.TemporaryDirectory(prefix="aasx_clone_") as tmp_extract_dir:
        print(f"[PROCESS] Unpacking {source_file}...")
        with zipfile.ZipFile(source_file, "r") as zip_ref:
            zip_ref.extractall(tmp_extract_dir)

        print(f"[PROCESS] Applying {len(replacements)} targeted identifier replacements...")

        for root, _, files in os.walk(tmp_extract_dir):
            for file_name in files:
                file_path = Path(root) / file_name

                # Skip likely binary files.
                if file_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".pdf", ".bin"}:
                    continue

                try:
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                except Exception as exc:
                    print(f"  [SKIP] Could not read {file_path.name}: {exc}")
                    continue

                modified_content = content
                for old, new in replacements.items():
                    modified_content = modified_content.replace(old, new)

                if modified_content != content:
                    file_path.write_text(modified_content, encoding="utf-8")
                    print(f"  [UPDATED] {file_path.relative_to(tmp_extract_dir)}")

        print(f"[PROCESS] Creating cloned asset: {target_file}...")
        target_file.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target_file, "w", zipfile.ZIP_DEFLATED) as zip_out:
            for root, _, files in os.walk(tmp_extract_dir):
                for file_name in files:
                    full_path = Path(root) / file_name
                    relative_path = full_path.relative_to(tmp_extract_dir)
                    zip_out.write(full_path, relative_path)

    print("[SUCCESS] Clone completed successfully.\n")

# --- EXECUTION BLOCK ---
if __name__ == "__main__":
    base_dir = Path(__file__).resolve().parent
    aas_dir = base_dir / "aas"

    source_conveyor = aas_dir / "conveyorbelt01.aasx"
    source_robot = aas_dir / "robot01.aasx"

    target_conveyor = aas_dir / "conveyorbelt02.aasx"
    target_robot = aas_dir / "robot02.aasx"

    common_station_replacements = {
        "Station_01": "Station_02",
        "station_01": "station_02",
    }

    conveyor_replacements = {
        **common_station_replacements,
        "CoveyorBelt01": "CoveyorBelt02",
        "ConveyorBelt01": "ConveyorBelt02",
        "conveyorbelt01": "conveyorbelt02",
        "conveyorbelt-01": "conveyorbelt-02",
        "SIM-CONV-001": "SIM-CONV-002",
        "Station_01": "Station_02",


        "https://admin-shell.io/idta/SubmodelTemplate/DigitalNameplate/3/0": "https://admin-shell.io/idta/SubmodelTemplate/DigitalNameplate/3/0/station02",
        "https://admin-shell.io/aas/conveyorbelt01": "https://admin-shell.io/aas/conveyorbelt02",
        "https://example.com/ids/sm/3121_1142_6062_3675": "https://example.com/ids/sm/3121_1142_6062_3675/station02",
        "https://example.com/ids/sm/5293_2142_6062_9148": "https://example.com/ids/sm/5293_2142_6062_9148/station02"
    }

    robot_replacements = {
        **common_station_replacements,
        "Robot01": "Robot02",
        "robot01": "robot02",
        "SixAxisRobot01": "SixAxisRobot02",
        "robot-01": "robot-02",
        "Station_01": "Station_02",

        "https://admin-shell.io/idta/aas/robot01/DigitalNameplate/3/0": "https://admin-shell.io/idta/aas/robot02/DigitalNameplate/3/0/station02",
    }

    clone_aasx_with_replacements(source_conveyor, target_conveyor, conveyor_replacements)
    clone_aasx_with_replacements(source_robot, target_robot, robot_replacements)