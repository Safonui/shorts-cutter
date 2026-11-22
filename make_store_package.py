# -*- coding: utf-8 -*-
"""Сборка пакета MSIX для Microsoft Store (подпись делает сама Microsoft).

Запуск:
    .venv\\Scripts\\python.exe make_store_package.py [--publisher "CN=..."]

Что происходит:
1. собирается папка приложения (`build_exe.py --onedir`) — так пакет содержит
   приложение, ffmpeg и всё остальное;
2. из NuGet-пакета Microsoft.Windows.SDK.BuildTools достаётся официальный
   `makeappx.exe` (он же строит карту блоков пакета — вручную её делать нельзя);
3. собирается `store/dist/<имя>_<версия>_x64.msix` вместе с манифестом и
   графикой из `assets/store`.

Идентификатор издателя (`Publisher`) должен совпадать с тем, что показан в
Partner Center → Account settings → Publisher. Пока он не задан, в манифесте
стоит `CN=Placeholder`, и пакет не примется в Store — но собрать и проверить
структуру можно.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

from appconfig import APP_NAME, APP_PUBLISHER, APP_VERSION

HERE = Path(__file__).resolve().parent
STORE = HERE / "store"
DIST = STORE / "dist"
WORK = STORE / "work"
TOOLS = STORE / "tools"
PACKAGE_NAME = "ShortsCutter"
PLACEHOLDER_PUBLISHER = "CN=Placeholder"
BUILD_TOOLS = "microsoft.windows.sdk.buildtools"


def makeappx_path() -> Path:
    """Найти (при необходимости — скачать) официальный makeappx.exe."""
    cached = list(TOOLS.glob("**/makeappx.exe"))
    if cached:
        return cached[0]

    print("Скачиваю makeappx.exe из NuGet (Microsoft.Windows.SDK.BuildTools)…")
    index = json.load(urllib.request.urlopen(urllib.request.Request(
        f"https://api.nuget.org/v3-flatcontainer/{BUILD_TOOLS}/index.json",
        headers={"User-Agent": "shorts-cutter/1.0"}), timeout=60))
    version = next(item for item in reversed(index["versions"]) if "-" not in item)
    url = (f"https://api.nuget.org/v3-flatcontainer/{BUILD_TOOLS}/{version}/"
           f"{BUILD_TOOLS}.{version}.nupkg")
    TOOLS.mkdir(parents=True, exist_ok=True)
    archive = TOOLS / "buildtools.nupkg"
    with urllib.request.urlopen(urllib.request.Request(
            url, headers={"User-Agent": "shorts-cutter/1.0"}), timeout=600) as response, \
            open(archive, "wb") as output:
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            output.write(chunk)
    with zipfile.ZipFile(archive) as package:
        for name in package.namelist():
            if name.endswith(("makeappx.exe", "makepri.exe")) and "/x64/" in name:
                target = TOOLS / Path(name).name
                target.write_bytes(package.read(name))
                print(f"  извлечено: {target.name}")
    archive.unlink(missing_ok=True)
    cached = list(TOOLS.glob("makeappx.exe"))
    if not cached:
        raise SystemExit("не удалось получить makeappx.exe")
    return cached[0]


def ensure_app_folder() -> Path:
    """Папка приложения для упаковки (собирается build_exe.py --onedir)."""
    folder = HERE / "dist" / APP_NAME
    if not (folder / f"{APP_NAME}.exe").exists():
        print("Собираю папку приложения (build_exe.py --onedir)…")
        result = subprocess.run([sys.executable, str(HERE / "build_exe.py"), "--onedir"],
                                cwd=HERE)
        if result.returncode != 0:
            raise SystemExit("сборка приложения не удалась")
    if not (folder / f"{APP_NAME}.exe").exists():
        raise SystemExit(f"не найден {folder / (APP_NAME + '.exe')}")
    return folder


def manifest_text(publisher: str) -> str:
    """AppxManifest.xml для полноценного настольного приложения в пакете."""
    numbers = APP_VERSION.split(".")
    while len(numbers) < 4:
        numbers.append("0")
    version = ".".join(numbers[:4])
    return f"""<?xml version="1.0" encoding="utf-8"?>
<Package
  xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
  xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
  xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
  IgnorableNamespaces="uap rescap">

  <Identity Name="{PACKAGE_NAME}" Publisher="{publisher}" Version="{version}"
            ProcessorArchitecture="x64" />

  <Properties>
    <DisplayName>{APP_NAME}</DisplayName>
    <PublisherDisplayName>{APP_PUBLISHER}</PublisherDisplayName>
    <Logo>Assets\\StoreLogo.png</Logo>
    <Description>Режет длинное видео на вертикальные клипы 9:16, вшивает субтитры
      и находит яркие моменты по звуку.</Description>
  </Properties>

  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.17763.0"
                        MaxVersionTested="10.0.22621.0" />
  </Dependencies>

  <Resources>
    <Resource Language="ru-RU" />
  </Resources>

  <Applications>
    <Application Id="App" Executable="app\\{APP_NAME}.exe"
                 EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements DisplayName="{APP_NAME}"
                          Description="Вертикальные клипы 9:16 с субтитрами"
                          BackgroundColor="#0A0A14"
                          Square150x150Logo="Assets\\Square150x150Logo.png"
                          Square44x44Logo="Assets\\Square44x44Logo.png">
        <uap:DefaultTile Wide310x150Logo="Assets\\Wide310x150Logo.png"
                         Square310x310Logo="Assets\\LargeTile.png"
                         Square71x71Logo="Assets\\SmallTile.png" />
        <uap:SplashScreen Image="Assets\\SplashScreen.png" BackgroundColor="#0A0A14" />
      </uap:VisualElements>
    </Application>
  </Applications>

  <Capabilities>
    <rescap:Capability Name="runFullTrust" />
  </Capabilities>
</Package>
"""


def build_package(publisher: str) -> int:
    app_folder = ensure_app_folder()
    makeappx = makeappx_path()

    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "Assets").mkdir(exist_ok=True)
    for asset in (HERE / "assets" / "store").glob("*.png"):
        shutil.copy2(asset, WORK / "Assets" / asset.name)
    shutil.copy2(HERE / "assets" / "icon.ico", WORK / "Assets" / "icon.ico")
    shutil.copytree(app_folder, WORK / "app")
    (WORK / "AppxManifest.xml").write_text(manifest_text(publisher), encoding="utf-8")

    DIST.mkdir(parents=True, exist_ok=True)
    numbers = APP_VERSION.split(".")
    while len(numbers) < 4:
        numbers.append("0")
    package = DIST / f"{PACKAGE_NAME}_{'.'.join(numbers[:4])}_x64.msix"
    package.unlink(missing_ok=True)

    print("Собираю пакет…")
    try:
        result = subprocess.run([str(makeappx), "pack", "/d", str(WORK), "/p", str(package),
                                 "/o"], capture_output=True, text=True, encoding="utf-8",
                                errors="replace")
    except OSError as exc:
        print(_tool_problem(exc))
        return 1
    if result.stdout.strip():
        print(result.stdout.strip()[-600:])
    if result.returncode != 0 or not package.exists():
        print(result.stderr.strip()[-600:])
        return 1

    # Проверка пакета тем же инструментом.
    try:
        check = subprocess.run([str(makeappx), "validate", "/p", str(package)],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace")
    except OSError as exc:
        print(_tool_problem(exc))
        return 1
    valid = check.returncode == 0
    print(f"Проверка пакета: {'пройдена' if valid else 'НЕ пройдена'}")
    if not valid:
        print((check.stdout + check.stderr).strip()[-500:])

    print(f"\nПакет: {package} ({package.stat().st_size / 1e6:.0f} МБ)")
    if publisher == PLACEHOLDER_PUBLISHER:
        print("ВНИМАНИЕ: издатель в манифесте — заглушка. Перед отправкой в Store\n"
              "укажите Publisher из Partner Center:\n"
              '  .venv\\Scripts\\python.exe make_store_package.py --publisher "CN=..."')
    print("Дальше: store/ИНСТРУКЦИЯ-ПУБЛИКАЦИИ.md")
    return 0


def _tool_problem(exc: OSError) -> str:
    """Понятное объяснение, если makeappx.exe не запускается."""
    text = str(exc)
    if "14001" in text or "side-by-side" in text or "параллельная конфигурация" in text:
        return (
            "makeappx.exe не запускается: на компьютере нарушена боковая конфигурация\n"
            "Visual C++ (ошибка 14001). Этот инструмент Microsoft собран под\n"
            "распространяемый пакет Visual C++ 2015–2022.\n\n"
            "Как исправить (любой способ):\n"
            "  1) winget install --id Microsoft.VCRedist.2015+.x64 --force\n"
            "     (или скачать «Microsoft Visual C++ Redistributable 2015-2022 (x64)»\n"
            "      с сайта Microsoft и установить);\n"
            "  2) либо собрать пакет на другом компьютере: скопировать проект и\n"
            "     запустить make_store_package.py там.\n\n"
            "Остальные части приложения от этого не зависят."
        )
    return f"Не удалось запустить makeappx.exe: {text}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Сборка MSIX для Microsoft Store")
    parser.add_argument("--publisher", default=PLACEHOLDER_PUBLISHER,
                        help='издатель из Partner Center, например "CN=Иван Иванов"')
    args = parser.parse_args()
    return build_package(args.publisher)


if __name__ == "__main__":
    sys.exit(main())
