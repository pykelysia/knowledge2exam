"""PDF 渲染依赖安装工具。

提供跨平台的 pandoc + xelatex 自动安装辅助函数。
调用方需自行确认是否有权限执行系统包管理器命令。
"""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess

logger = logging.getLogger(__name__)

# 需要安装的包名（按平台）
_PACKAGES: dict[str, dict[str, list[str]]] = {
    "debian": {
        "apt": [
            "pandoc",
            "texlive-xetex",
            "texlive-lang-chinese",
            "fonts-noto-cjk",
            "latex-cjk-chinese",
        ],
    },
    "ubuntu": {
        "apt": [
            "pandoc",
            "texlive-xetex",
            "texlive-lang-chinese",
            "fonts-noto-cjk",
            "latex-cjk-chinese",
        ],
    },
    "darwin": {
        "brew": [
            "pandoc",
            "texlive",
        ],
    },
    "windows": {
        "winget": [
            "JohnMacFarlane.Pandoc",
        ],
        "choco": [
            "pandoc",
        ],
    },
}


_INSTALL_TIMEOUT_SECONDS = 900


def _run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
    """执行命令并返回结果（系统包安装可能耗时较长，设兜底超时）。"""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        timeout=_INSTALL_TIMEOUT_SECONDS,
        **kwargs,  # type: ignore[arg-type]
    )


def _detect_package_manager() -> str | None:
    """检测当前系统可用的包管理器。"""
    system = platform.system().lower()
    if system in ("linux", "linux2"):
        if shutil.which("apt"):
            return "apt"
        if shutil.which("apt-get"):
            return "apt"
        if shutil.which("dnf"):
            return "dnf"
        if shutil.which("yum"):
            return "yum"
        if shutil.which("pacman"):
            return "pacman"
        return None
    if system == "darwin":
        if shutil.which("brew"):
            return "brew"
        return None
    if system == "windows":
        if shutil.which("winget"):
            return "winget"
        if shutil.which("choco"):
            return "choco"
        return None
    return None


def _install_apt(packages: list[str], use_sudo: bool = True) -> bool:
    """通过 apt 安装包。"""
    sudo = ["sudo"] if use_sudo else []
    cmd = [*sudo, "apt-get", "install", "-y", *packages]
    logger.info("执行: %s", " ".join(cmd))
    result = _run(cmd)
    if result.returncode != 0:
        logger.error("apt 安装失败:\n%s\n%s", result.stdout, result.stderr)
        return False
    logger.info("apt 安装成功")
    return True


def _install_brew(packages: list[str]) -> bool:
    """通过 brew 安装包。"""
    cmd = ["brew", "install", *packages]
    logger.info("执行: %s", " ".join(cmd))
    result = _run(cmd)
    if result.returncode != 0:
        logger.error("brew 安装失败:\n%s\n%s", result.stdout, result.stderr)
        return False
    logger.info("brew 安装成功")
    return True


def _install_winget(packages: list[str]) -> bool:
    """通过 winget 安装包。"""
    cmd = [
        "winget",
        "install",
        "--silent",
        "--accept-source-agreements",
        "--accept-package-agreements",
        *packages,
    ]
    logger.info("执行: %s", " ".join(cmd))
    result = _run(cmd)
    if result.returncode != 0:
        logger.error("winget 安装失败:\n%s\n%s", result.stdout, result.stderr)
        return False
    logger.info("winget 安装成功")
    return True


def _install_choco(packages: list[str]) -> bool:
    """通过 choco 安装包。"""
    cmd = ["choco", "install", "-y", *packages]
    logger.info("执行: %s", " ".join(cmd))
    result = _run(cmd)
    if result.returncode != 0:
        logger.error("choco 安装失败:\n%s\n%s", result.stdout, result.stderr)
        return False
    logger.info("choco 安装成功")
    return True


def install_pandoc_dependencies(
    use_sudo: bool = True,
    extra_packages: list[str] | None = None,
) -> bool:
    """尝试在当前系统上自动安装 pandoc 及中文字体依赖。

    参数：
      - use_sudo: Linux 下是否使用 sudo（默认 True，容器内可设为 False）
      - extra_packages: 额外要安装的包名列表

    返回：
      - True: 安装成功或已安装
      - False: 安装失败

    注意：
      - 此函数会实际执行系统包管理器命令，请谨慎在生产环境使用。
      - Windows 上 winget/choco 安装后可能需要重启终端才能识别 pandoc。
    """
    # 已安装则跳过
    if shutil.which("pandoc"):
        logger.info("pandoc 已安装，跳过安装步骤")
        return True

    system = platform.system().lower()
    distro = platform.system().lower()

    # 尝试从 /etc/os-release 获取更精确的发行版信息
    if system in ("linux", "linux2"):
        try:
            with open("/etc/os-release") as f:
                content = f.read().lower()
            if "ubuntu" in content:
                distro = "ubuntu"
            elif "debian" in content:
                distro = "debian"
        except (OSError, FileNotFoundError):
            pass

    # 查找对应平台的包列表
    packages_by_pm = _PACKAGES.get(distro) or _PACKAGES.get(system, {})
    if not packages_by_pm:
        logger.error(
            "不支持自动安装 pandoc 的系统: %s。请手动安装 pandoc + texlive-xetex + 中文字体。",
            system,
        )
        return False

    # 检测可用的包管理器
    pm = _detect_package_manager()
    if not pm:
        logger.error(
            "未检测到支持的包管理器（apt/brew/winget/choco）。请手动安装 pandoc。"
        )
        return False

    # 获取该包管理器对应的包列表
    pkgs = packages_by_pm.get(pm, [])
    if not pkgs:
        logger.warning("系统 %s 的包管理器 %s 没有配置包列表", distro, pm)
        # 尝试通用包列表
        pkgs = packages_by_pm.get("apt", [])

    if extra_packages:
        pkgs = list(pkgs) + list(extra_packages)

    if not pkgs:
        logger.error("没有可安装的包列表")
        return False

    # 执行安装
    logger.info("检测到系统: %s，使用包管理器: %s", distro, pm)

    if pm == "apt":
        return _install_apt(pkgs, use_sudo=use_sudo)
    if pm == "brew":
        return _install_brew(pkgs)
    if pm == "winget":
        return _install_winget(pkgs)
    if pm == "choco":
        return _install_choco(pkgs)

    logger.error("不支持的包管理器: %s", pm)
    return False


def ensure_pandoc_available(
    raise_on_missing: bool = False,
    auto_install: bool = False,
    use_sudo: bool = True,
) -> str | None:
    """确保 pandoc 可用，返回 pandoc 路径或 None。

    参数：
      - raise_on_missing: 为 True 时找不到则抛 RuntimeError
      - auto_install: 为 True 时尝试自动安装（需有系统权限）
      - use_sudo: auto_install 时 Linux 是否用 sudo

    返回：
      - pandoc 可执行文件路径（字符串）
      - 或 None（当 raise_on_missing=False 且不可用时）

    示例：
        path = ensure_pandoc_available(raise_on_missing=True)
        # 或
        if not ensure_pandoc_available():
            install_pandoc_dependencies()
    """
    pandoc = shutil.which("pandoc")
    if pandoc:
        return pandoc

    logger.warning("pandoc 未安装")

    if auto_install:
        logger.info("尝试自动安装 pandoc 依赖...")
        success = install_pandoc_dependencies(use_sudo=use_sudo)
        if success:
            # 重新检测
            pandoc = shutil.which("pandoc")
            if pandoc:
                logger.info("pandoc 安装成功: %s", pandoc)
                return pandoc
            logger.warning("安装后仍未检测到 pandoc，可能需要重启终端")

    if raise_on_missing:
        raise RuntimeError(
            "pandoc 未安装，无法渲染 PDF。"
            " 可设置环境变量 PDF_RAISE_ON_MISSING=1 强制报错，"
            " 或调用 install_pandoc_dependencies() 自动安装。"
        )

    return None
