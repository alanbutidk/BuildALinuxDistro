import json, os, re, shutil, subprocess, sys, tarfile, urllib.request
import yaml

PRESETS = {
    "stock": {},
    "desktop": {"PREEMPT": "y", "HZ_250": "y"},
    "server": {"PREEMPT_NONE": "y", "HZ_100": "y"},
    "low-latency": {"PREEMPT": "y", "HZ_1000": "y", "NO_HZ_IDLE": "y"},
    "minimal": {"CC_OPTIMIZE_FOR_SIZE": "y", "DEBUG_KERNEL": "n", "MODULES": "n"},
}

FEATURES = {
    "modules": {"MODULES": "y"},
    "kvm": {"VIRTUALIZATION": "y", "KVM": "y", "KVM_INTEL": "m", "KVM_AMD": "m"},
    "apparmor": {"SECURITY": "y", "SECURITY_APPARMOR": "y", "DEFAULT_SECURITY_APPARMOR": "y"},
    "selinux": {"SECURITY": "y", "SECURITY_SELINUX": "y", "DEFAULT_SECURITY_SELINUX": "y"},
    "btrfs": {"BTRFS_FS": "y"},
    "wireguard": {"WIREGUARD": "m"},
    "bpf": {"BPF_SYSCALL": "y", "BPF_JIT": "y", "CGROUP_BPF": "y"},
    "debug-info": {"DEBUG_KERNEL": "y", "DEBUG_INFO": "y", "DEBUG_INFO_DWARF5": "y"},
}

ARCHES = {
    "x86_64": ("x86_64", "", "bzImage", "arch/x86/boot/bzImage", None),
    "aarch64": ("arm64", "aarch64-linux-gnu-", "Image", "arch/arm64/boot/Image", "gcc-aarch64-linux-gnu"),
    "riscv64": ("riscv", "riscv64-linux-gnu-", "Image", "arch/riscv/boot/Image", "gcc-riscv64-linux-gnu"),
}


def run(cmd, **kw):
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def fragment(cfg):
    kernel = cfg["kernel"]
    feats = kernel.get("features") or []
    if "apparmor" in feats and "selinux" in feats:
        sys.exit("apparmor and selinux cannot both be the default security module")
    syms = dict(PRESETS[kernel["preset"]])
    for f in feats:
        syms.update(FEATURES[f])
    if "modules" not in feats:
        syms["MODULES"] = "n"
    syms["LOCALVERSION"] = "-" + str(cfg["base"]["name"])
    syms["DEFAULT_HOSTNAME"] = str(cfg["base"]["hostname"])
    syms.update({str(a): b for a, b in (kernel.get("config") or {}).items()})
    lines = []
    for name, val in syms.items():
        val = str(val)
        if val == "n":
            lines.append(f"# CONFIG_{name} is not set")
        elif re.fullmatch(r"y|m|\d+|0x[0-9a-fA-F]+", val):
            lines.append(f"CONFIG_{name}={val}")
        else:
            lines.append(f'CONFIG_{name}="{val}"')
    return "\n".join(lines) + "\n"


def resolve(want):
    with urllib.request.urlopen("https://www.kernel.org/releases.json") as r:
        rel = json.load(r)
    if want == "latest":
        return rel["latest_stable"]["version"]
    found = [x["version"] for x in rel["releases"]
             if x["moniker"] in ("stable", "longterm") and x["version"].startswith(want + ".")]
    if found:
        return max(found, key=lambda v: [int(p) for p in v.split(".")])
    return want


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "config.yml"
    with open(path) as fh:
        cfg = yaml.safe_load(fh)
    arch, cross, target, image, pkg = ARCHES[cfg["base"]["arch"]]
    feats = cfg["kernel"].get("features") or []
    ver = resolve(str(cfg["kernel"]["version"]))
    print("kernel", ver, flush=True)

    out = os.path.abspath("out")
    os.makedirs(out, exist_ok=True)
    frag = os.path.join(out, "fragment.config")
    with open(frag, "w") as fh:
        fh.write(fragment(cfg))

    if pkg:
        run(["sudo", "apt-get", "install", "-y", pkg])

    tgz = f"linux-{ver}.tar.xz"
    major = ver.split(".")[0]
    urllib.request.urlretrieve(f"https://cdn.kernel.org/pub/linux/kernel/v{major}.x/{tgz}", tgz)
    with tarfile.open(tgz) as t:
        t.extractall("src")
    src = os.path.abspath(f"src/linux-{ver}")

    env = dict(
        os.environ,
        ARCH=arch,
        CROSS_COMPILE=cross,
        KBUILD_BUILD_TIMESTAMP="Thu Jan  1 00:00:00 UTC 1970",
        KBUILD_BUILD_USER="distroyml",
        KBUILD_BUILD_HOST="distroyml",
    )
    jobs = f"-j{os.cpu_count()}"
    run(["make", "defconfig"], cwd=src, env=env)
    run(["scripts/kconfig/merge_config.sh", "-m", ".config", frag], cwd=src, env=env)
    run(["make", "olddefconfig"], cwd=src, env=env)
    run(["make", jobs, target], cwd=src, env=env)
    if "modules" in feats:
        run(["make", jobs, "modules"], cwd=src, env=env)
        run(["make", "modules_install", f"INSTALL_MOD_PATH={out}/rootfs"], cwd=src, env=env)

    shutil.copy(os.path.join(src, image), os.path.join(out, os.path.basename(image)))
    shutil.copy(os.path.join(src, ".config"), os.path.join(out, "kernel.config"))
    shutil.copy(path, os.path.join(out, "config.yml"))


if __name__ == "__main__":
    main()
