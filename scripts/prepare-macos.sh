#!/usr/bin/env bash
# Prepare an OSX-KVM macOS installer disk and a tmac VM record.
# Tested shape: Linux x86_64, QEMU/KVM, AMD Ryzen (requires the topology fix).
#
# Usage:
#   scripts/prepare-macos.sh --osx-kvm DIR --version monterey --cores 2 [--start]
#
# This script does not download macOS unless the recovery image is absent; it
# then uses OSX-KVM's fetch-macOS-v2.py.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD

OSX_KVM=""
VERSION="monterey"
CORES=2
MEMORY=4096
NAME=""
PATCHES=""
START=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --osx-kvm) OSX_KVM=$2; shift 2 ;;
    --version) VERSION=$2; shift 2 ;;
    --cores) CORES=$2; shift 2 ;;
    --memory) MEMORY=$2; shift 2 ;;
    --name) NAME=$2; shift 2 ;;
    --patches) PATCHES=$2; shift 2 ;;
    --start) START=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$OSX_KVM" ]] || { echo "--osx-kvm DIR is required" >&2; exit 2; }
OSX_KVM=$(cd "$OSX_KVM" && pwd)
NAME=${NAME:-mac-$VERSION}
WORK="$ROOT/work/$NAME"
mkdir -p "$WORK"
if [[ -z "$PATCHES" ]]; then
  PATCHES="$ROOT/work/AMD_Vanilla/patches.plist"
  if [[ ! -f "$PATCHES" ]]; then
    git clone --depth 1 https://github.com/AMD-OSX/AMD_Vanilla.git "$ROOT/work/AMD_Vanilla"
  fi
fi
[[ -f "$PATCHES" ]] || { echo "AMD_Vanilla patches.plist not found: $PATCHES" >&2; exit 1; }

if [[ ! -f "$OSX_KVM/$VERSION/BaseSystem.dmg" ]]; then
  mkdir -p "$OSX_KVM/$VERSION"
  ( cd "$OSX_KVM/$VERSION" && python3 "$OSX_KVM/fetch-macOS-v2.py" -s "$VERSION" )
fi

if [[ ! -f "$OSX_KVM/$VERSION/BaseSystem.img" ]]; then
  echo "converting $VERSION DMG to raw (qemu-img supports Apple UDIF)"
  qemu-img convert -f dmg -O raw "$OSX_KVM/$VERSION/BaseSystem.dmg" "$OSX_KVM/$VERSION/BaseSystem.img"
fi
cp --reflink=auto "$OSX_KVM/$VERSION/BaseSystem.img" "$OSX_KVM/BaseSystem.img"

# Keep one pristine OpenCore image, then patch in place.
if [[ ! -f "$OSX_KVM/OpenCore/OpenCore.orig.qcow2" ]]; then
  cp "$OSX_KVM/OpenCore/OpenCore.qcow2" "$OSX_KVM/OpenCore/OpenCore.orig.qcow2"
fi
cp "$OSX_KVM/OpenCore/OpenCore.orig.qcow2" "$OSX_KVM/OpenCore/OpenCore.qcow2"
python3 scripts/opencore_amd.py \
  --image "$OSX_KVM/OpenCore/OpenCore.qcow2" \
  --patches "$PATCHES" \
  --cores "$CORES"

DISK="$WORK/$NAME.qcow2"
if [[ ! -f "$DISK" ]]; then
  qemu-img create -f qcow2 "$DISK" 40G
fi

VCPUS=$((CORES * 2))
export TMAC_HOME="$WORK/tmac-home"
python3 host/tmac.py create "$NAME" --osx-kvm "$OSX_KVM" --disk "$DISK" \
  --memory "$MEMORY" --cpus "$VCPUS" --smp "${VCPUS},cores=${CORES},threads=2,sockets=1" \
  --mode macos --vnc 127.0.0.1:11

echo
echo "prepared $NAME"
echo "  disk:   $DISK"
echo "  opencore: $OSX_KVM/OpenCore/OpenCore.qcow2"
echo "  start:  TMAC_HOME=$TMAC_HOME python3 host/tmac.py start $NAME"
echo "  picker: select 'macOS Base System' with right arrow then return"
[[ "$START" == 1 ]] && python3 host/tmac.py start "$NAME"
