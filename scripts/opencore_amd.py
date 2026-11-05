#!/usr/bin/env python3
"""Patch an OSX-KVM OpenCore qcow2 for AMD KVM + matching CPU topology.

What it does, deterministically:
  1. converts the qcow2 to a temporary raw image,
  2. locates the first GPT partition (the EFI FAT volume) and extracts
     EFI/OC/config.plist with mtools,
  3. merges AMD_Vanilla kernel patches (unless already present),
  4. sets the four "Force cpuid_cores_per_package" Replace values to the
     physical core count exposed to macOS,
  5. enables ProvideCurrentCpuInfo, Intel-compatible CPUID emulation,
     SecureBootModel=Disabled, the AMD/KVM Booter quirks, SMBIOS product,
     boot-args and SIP value,
  6. writes the plist back and converts the raw image to the output qcow2.

The topology step matters: with `-smp 4,cores=2,threads=2`, a core-count
constant of 2 prevents XNU's `_cpu_thread_alloc` divide-by-zero trap.
"""

from __future__ import annotations

import argparse
import base64
import copy
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kw)


def gpt_partition_offset(raw: Path) -> int:
    with open(raw, "rb") as f:
        f.seek(512)
        header = f.read(92)
        if header[:8] != b"EFI PART":
            raise SystemExit("not a GPT image; refusing to guess partition offset")
        entry_lba = struct.unpack_from("<Q", header, 72)[0]
        f.seek(entry_lba * 512)
        entry = f.read(128)
        first_lba = struct.unpack_from("<Q", entry, 32)[0]
        return first_lba * 512


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="OpenCore.qcow2 (modified in place unless --out)")
    ap.add_argument("--patches", required=True, help="AMD_Vanilla/patches.plist")
    ap.add_argument("--cores", type=int, required=True, help="physical cores exposed to macOS")
    ap.add_argument("--boot-args", default="keepsyms=1 debug=0x100 -v serial=3")
    ap.add_argument("--product", default="iMacPro1,1")
    ap.add_argument("--sip", default="0f000000")
    ap.add_argument("--out")
    args = ap.parse_args()

    src = Path(args.image).resolve()
    out = Path(args.out).resolve() if args.out else src
    if not src.exists():
        raise SystemExit(f"missing image: {src}")
    if args.cores not in (2, 4, 6, 8, 12, 16, 24, 32):
        raise SystemExit("use a documented core count")

    with tempfile.TemporaryDirectory(prefix="opencore-amd-") as td:
        tmp = Path(td)
        raw = tmp / "oc.raw"
        cfg_path = tmp / "config.plist"
        run(["qemu-img", "convert", "-O", "raw", str(src), str(raw)])
        offset = gpt_partition_offset(raw)
        fat = f"{raw}@@{offset}"
        run(["mcopy", "-o", "-i", fat, "::/EFI/OC/config.plist", str(cfg_path)])
        cfg = plistlib.loads(cfg_path.read_bytes())

        amd = plistlib.loads(Path(args.patches).read_bytes())
        existing = {p.get("Comment") for p in cfg["Kernel"].get("Patch", [])}
        added = 0
        for patch in amd["Kernel"]["Patch"]:
            if patch.get("Comment") not in existing:
                cfg["Kernel"].setdefault("Patch", []).append(copy.deepcopy(patch))
                added += 1

        hex_cores = f"{args.cores:02x}"
        values = {
            "10.13-10.14": bytes.fromhex("b8" + hex_cores + "00000000"),
            "10.15-11.0": bytes.fromhex("ba" + hex_cores + "00000000"),
            "12.0-13.2": bytes.fromhex("ba" + hex_cores + "00000090"),
            "13.3+": bytes.fromhex("ba" + hex_cores + "000000"),
        }
        for patch in cfg["Kernel"].get("Patch", []):
            comment = patch.get("Comment", "")
            if "Force cpuid_cores_per_package" in comment:
                for suffix, value in values.items():
                    if comment.endswith(suffix):
                        patch["Replace"] = value

        cfg["Kernel"].setdefault("Quirks", {})["ProvideCurrentCpuInfo"] = True
        emulate = cfg["Kernel"].setdefault("Emulate", {})
        emulate["Cpuid1Data"] = base64.b64decode("VAYFAAAAAAAAAAAAAAAAAA==")
        emulate["Cpuid1Mask"] = base64.b64decode("////AAAAAAAAAAAAAAAAAA==")
        emulate["DummyPowerManagement"] = True
        quirks = cfg["Booter"]["Quirks"]
        quirks.update({
            "SetupVirtualMap": False,
            "RebuildAppleMemoryMap": False,
            "SyncRuntimePermissions": False,
            "ProvideCustomSlide": True,
            "EnableSafeModeSlide": True,
            "AvoidRuntimeDefrag": True,
            "ProtectMemoryRegions": False,
            "EnableWriteUnprotector": True,
            "DevirtualiseMmio": False,
            "ResizeAppleGpuBars": 0,
        })
        cfg["Misc"]["Security"]["SecureBootModel"] = "Disabled"
        cfg["PlatformInfo"]["Generic"]["SystemProductName"] = args.product
        nvram = cfg["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]
        nvram["boot-args"] = args.boot_args
        nvram["csr-active-config"] = bytes.fromhex(args.sip)

        cfg_path.write_bytes(plistlib.dumps(cfg, fmt=plistlib.FMT_XML, sort_keys=False))
        run(["mcopy", "-o", "-i", fat, str(cfg_path), "::/EFI/OC/config.plist"])
        run(["qemu-img", "convert", "-O", "qcow2", str(raw), str(out)])
        print(f"patched {src} -> {out}")
        print(f"  AMD patches added: {added}, total Kernel.Patch: {len(cfg['Kernel']['Patch'])}")
        print(f"  cores={args.cores}, product={args.product}, boot-args={args.boot_args}")


if __name__ == "__main__":
    main()
