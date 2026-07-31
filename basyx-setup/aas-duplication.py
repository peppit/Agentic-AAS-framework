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

    source_station_number = 1
    # Change only this value when creating the next station.
    target_station_number = 8

    source_number = f"{source_station_number:02d}"
    target_number = f"{target_station_number:02d}"
    source_sequence = f"{source_station_number:03d}"
    target_sequence = f"{target_station_number:03d}"

    source_conveyor = aas_dir / f"conveyorbelt{source_number}.aasx"
    source_robot = aas_dir / f"robot{source_number}.aasx"

    target_conveyor = aas_dir / f"conveyorbelt{target_number}.aasx"
    target_robot = aas_dir / f"robot{target_number}.aasx"

    common_station_replacements = {
        f"Station_{source_number}": f"Station_{target_number}",
        f"station_{source_number}": f"station_{target_number}",
    }

    conveyor_replacements = {
        **common_station_replacements,
        "https://admin-shell.io/idta/SubmodelTemplate/DigitalNameplate/3/0": f"https://admin-shell.io/idta/SubmodelTemplate/DigitalNameplate/3/0/station{target_number}",
        "https://example.com/ids/sm/3121_1142_6062_3675": f"https://example.com/ids/sm/3121_1142_6062_3675/station{target_number}",
        "https://example.com/ids/sm/5293_2142_6062_9148": f"https://example.com/ids/sm/5293_2142_6062_9148/station{target_number}",
        f"CoveyorBelt{source_number}": f"CoveyorBelt{target_number}",
        f"ConveyorBelt{source_number}": f"ConveyorBelt{target_number}",
        f"conveyorbelt{source_number}": f"conveyorbelt{target_number}",
        f"conveyorbelt-{source_number}": f"conveyorbelt-{target_number}",
        f"SIM-CONV-{source_sequence}": f"SIM-CONV-{target_sequence}",
        f"Station_{source_number}": f"Station_{target_number}",
    }

    robot_replacements = {
        **common_station_replacements,
        f"Robot{source_number}": f"Robot{target_number}",
        f"robot{source_number}": f"robot{target_number}",
        f"SixAxisRobot{source_number}": f"SixAxisRobot{target_number}",
        f"robot-{source_number}": f"robot-{target_number}",
        f"Station_{source_number}": f"Station_{target_number}",
    }

    clone_aasx_with_replacements(source_conveyor, target_conveyor, conveyor_replacements)
    clone_aasx_with_replacements(source_robot, target_robot, robot_replacements)
