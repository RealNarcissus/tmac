#!/usr/bin/env python3
"""Enable a kext already present in an OpenCore EFI image (in place).

Usage: opencore_add_kext.py --image OpenCore.qcow2 --kext AppleALC.kext
The kext bundle must already exist under EFI/OC/Kexts. Idempotent.
"""
from __future__ import annotations
import argparse, plistlib, shutil, subprocess, tempfile
from pathlib import Path

def run(cmd, **kw): return subprocess.run(cmd, check=True, **kw)
def gpt_partition_offset(raw):
    import struct
    with open(raw, "rb") as f:
        f.seek(512); header=f.read(92)
        if header[:8] != b"EFI PART": raise SystemExit("not a GPT image")
        entry_lba=struct.unpack_from("<Q", header, 72)[0]
        f.seek(entry_lba*512); entry=f.read(128)
        return struct.unpack_from("<Q", entry, 32)[0]*512

ap=argparse.ArgumentParser()
ap.add_argument("--image", required=True)
ap.add_argument("--kext", required=True)
a=ap.parse_args(); src=Path(a.image)
with tempfile.TemporaryDirectory() as td:
    td=Path(td); raw=td/"oc.raw"; cfg=td/"config.plist"
    run(["qemu-img","convert","-O","raw",str(src),str(raw)])
    fat=f"{raw}@@{gpt_partition_offset(raw)}"
    run(["mcopy","-o","-i",fat,"::/EFI/OC/config.plist",str(cfg)])
    p=plistlib.loads(cfg.read_bytes())
    adds=p.setdefault("Kernel",{}).setdefault("Add",[])
    exe="Contents/MacOS/"+Path(a.kext).stem
    pl="Contents/Info.plist"
    if not any(x.get("BundlePath")==a.kext for x in adds):
        adds.append({"Arch":"x86_64","BundlePath":a.kext,"Comment":"","Enabled":True,
                     "ExecutablePath":exe,"MaxKernel":"","MinKernel":"","PlistPath":pl})
        print("added",a.kext)
    else:
        for x in adds:
            if x.get("BundlePath")==a.kext: x["Enabled"]=True
        print("already present",a.kext)
    cfg.write_bytes(plistlib.dumps(p))
    run(["mcopy","-o","-i",fat,str(cfg),"::/EFI/OC/config.plist"])
    run(["qemu-img","convert","-O","qcow2",str(raw),str(src)])
