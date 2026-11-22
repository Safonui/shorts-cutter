# -*- coding: utf-8 -*-
"""Загрузка проекта «Нарезка Shorts» на GitHub БЕЗ установленного git.

Зачем это нужно: на этом компьютере git не установлен, а обычные установщики
Windows может блокировать политикой управления приложениями. Этот скрипт
отправляет файлы напрямую в GitHub через его API — средствами стандартной
библиотеки Python, без git и без прав администратора.

Что делает:
    1. читает .gitignore и берёт только те файлы, которые попадают в репозиторий;
    2. заливает их в репозиторий одним коммитом (повторный запуск делает новый
       коммит с актуальным состоянием — файлы, удалённые локально, исчезнут);
    3. печатает, что дальше нажать, чтобы собрать .exe в облаке.

Подготовка (один раз):
    1. Создайте на github.com пустой репозиторий (без README и .gitignore).
    2. Создайте токен: Settings → Developer settings → Personal access tokens →
       Fine-grained tokens → Generate new token. Доступ: только этот репозиторий,
       право «Contents: Read and write». Скопируйте токен (он показывается один раз).

Запуск:
    $env:GITHUB_TOKEN = "вставьте_токен"      # так токен не попадает в историю команд
    py -3 загрузить_на_github.py --repo логин/имя-репозитория

Что уедет, можно посмотреть заранее, ничего не отправляя:
    py -3 загрузить_на_github.py --repo логин/имя --dry-run
"""

from __future__ import annotations

import argparse
import base64
import fnmatch
import json
import os
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = "https://api.github.com"
MAX_FILE_BYTES = 40 * 1024 * 1024        # больше GitHub не примет одним файлом
WORKERS = 8


# ── какие файлы берём ───────────────────────────────────────────────────────

def load_ignore_patterns(root: Path) -> list[tuple[str, bool, bool]]:
    """Разобрать .gitignore: (шаблон, это каталог, отрицание).

    Поддерживается то, что реально есть в нашем .gitignore: комментарии,
    каталоги с «/» на конце, шаблоны со «*» и «[» и отрицания через «!».
    """
    patterns: list[tuple[str, bool, bool]] = []
    path = root / ".gitignore"
    if not path.exists():
        return patterns
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        if negate:
            line = line[1:].strip()
        directory = line.endswith("/")
        line = line.strip("/")
        if line:
            patterns.append((line, directory, negate))
    return patterns


def is_ignored(rel: str, patterns: list[tuple[str, bool, bool]]) -> bool:
    """Правило git «последнее совпадение побеждает»."""
    parts = rel.split("/")
    ignored = False
    for name, directory, negate in patterns:
        if directory:
            # каталог совпал, если совпал любой его компонент или путь-префикс
            hit = any(fnmatch.fnmatch(part, name) for part in parts[:-1])
            hit = hit or any(fnmatch.fnmatch("/".join(parts[:i]), name)
                             for i in range(1, len(parts)))
        elif "/" in name:
            hit = fnmatch.fnmatch(rel, name)
        else:
            hit = fnmatch.fnmatch(parts[-1], name)
        if hit:
            ignored = not negate
    return ignored


def collect_files(root: Path) -> list[tuple[str, Path]]:
    """Список пар (путь в репозитории, файл на диске)."""
    patterns = load_ignore_patterns(root)
    found: list[tuple[str, Path]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel == ".git" or rel.startswith(".git/"):
            continue
        if is_ignored(rel, patterns):
            continue
        found.append((rel, path))
    return found


# ── обращения к GitHub API ──────────────────────────────────────────────────

class GithubError(Exception):
    """Ответ GitHub с кодом ошибки — разбираем его отдельно."""

    def __init__(self, code: int, message: str, accepted: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.accepted = accepted      # заголовок: какие права нужны для запроса


def request(token: str, method: str, path: str, payload: dict | None = None) -> dict:
    """Одно обращение к API. Ошибки — исключением GithubError."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        API + path, data=data, method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "narezka-shorts-uploader",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            message = json.loads(detail).get("message", detail)
        except json.JSONDecodeError:
            message = detail
        # GitHub сам сообщает, какое право нужно для этого запроса —
        # это избавляет от догадок при настройке токена.
        accepted = ""
        if exc.headers is not None:
            accepted = exc.headers.get("X-Accepted-GitHub-Permissions", "") or ""
        raise GithubError(exc.code, message, accepted) from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"\nНет связи с GitHub: {exc.reason}\n"
                         "Проверьте интернет или VPN и повторите.") from None


def explain(exc: GithubError) -> None:
    """Превратить ошибку GitHub в понятный совет и остановить работу."""
    if exc.code == 401:
        hint = "Токен неверный или истёк — создайте новый."
    elif exc.code == 404:
        hint = ("Репозитория нет или у токена нет к нему доступа. Проверьте "
                "написание «логин/репозиторий» и право Contents: Read and write.")
    elif exc.code == 403:
        hint = ("Похоже, у токена нет нужного права. Откройте "
                "https://github.com/settings/tokens?type=beta → свой токен → Edit "
                "и включите Contents: Read and write (а для файлов в "
                ".github/workflows — ещё и Workflows: Read and write).")
        if exc.accepted:
            hint = (f"GitHub требует для этого запроса: {exc.accepted}.\n" + hint)
    elif exc.code == 409:
        hint = ("GitHub считает репозиторий пустым. Скрипт создаёт первый коммит "
                "сам, через Contents API — если это сообщение появилось, значит "
                "служебный коммит создать не удалось.")
    else:
        hint = "Подробности в ответе GitHub выше."
    raise SystemExit(f"\nGitHub ответил {exc.code}: {exc.message}\n{hint}")


def api(token: str, method: str, path: str, payload: dict | None = None) -> dict:
    """Обращение, ошибка в котором означает конец работы."""
    try:
        return request(token, method, path, payload)
    except GithubError as exc:
        explain(exc)
        raise AssertionError("недостижимо") from None


def api_optional(token: str, method: str, path: str) -> dict | None:
    """Обращение, где «не найдено» и «пустой репозиторий» — ожидаемый ответ."""
    try:
        return request(token, method, path)
    except GithubError as exc:
        if exc.code in (404, 409, 422):
            return None
        explain(exc)
        return None


# ── основная работа ─────────────────────────────────────────────────────────

def upload_blob(token: str, repo: str, item: tuple[str, Path]) -> dict:
    rel, path = item
    content = base64.b64encode(path.read_bytes()).decode("ascii")
    sha = api(token, "POST", f"/repos/{repo}/git/blobs",
              {"content": content, "encoding": "base64"})["sha"]
    print(f"    загружено: {rel}")
    return {"path": rel, "mode": "100644", "type": "blob", "sha": sha}


def accessible_repos(token: str) -> set[str] | None:
    """Какие репозитории видит этот токен. None — если выяснить не удалось.

    Нужно для проверки до загрузки: если репозитория нет в списке доступа
    токена, запись провалится с невнятным 403. Отдельная тонкость: чтение
    публичного репозитория доступно и без прав, поэтому «репозиторий
    открывается» ещё ничего не значит.
    """
    names: set[str] = set()
    try:
        for page in range(1, 4):
            batch = request(token, "GET", f"/user/repos?per_page=100&page={page}")
            if not batch:
                return names
            names |= {repo["full_name"].lower() for repo in batch}
            if len(batch) < 100:
                return names
    except GithubError:
        return None
    return None                      # список мог быть обрезан — выводов не делаем


def clean_token(raw: str) -> str:
    """Убрать кавычки и пробелы; проверить, что символы латинские.

    Токен уходит в HTTP-заголовок, а заголовки допускают только латиницу —
    без этой проверки скопированный с лишним символом токен валился бы
    непонятной ошибкой кодировки.
    """
    token = raw.strip().strip('"').strip("'").strip()
    if token and not token.isascii():
        raise SystemExit(
            "В токене есть символы не из латиницы — похоже, скопировался лишний "
            "текст или кавычки. Скопируйте токен с GitHub ещё раз целиком.")
    return token


def seed_empty_repo(token: str, repo: str, branch: str, default_branch: str) -> None:
    """Создать первый коммит в пустом репозитории.

    Особенность GitHub: пока в репозитории нет ни одного коммита, весь Git Data
    API отвечает «409 Git Repository is empty» — включая создание blob, то есть
    залить файлы обычным путём невозможно. Поэтому первый коммит делаем через
    Contents API: он на пустом репозитории работает и заодно создаёт ветку.
    Служебный файл .gitkeep исчезнет в следующем коммите: дерево присылается
    целиком, без него.
    """
    payload: dict = {
        "message": "Подготовка репозитория (служебный коммит)",
        "content": base64.b64encode(b"seed\n").decode("ascii"),
    }
    if branch != default_branch:
        # Для ветки с другим именем её нужно указать явно; основную GitHub
        # создаст сам, если параметр branch не передавать.
        payload["branch"] = branch
    result = api(token, "PUT", f"/repos/{repo}/contents/.gitkeep", payload)
    print(f"  репозиторий пуст — создан служебный первый коммит "
          f"{result['commit']['sha'][:10]}")


def push(token: str, repo: str, branch: str, message: str, files: list,
         default_branch: str = "") -> str:
    # Сначала выясняем состояние ветки: на пустом репозитории ни blob, ни дерево
    # создать нельзя, поэтому при необходимости заводим первый коммит.
    ref = api_optional(token, "GET", f"/repos/{repo}/git/ref/heads/{branch}")
    if not (ref and ref.get("object", {}).get("sha")):
        seed_empty_repo(token, repo, branch, default_branch or branch)
        ref = api_optional(token, "GET", f"/repos/{repo}/git/ref/heads/{branch}")

    parents: list[str] = []
    if ref and ref.get("object", {}).get("sha"):
        parents = [ref["object"]["sha"]]
        print(f"  продолжаю ветку {branch}, предыдущий коммит {parents[0][:10]}")

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        tree = list(pool.map(lambda item: upload_blob(token, repo, item), files))

    tree_sha = api(token, "POST", f"/repos/{repo}/git/trees", {"tree": tree})["sha"]

    payload: dict = {"message": message, "tree": tree_sha}
    if parents:
        payload["parents"] = parents
    commit_sha = api(token, "POST", f"/repos/{repo}/git/commits", payload)["sha"]

    if parents:
        api(token, "PATCH", f"/repos/{repo}/git/refs/heads/{branch}",
            {"sha": commit_sha, "force": False})
    else:
        api(token, "POST", f"/repos/{repo}/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": commit_sha})
    return commit_sha


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Загрузить проект на GitHub без git (через API)")
    parser.add_argument("--repo", required=True, help="логин/имя-репозитория")
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""),
                        help="токен GitHub; по умолчанию берётся из GITHUB_TOKEN")
    parser.add_argument("--branch", default="", help="ветка (по умолчанию — основная в репозитории)")
    parser.add_argument("--message", default="Нарезка Shorts: загрузка проекта",
                        help="текст коммита")
    parser.add_argument("--dry-run", action="store_true",
                        help="только показать, что будет загружено")
    args = parser.parse_args()
    args.token = clean_token(args.token)

    files = collect_files(HERE)
    total = sum(path.stat().st_size for _, path in files)
    print(f"Файлов к загрузке: {len(files)} ({total / 1e6:.2f} МБ)")
    for rel, path in files:
        print(f"    {path.stat().st_size:>10,} байт  {rel}".replace(",", " "))

    too_big = [(rel, path.stat().st_size) for rel, path in files if path.stat().st_size > MAX_FILE_BYTES]
    if too_big:
        for rel, size in too_big:
            print(f"\nСлишком большой файл: {rel} ({size / 1e6:.0f} МБ) — "
                  "добавьте его в .gitignore или в Exclude.", file=sys.stderr)
        return 1

    if args.dry_run:
        print("\nЭто только примерка (--dry-run): на GitHub ничего не отправлено.")
        return 0

    if not args.token:
        print("\nНет токена. Сделайте так:\n"
              '    $env:GITHUB_TOKEN = "ваш_токен"\n'
              "    py -3 загрузить_на_github.py --repo логин/имя-репозитория",
              file=sys.stderr)
        return 2

    info = api(args.token, "GET", f"/repos/{args.repo}")
    branch = args.branch or info.get("default_branch") or "main"
    print(f"\nРепозиторий: {info.get('full_name')} → ветка {branch}")

    # Проверка до загрузки: у публичного репозитория чтение разрешено всем,
    # поэтому «открывается» ничего не значит — важно, выдан ли токену доступ.
    allowed = accessible_repos(args.token)
    if allowed is not None and args.repo.lower() not in allowed:
        print(f"\nТокену не выдан доступ к {args.repo} — запись будет отклонена.")
        print("Доступные этому токену репозитории: "
              + (", ".join(sorted(allowed)) if allowed else "ни одного"))
        print("\nКак починить (30 секунд):")
        print("  1. https://github.com/settings/tokens?type=beta → свой токен → Edit")
        print(f"  2. Repository access → Only select repositories → добавьте {args.repo}")
        print("  3. Repository permissions → Contents: Read and write,")
        print("     а если токен трогает .github/workflows — ещё и Workflows: Read and write")
        print("  4. Save changes и повторите запуск")
        return 3

    commit = push(args.token, args.repo, branch, args.message, files,
                  default_branch=info.get("default_branch") or "")
    print(f"\nГотово. Коммит {commit[:10]} в {args.repo} ({branch}).")
    print("\nДальше:")
    print("  Сборка уже запустилась сама — первый коммит в основную ветку её включает.")
    print(f"  1. Открыть https://github.com/{args.repo}/actions")
    print("  2. Если нужно запустить вручную: «Сборка .exe для Windows» → Run workflow.")
    print("  3. Через 5–10 минут скачать .exe внизу страницы запуска (Artifacts).")
    print("  4. Нужна ссылка для раздачи: Releases → Draft a new release → тег v1.0.0 →")
    print("     Publish — workflow сам приложит .exe к релизу.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
