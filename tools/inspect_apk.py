#!/usr/bin/env python3
"""Static APK inventory: ZIP integrity, ABI folders, ELF architecture and SHA-256.

This script never installs or executes the APK. It uses Python's standard library.
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

ABI_NAMES = {
    "armeabi-v7a": "ARM 32-bit",
    "arm64-v8a": "ARM 64-bit",
    "armeabi": "ARM 32-bit (legacy)",
    "x86": "x86 32-bit",
    "x86_64": "x86 64-bit",
    "mips": "MIPS 32-bit (legacy)",
    "mips64": "MIPS 64-bit (legacy)",
}
MACHINE_NAMES = {
    3: "x86",
    40: "ARM",
    62: "x86-64",
    183: "AArch64",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def elf_info(data: bytes) -> str:
    if len(data) < 20 or data[:4] != b"\x7fELF":
        return "No parece ELF"
    elf_class = {1: "ELF32", 2: "ELF64"}.get(data[4], f"clase={data[4]}")
    endian_flag = data[5]
    if endian_flag not in (1, 2):
        return f"{elf_class}, endian desconocido"
    byte_order = "little" if endian_flag == 1 else "big"
    machine = int.from_bytes(data[18:20], byte_order)
    machine_name = MACHINE_NAMES.get(machine, f"e_machine={machine}")
    return f"{elf_class}, {machine_name}"


def inspect(path: Path) -> int:
    if not path.is_file():
        print(f"ERROR: no existe el archivo: {path}", file=sys.stderr)
        return 2
    print(f"Archivo: {path}")
    print(f"Tamaño: {path.stat().st_size:,} bytes")
    print(f"SHA-256: {sha256_file(path)}")

    try:
        with zipfile.ZipFile(path) as apk:
            bad_member = apk.testzip()
            if bad_member:
                print(f"ZIP: ERROR en la entrada {bad_member}")
                return 3
            print("ZIP: correcto")

            infos = apk.infolist()
            native = [item for item in infos if item.filename.startswith("lib/") and item.filename.endswith(".so")]
            abis: dict[str, list[str]] = defaultdict(list)

            for item in native:
                parts = item.filename.split("/")
                if len(parts) >= 3:
                    abis[parts[1]].append(item.filename)

            print(f"Entradas totales: {len(infos)}")
            print(f"Bibliotecas .so: {len(native)}")
            if not native:
                print("No se encontraron .so en este archivo.")
                print("NOTA: puede ser un APK sin código nativo o un APK base al que le falten splits.")

            for abi in sorted(abis):
                print(f"\nABI de carpeta: {abi} ({ABI_NAMES.get(abi, 'ABI no catalogada')})")
                for filename in sorted(abis[abi]):
                    info = apk.getinfo(filename)
                    with apk.open(info) as stream:
                        header = stream.read(64)
                    print(f"  {filename} — {elf_info(header)}")

            print("\nResumen:")
            if "armeabi-v7a" in abis:
                print("- Hay bibliotecas en lib/armeabi-v7a; verificar que todas las dependencias necesarias estén presentes.")
            else:
                print("- No hay bibliotecas en lib/armeabi-v7a dentro de este archivo.")
            if "arm64-v8a" in abis:
                print("- Hay bibliotecas en lib/arm64-v8a.")
            else:
                print("- No hay bibliotecas en lib/arm64-v8a dentro de este archivo.")
            print("- Este resultado solo describe este APK. Inspecciona también todos los splits del mismo paquete.")
            return 0
    except zipfile.BadZipFile:
        print("ERROR: el archivo no parece ser un APK/ZIP válido.", file=sys.stderr)
        return 4
    except (OSError, RuntimeError) as exc:
        print(f"ERROR al leer el paquete: {exc}", file=sys.stderr)
        return 5


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspección estática de ABI y bibliotecas ELF de un APK")
    parser.add_argument("apk", type=Path, help="Ruta local al archivo APK")
    args = parser.parse_args()
    return inspect(args.apk)


if __name__ == "__main__":
    raise SystemExit(main())
